from pathlib import Path
import hashlib
import json
import os
import re
import stat
import subprocess
import tempfile
import unittest
import zipfile

import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/repository.yml"
DISPATCH = ROOT / ".github/workflows/central-ci-dispatch.yml"
INVENTORY = ROOT / "INVENTORY.yaml"
CONTRACT = ROOT / "contracts/repository-ci-v1.json"


class RepositoryWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
        cls.dispatch = yaml.safe_load(DISPATCH.read_text(encoding="utf-8"))
        cls.workflow_text = WORKFLOW.read_text(encoding="utf-8")
        cls.dispatch_text = DISPATCH.read_text(encoding="utf-8")
        cls.contract = json.loads(CONTRACT.read_text(encoding="utf-8"))

    @property
    def steps_by_name(self):
        return {
            step.get("name"): step
            for step in self.workflow["jobs"]["execute"]["steps"]
            if step.get("name")
        }

    def bind_artifact_selection_digest(self, env: dict[str, str], github_output: Path) -> str:
        digests = [
            line.split("=", 1)[1]
            for line in github_output.read_text(encoding="utf-8").splitlines()
            if line.startswith("artifact_selection_sha256=")
        ]
        self.assertEqual(len(digests), 1)
        self.assertRegex(digests[0], r"^[0-9a-f]{64}$")
        env["ARTIFACT_SELECTION_SHA256"] = digests[0]
        return digests[0]

    def run_repository_request(
        self,
        *,
        operation: str = "build",
        host_os: str = "linux",
        semantic=None,
        release_authorized: str = "false",
        source_is_tag: str = "false",
        caller_repository: str = "StreamScapeTV/ci-workflows",
        caller_ref: str = "refs/heads/main",
        lifecycle_ci_run_id: str = "",
        trusted_capability_ci_run_id: str = "",
    ):
        script = self.steps_by_name["Validate bounded repository operation"]["run"]
        raw = semantic if isinstance(semantic, str) else json.dumps(semantic or {})
        with tempfile.TemporaryDirectory() as td:
            output = Path(td) / "github-output"
            output.write_text("", encoding="utf-8")
            env = {
                **os.environ,
                "OPERATION": operation,
                "HOST_OS": host_os,
                "RAW_SEMANTIC_INPUTS": raw,
                "RELEASE_AUTHORIZED": release_authorized,
                "SOURCE_IS_TAG": source_is_tag,
                "LIFECYCLE_CI_RUN_ID": lifecycle_ci_run_id,
                "TRUSTED_CAPABILITY_CI_RUN_ID": trusted_capability_ci_run_id,
                "CALLER_REPOSITORY": caller_repository,
                "CALLER_REF": caller_ref,
                "CONTRACT": str(CONTRACT),
                "GITHUB_OUTPUT": str(output),
            }
            result = subprocess.run(
                ["bash", "-c", script],
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )
            return result, output.read_text(encoding="utf-8")

    def run_dispatch_request(self, profile: str, inputs: dict):
        steps = self.dispatch["jobs"]["request"]["steps"]
        script = next(
            step["run"]
            for step in steps
            if step.get("name") == "Validate generic repository-owned CI request"
        )
        return subprocess.run(
            ["bash", "-c", script],
            cwd=ROOT,
            env={
                **os.environ,
                "TEST_PROFILE": profile,
                "INPUTS_JSON": json.dumps(inputs),
            },
            text=True,
            capture_output=True,
            check=False,
        )

    def run_capability_resolver(
        self,
        *,
        run_repository: str = "ExampleOrg/apple-application",
        source_repository: str | None = None,
        run_ref: str = "feature",
        source_ref: str | None = None,
        run_is_tag: bool = False,
        source_is_tag: str | None = None,
        operation: str = "build",
        host_os: str = "linux",
        run_profile: str | None = None,
        run_workflow: str = "validation.repository",
        project_state: dict | None = None,
        ci_run_id: str = "11111111-1111-4111-8111-111111111111",
        trusted_capability_ci_run_id: str = "",
    ):
        script = self.steps_by_name[
            "Resolve trusted private infrastructure capabilities"
        ]["run"]
        source_repository = source_repository or run_repository
        source_ref = source_ref or run_ref
        source_is_tag = source_is_tag or ("true" if run_is_tag else "false")
        run_profile = run_profile or operation
        if project_state is None:
            project_state = {
                "repository_ci": {
                    "schemaVersion": 1,
                    "repository": run_repository,
                    "capabilities": ["private_network", "github_git"],
                }
            }

        claim_response = {
            "ok": True,
            "code": "ok",
            "replayed": True,
            "run": {
                "project_key": "private-project",
                "repository": run_repository,
                "ref": run_ref,
                "is_tag": run_is_tag,
                "workflow_key": run_workflow,
                "test_profile": run_profile,
            },
        }
        project_response = {
            "project_key": "private-project",
            "state": project_state,
            "review_policy": "independent_required",
            "review_policy_version": 1,
        }

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            fake_bin = root / "bin"
            fake_bin.mkdir()
            curl = fake_bin / "curl"
            curl.write_text(
                "#!/usr/bin/env python3\n"
                "import json\n"
                "import sys\n"
                f"claim = {claim_response!r}\n"
                f"project = {project_response!r}\n"
                "url = sys.argv[-1]\n"
                "if url.endswith('/claim_ci_run'):\n"
                "    print(json.dumps(claim))\n"
                "elif url.endswith('/get_project_state'):\n"
                "    print(json.dumps(project))\n"
                "else:\n"
                "    raise SystemExit(97)\n",
                encoding="utf-8",
            )
            curl.chmod(0o755)
            output = root / "github-output"
            output.write_text("", encoding="utf-8")
            result = subprocess.run(
                ["bash", "-c", script],
                cwd=ROOT,
                env={
                    **os.environ,
                    "PATH": f"{fake_bin}:{os.environ['PATH']}",
                    "LIFECYCLE_CI_RUN_ID": ci_run_id,
                    "TRUSTED_CAPABILITY_CI_RUN_ID": trusted_capability_ci_run_id,
                    "SOURCE_REPOSITORY": source_repository,
                    "SOURCE_REF": source_ref,
                    "SOURCE_IS_TAG": source_is_tag,
                    "OPERATION": operation,
                    "HOST_OS": host_os,
                    "CONTRACT": str(CONTRACT),
                    "AGENT_STATE_SUPABASE_URL": "https://agent-state.invalid",
                    "AGENT_STATE_SUPABASE_SECRET_KEY": "fixture-secret",
                    "GITHUB_OUTPUT": str(output),
                },
                text=True,
                capture_output=True,
                check=False,
            )
            values = {}
            for line in output.read_text(encoding="utf-8").splitlines():
                key, value = line.split("=", 1)
                values[key] = value
            return result, values

    def test_reusable_api_is_host_os_plus_structured_semantics_only(self) -> None:
        inputs = self.workflow["on"]["workflow_call"]["inputs"]
        self.assertEqual(
            set(inputs),
            {
                "repository",
                "ref",
                "source_is_tag",
                "expected_source_sha",
                "trusted_capability_ci_run_id",
                "operation",
                "host_os",
                "semantic_inputs_json",
                "release_authorized",
                "ci_run_id",
                "upload_private_log",
            },
        )
        for forbidden in (
            "platform",
            "ui_suite",
            "build_number",
            "release_kind",
            "command",
            "script",
            "script_path",
            "runner",
            "runner_label",
            "timeout",
            "environment",
            "env",
            "secret_name",
            "workspace",
            "scheme",
            "gradle_task",
            "xcodebuild_args",
            "sdk_package",
            "java_version",
            "android_api",
            "emulator_image",
        ):
            self.assertNotIn(forbidden, inputs)

        execute = self.workflow["jobs"]["execute"]
        self.assertEqual(execute["needs"], "resolve_host")
        self.assertEqual(
            execute["runs-on"],
            "${{ fromJSON(needs.resolve_host.outputs.runs_on) }}",
        )
        self.assertEqual(execute["timeout-minutes"], 300)

    def test_contract_documents_fixed_entrypoints_hosts_and_versioned_environment(self) -> None:
        self.assertEqual(self.contract["schemaVersion"], 1)
        adoption = self.contract["adoption"]
        self.assertEqual(
            adoption["fixedEntrypoints"],
            {
                "build": ".ci/build.sh",
                "test": ".ci/test.sh",
                "full": ".ci/test-full.sh",
                "ui-test": ".ci/test-ui.sh",
                "release": ".ci/release.sh",
            },
        )
        self.assertEqual(
            adoption["hostExecution"],
            {
                "linux": {"defaultHostClass": "linux-hosted"},
                "macos": {"defaultHostClass": "macos-hosted"},
            },
        )
        self.assertEqual(
            list(adoption["hostClasses"]),
            ["linux-hosted", "macos-hosted", "macos-high-capacity"],
        )
        self.assertIn("CI_HOST_OS", adoption["scriptEnvironment"])
        self.assertIn("CI_HOST_CLASS", adoption["scriptEnvironment"])
        self.assertIn("CI_OPERATION", adoption["scriptEnvironment"])
        self.assertIn("CI_INPUTS_JSON", adoption["scriptEnvironment"])
        self.assertGreaterEqual(len(adoption["adoptionSteps"]), 5)
        self.assertIn("raw_argv", adoption["forbiddenCallerSurface"])
        self.assertIn("runner_label", adoption["forbiddenCallerSurface"])
        self.assertIn(
            "trusted private Agent State configuration",
            adoption["adoptionSteps"][-1],
        )
        self.assertEqual(
            self.contract["migrationLedger"]["source"],
            "Agent State ci-workflows project configuration",
        )
        self.assertEqual(
            self.contract["migrationLedger"]["projectStateKey"],
            "repository_ci_migration_v1",
        )

    def test_fixed_entrypoints_are_the_only_executable_mapping(self) -> None:
        request = self.steps_by_name["Validate bounded repository operation"]["run"]
        for mapping in (
            "build) entrypoint=.ci/build.sh",
            "test) entrypoint=.ci/test.sh",
            "full) entrypoint=.ci/test-full.sh",
            "ui-test) entrypoint=.ci/test-ui.sh",
            "entrypoint=.ci/release.sh",
        ):
            self.assertIn(mapping, request)

        execute = self.steps_by_name["Execute fixed repository-owned entrypoint"]["run"]
        self.assertIn('"./${ENTRYPOINT}"', execute)
        self.assertNotIn("args=(", execute)
        self.assertNotIn("${args[@]}", execute)
        self.assertNotIn("--platform", execute)
        self.assertIn('export CI_HOST_OS="${HOST_OS}"', execute)
        self.assertIn('export CI_HOST_CLASS="${HOST_CLASS}"', execute)
        self.assertIn('export CI_OPERATION="${OPERATION}"', execute)
        self.assertIn('export CI_INPUTS_JSON="${NORMALIZED_INPUTS}"', execute)
        for product_command in (
            "xcodebuild",
            "gradlew",
            "./gradlew",
            "pytest",
            "mvn ",
            "swift test",
            ".xcworkspace",
            ".xcodeproj",
        ):
            self.assertNotIn(product_command, execute)

    def test_repository_request_validator_normalizes_bounded_semantics(self) -> None:
        valid, output = self.run_repository_request(
            operation="test",
            host_os="macos",
            semantic={
                "product_target": "ios",
                "test_selectors": ["Suite/Test", "Suite.Other/test_case"],
            },
        )
        self.assertEqual(valid.returncode, 0, valid.stderr)
        self.assertIn("entrypoint=.ci/test.sh\n", output)
        normalized_line = next(
            line for line in output.splitlines() if line.startswith("semantic_inputs_json=")
        )
        normalized = json.loads(normalized_line.split("=", 1)[1])
        self.assertEqual(normalized["schemaVersion"], 1)
        self.assertEqual(normalized["inputs"]["product_target"], "ios")
        self.assertEqual(
            normalized["inputs"]["test_selectors"],
            ["Suite/Test", "Suite.Other/test_case"],
        )

        ui, _ = self.run_repository_request(
            operation="ui-test",
            host_os="macos",
            semantic={"product_target": "tvos", "ui_mode": "full-ordinary"},
        )
        self.assertEqual(ui.returncode, 0, ui.stderr)

        for semantic, message in (
            ({"unknown": "value"}, "unknown field"),
            ({"product_target": "../escape"}, "outside the reviewed bound"),
            ({"test_selectors": ["-only-testing:Injected"]}, "contains a leading-dash item"),
        ):
            result, _ = self.run_repository_request(
                operation="build" if "test_selectors" not in semantic else "test",
                semantic=semantic,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(message, result.stderr)

        duplicate, _ = self.run_repository_request(
            semantic='{"product_target":"ios","product_target":"tvos"}'
        )
        self.assertNotEqual(duplicate.returncode, 0)
        self.assertIn("unambiguous JSON object", duplicate.stderr)

        oversized, _ = self.run_repository_request(semantic="x" * 2049)
        self.assertNotEqual(oversized.returncode, 0)
        self.assertIn("exceeds the reviewed bound", oversized.stderr)

        invalid_host, _ = self.run_repository_request(host_os="ios")
        self.assertNotEqual(invalid_host.returncode, 0)
        self.assertIn("host_os must be linux or macos", invalid_host.stderr)

    def test_release_contract_remains_separately_authorized_and_tag_bound(self) -> None:
        denied, _ = self.run_repository_request(
            operation="release",
            host_os="macos",
            semantic={"release_kind": "publish", "build_identity": "20260919.1"},
        )
        self.assertNotEqual(denied.returncode, 0)
        self.assertIn("separately authorized Central caller", denied.stderr)

        allowed, output = self.run_repository_request(
            operation="release",
            host_os="macos",
            semantic={"release_kind": "publish", "build_identity": "20260919.1"},
            release_authorized="true",
            source_is_tag="true",
        )
        self.assertEqual(allowed.returncode, 0, allowed.stderr)
        self.assertIn("entrypoint=.ci/release.sh", output)

        missing, _ = self.run_repository_request(
            operation="release",
            host_os="macos",
            semantic={"release_kind": "publish"},
            release_authorized="true",
            source_is_tag="true",
        )
        self.assertNotEqual(missing.returncode, 0)
        self.assertIn("omit a required field", missing.stderr)

    def test_dispatch_validates_scalar_transport_and_nested_semantic_json(self) -> None:
        valid = self.run_dispatch_request(
            "test",
            {
                "host_os": "linux",
                "semantic_inputs": json.dumps(
                    {
                        "product_target": "jvm",
                        "test_selectors": ["pkg.Test/test_case"],
                    }
                ),
            },
        )
        self.assertEqual(valid.returncode, 0, valid.stderr)

        no_semantics = self.run_dispatch_request("full", {"host_os": "macos"})
        self.assertEqual(no_semantics.returncode, 0, no_semantics.stderr)

        provider_operation_id = "provider_" + ("a" * 32)
        replay_cases = (
            (
                "build",
                {
                    "host_os": "linux",
                    "semantic_inputs": json.dumps({"product_target": "jvm"}),
                    "provider_operation_id": provider_operation_id,
                },
            ),
            (
                "test",
                {
                    "host_os": "linux",
                    "semantic_inputs": json.dumps(
                        {"test_selectors": ["pkg.Test/test_case"]}
                    ),
                    "provider_operation_id": provider_operation_id,
                },
            ),
            (
                "full",
                {
                    "host_os": "macos",
                    "provider_operation_id": provider_operation_id,
                },
            ),
            (
                "ui-test",
                {
                    "host_os": "macos",
                    "semantic_inputs": json.dumps({"ui_mode": "smoke"}),
                    "provider_operation_id": provider_operation_id,
                },
            ),
        )
        for profile, inputs in replay_cases:
            with self.subTest(profile=profile):
                replay_metadata = self.run_dispatch_request(profile, inputs)
                self.assertEqual(
                    replay_metadata.returncode,
                    0,
                    replay_metadata.stderr,
                )

        duplicate = self.run_dispatch_request(
            "build",
            {
                "host_os": "linux",
                "semantic_inputs": '{"product_target":"linux","product_target":"macos"}',
            },
        )
        self.assertNotEqual(duplicate.returncode, 0)
        self.assertIn("unambiguous JSON object", duplicate.stderr)

        cases = (
            ({"platform": "linux"}, "accepts only host_os and semantic_inputs"),
            (
                {
                    "host_os": "linux",
                    "provider_operation_id": "provider_" + ("A" * 32),
                },
                "provider_operation_id must match provider_<32 lowercase hex>",
            ),
            (
                {
                    "host_os": "linux",
                    "provider_operation_id": "provider_" + ("a" * 32),
                    "trace_id": "not-a-reviewed-metadata-field",
                },
                "accepts only host_os and semantic_inputs",
            ),
            ({"host_os": "ios"}, "host_os must be linux or macos"),
            (
                {"host_os": "linux", "semantic_inputs": json.dumps({"unknown": "x"})},
                "unknown field",
            ),
            (
                {"host_os": "linux", "semantic_inputs": "x" * 2049},
                "bounded JSON string",
            ),
            (
                {
                    "host_os": "linux",
                    "semantic_inputs": json.dumps(
                        {"test_selectors": ["-danger"]}
                    ),
                },
                "invalid selector",
            ),
        )
        for inputs, message in cases:
            result = self.run_dispatch_request("test", inputs)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(message, result.stderr)

    def test_agent_state_repository_route_passes_only_host_and_json_to_reusable_workflow(self) -> None:
        request_steps = self.dispatch["jobs"]["request"]["steps"]
        by_name = {step.get("name"): step for step in request_steps if step.get("name")}
        admission = by_name["Validate generic repository-owned CI request"]
        self.assertEqual(
            admission["if"],
            "${{ steps.claim.outputs.workflow_key == 'validation.repository' }}",
        )
        self.assertIn(
            'profile not in {"build", "test", "full", "ui-test"}',
            admission["run"],
        )
        job = self.dispatch["jobs"]["repository"]
        self.assertEqual(
            job["if"],
            "${{ needs.request.outputs.workflow_key == 'validation.repository' }}",
        )
        self.assertEqual(job["uses"], "./.github/workflows/repository.yml")
        self.assertEqual(
            set(job["with"]),
            {
                "repository",
                "ref",
                "source_is_tag",
                "operation",
                "host_os",
                "semantic_inputs_json",
                "ci_run_id",
            },
        )
        self.assertNotIn("release_authorized", job["with"])
        self.assertNotIn("provider_operation_id", job["with"])
        self.assertEqual(
            job["with"]["semantic_inputs_json"],
            "${{ fromJSON(needs.request.outputs.inputs_json).semantic_inputs || '{}' }}",
        )
        self.assertIn('inputs.pop("provider_operation_id", None)', admission["run"])
        self.assertNotIn("provider_operation_id", str(job["with"]))
        self.assertNotIn("release", admission["run"].split("PY_VALIDATE_REPOSITORY", 1)[0])

    def test_source_ref_namespace_is_exact_for_same_named_branch_and_tag(self) -> None:
        resolver = self.steps_by_name["Resolve exact repository source ref"]
        checkout = self.steps_by_name["Check out exact repository source"]
        self.assertEqual(
            checkout["with"]["ref"],
            "${{ steps.source_ref.outputs.full_ref }}",
        )
        script = resolver["run"]
        self.assertIn("false) namespace=refs/heads", script)
        self.assertIn("true) namespace=refs/tags", script)
        self.assertIn('git check-ref-format "${full_ref}"', script)

        def resolve(is_tag: str, ref: str):
            with tempfile.TemporaryDirectory() as td:
                output = Path(td) / "github-output"
                output.write_text("", encoding="utf-8")
                result = subprocess.run(
                    ["bash", "-c", script],
                    cwd=ROOT,
                    env={
                        **os.environ,
                        "REQUESTED_REF": ref,
                        "REQUESTED_IS_TAG": is_tag,
                        "GITHUB_OUTPUT": str(output),
                    },
                    text=True,
                    capture_output=True,
                    check=False,
                )
                return result, output.read_text(encoding="utf-8")

        branch, branch_output = resolve("false", "same-name")
        tag, tag_output = resolve("true", "same-name")
        self.assertEqual(branch.returncode, 0, branch.stderr)
        self.assertEqual(tag.returncode, 0, tag.stderr)
        self.assertEqual(branch_output, "full_ref=refs/heads/same-name\n")
        self.assertEqual(tag_output, "full_ref=refs/tags/same-name\n")
        self.assertNotEqual(branch_output, tag_output)

        invalid, _ = resolve("false", "bad..ref")
        self.assertNotEqual(invalid.returncode, 0)

    def test_entrypoint_is_tracked_regular_executable_and_cannot_escape_checkout(self) -> None:
        script = self.steps_by_name[
            "Verify exact source and fixed tracked entrypoint"
        ]["run"]
        self.assertIn("git -C source ls-files --error-unmatch", script)
        self.assertIn('test "${mode}" = 100755', script)
        self.assertIn(
            'test -f "${path}" && test ! -L "${path}" && test -x "${path}"',
            script,
        )
        self.assertIn("Path(sys.argv[1]).resolve(strict=True)", script)
        self.assertIn("os.path.commonpath", script)
        self.assertIn("fixed repository entrypoint escapes the exact checkout", script)
        self.assertIn(
            'test -z "$(git -C source status --porcelain --untracked-files=all)"',
            script,
        )

    def test_generic_executor_contains_no_public_consumer_authorization_or_toolchain_pins(self) -> None:
        contract_text = CONTRACT.read_text(encoding="utf-8")
        resolver = self.steps_by_name[
            "Resolve trusted private infrastructure capabilities"
        ]["run"]

        self.assertNotIn("authorization", self.contract)
        public_surfaces = (
            self.workflow_text,
            CONTRACT.read_text(encoding="utf-8"),
            Path(__file__).read_text(encoding="utf-8"),
        )
        for public_text in public_surfaces:
            repository_refs = set(
                re.findall(r"StreamScapeTV/[A-Za-z0-9_.-]+", public_text)
            )
            self.assertLessEqual(repository_refs, {"StreamScapeTV/ci-workflows"})
        self.assertNotRegex(
            contract_text,
            r"StreamScapeTV/[A-Za-z0-9_.-]+",
        )
        self.assertEqual(
            self.contract["trustedCapabilityGrant"]["source"],
            "Agent State project configuration",
        )
        self.assertEqual(
            self.contract["trustedCapabilityGrant"]["projectStateKey"],
            "repository_ci",
        )
        self.assertEqual(
            self.contract["trustedCapabilityGrant"]["defaultCapabilities"],
            [],
        )
        self.assertTrue(
            self.contract["trustedCapabilityGrant"]["failClosed"],
        )
        self.assertFalse(
            self.contract["migrationLedger"]["publicContractContainsConcreteConsumers"]
        )

        for generic_class in (
            "apple-application",
            "apple-library-package",
            "android-application",
            "android-library-package",
            "linux-service-backend",
            "generic-package-consumer",
        ):
            self.assertIn(generic_class, self.contract["consumerClasses"])

        self.assertIn("claim_ci_run", resolver)
        self.assertIn("get_project_state", resolver)
        self.assertIn('state.get("repository_ci")', resolver)
        self.assertIn("not bound to the exact Agent State request", resolver)
        self.assertIn("bound to a different repository", resolver)
        self.assertNotIn('case "${SOURCE_REPOSITORY}"', resolver)
        self.assertNotIn('get("repositories")', resolver)

        for forbidden in (
            "android-api37",
            "platforms;android-37",
            "build-tools;37.0.0",
            "system-images;android-36",
            "actions/setup-java",
            "CIW_MAVEN_PACKAGE_READ_TOKEN",
        ):
            self.assertNotIn(forbidden, self.workflow_text)

    def test_private_agent_state_capability_grant_is_exactly_bound_and_fail_closed(self) -> None:
        self.assertEqual(
            set(self.contract["capabilityTypes"]),
            {"private_network", "github_git", "registry_netrc", "gradle_maven", "registry_oci_publish"},
        )

        no_run, no_run_values = self.run_capability_resolver(ci_run_id="")
        self.assertEqual(no_run.returncode, 0, no_run.stderr)
        self.assertEqual(no_run_values["auth_enabled"], "false")
        for capability in self.contract["capabilityTypes"]:
            self.assertEqual(no_run_values[capability], "false")

        trusted, trusted_values = self.run_capability_resolver(
            project_state={
                "repository_ci": {
                    "schemaVersion": 1,
                    "repository": "ExampleOrg/apple-application",
                    "capabilities": [
                        "private_network",
                        "github_git",
                        "registry_netrc",
                    ],
                }
            }
        )
        self.assertEqual(trusted.returncode, 0, trusted.stderr)
        self.assertEqual(trusted_values["private_network"], "true")
        self.assertEqual(trusted_values["github_git"], "true")
        self.assertEqual(trusted_values["registry_netrc"], "true")
        self.assertEqual(trusted_values["gradle_maven"], "false")
        self.assertEqual(trusted_values["registry_oci_publish"], "false")
        self.assertEqual(trusted_values["auth_enabled"], "true")

        v2, v2_values = self.run_capability_resolver(
            project_state={
                "repository_ci": {
                    "schemaVersion": 2,
                    "repository": "ExampleOrg/apple-application",
                    "capabilities": ["private_network", "gradle_maven"],
                    "hostPolicy": {
                        "default": "macos-hosted",
                        "operations": {"full": "macos-high-capacity"},
                    },
                }
            }
        )
        self.assertEqual(v2.returncode, 0, v2.stderr)
        self.assertEqual(v2_values["private_network"], "true")
        self.assertEqual(v2_values["gradle_maven"], "true")
        self.assertEqual(v2_values["registry_oci_publish"], "false")
        self.assertEqual(v2_values["auth_enabled"], "true")

        absent, absent_values = self.run_capability_resolver(project_state={})
        self.assertEqual(absent.returncode, 0, absent.stderr)
        self.assertEqual(absent_values["auth_enabled"], "false")
        for capability in self.contract["capabilityTypes"]:
            self.assertEqual(absent_values[capability], "false")

        mismatched_run, _ = self.run_capability_resolver(
            source_repository="ExampleOrg/other-application"
        )
        self.assertNotEqual(mismatched_run.returncode, 0)
        self.assertIn(
            "not bound to the exact Agent State request",
            mismatched_run.stderr,
        )

        mismatched_grant, _ = self.run_capability_resolver(
            project_state={
                "repository_ci": {
                    "schemaVersion": 1,
                    "repository": "ExampleOrg/other-application",
                    "capabilities": ["private_network"],
                }
            }
        )
        self.assertNotEqual(mismatched_grant.returncode, 0)
        self.assertIn("bound to a different repository", mismatched_grant.stderr)

        unknown_capability, _ = self.run_capability_resolver(
            project_state={
                "repository_ci": {
                    "schemaVersion": 1,
                    "repository": "ExampleOrg/apple-application",
                    "capabilities": ["private_network", "arbitrary_secret_access"],
                }
            }
        )
        self.assertNotEqual(unknown_capability.returncode, 0)
        self.assertIn("unknown capability", unknown_capability.stderr)

        duplicate_capability, _ = self.run_capability_resolver(
            project_state={
                "repository_ci": {
                    "schemaVersion": 1,
                    "repository": "ExampleOrg/apple-application",
                    "capabilities": ["private_network", "private_network"],
                }
            }
        )
        self.assertNotEqual(duplicate_capability.returncode, 0)
        self.assertIn("unique list", duplicate_capability.stderr)


    def test_oci_publish_capability_is_release_scoped_and_uses_isolated_tool_auth(self) -> None:
        grant = {
            "repository_ci": {
                "schemaVersion": 1,
                "repository": "ExampleOrg/service-backend",
                "capabilities": ["private_network", "registry_oci_publish"],
            }
        }

        validation, validation_values = self.run_capability_resolver(
            run_repository="ExampleOrg/service-backend",
            operation="build",
            host_os="linux",
            project_state=grant,
        )
        self.assertEqual(validation.returncode, 0, validation.stderr)
        self.assertEqual(validation_values["private_network"], "true")
        self.assertEqual(validation_values["registry_oci_publish"], "false")

        release, release_values = self.run_capability_resolver(
            run_repository="ExampleOrg/service-backend",
            run_ref="3.2.3",
            source_ref="3.2.3",
            run_is_tag=True,
            source_is_tag="true",
            operation="release",
            host_os="linux",
            run_profile="linux",
            run_workflow="release.repository",
            project_state=grant,
        )
        self.assertEqual(release.returncode, 0, release.stderr)
        self.assertEqual(release_values["private_network"], "true")
        self.assertEqual(release_values["registry_oci_publish"], "true")
        self.assertEqual(release_values["auth_enabled"], "false")

        setup = self.steps_by_name["Configure ephemeral OCI publication authentication"]
        self.assertEqual(
            setup["if"],
            "${{ steps.capabilities.outputs.registry_oci_publish == 'true' }}",
        )
        self.assertEqual(
            setup["env"]["REGISTRY_WRITE_TOKEN"],
            "${{ secrets.REGISTRY_WRITE_TOKEN }}",
        )
        self.assertIn("buildah login", setup["run"])
        self.assertIn("helm registry login", setup["run"])
        self.assertIn("git.faruqi.dev", setup["run"])
        self.assertIn("central-oci-publish", setup["run"])
        self.assertNotIn("mimranfaruqi/iptv", setup["run"])

        execute = self.steps_by_name["Execute fixed repository-owned entrypoint"]
        self.assertNotIn("REGISTRY_WRITE_TOKEN", execute["env"])
        self.assertEqual(
            execute["env"]["OCI_PUBLISH_ENABLED"],
            "${{ steps.capabilities.outputs.registry_oci_publish }}",
        )
        for env_name in (
            "OCI_AUTH_FILE",
            "OCI_HELM_REGISTRY_CONFIG",
            "OCI_HELM_REPOSITORY_CONFIG",
            "OCI_HELM_REPOSITORY_CACHE",
            "OCI_REGISTRY_HOST",
        ):
            self.assertIn(env_name, execute["env"])
        for exported in (
            "REGISTRY_AUTH_FILE",
            "HELM_REGISTRY_CONFIG",
            "HELM_REPOSITORY_CONFIG",
            "HELM_REPOSITORY_CACHE",
            "CI_OCI_REGISTRY",
        ):
            self.assertIn(f"export {exported}=", execute["run"])
        self.assertIn("CI_SECRET_REGISTRY_WRITE_TOKEN", execute["env"])
        self.assertIn(
            "CI_SECRET_REGISTRY_WRITE_TOKEN",
            self.steps_by_name["Scrub configured CI secrets from private text evidence"]["env"],
        )
        self.assertIn(
            "central-oci-publish",
            self.steps_by_name["Cleanup ephemeral registry and repository evidence"]["run"],
        )

        release_secrets = self.dispatch["jobs"]["repository_release"]["secrets"]
        validation_secrets = self.dispatch["jobs"]["repository"]["secrets"]
        write_expression = release_secrets["REGISTRY_WRITE_TOKEN"]
        self.assertIn("fromJSON(needs.request.outputs.inputs_json).host_os == 'linux'", write_expression)
        self.assertIn("secrets.FORGEJO_REGISTRY_TOKEN", write_expression)
        self.assertNotIn("REGISTRY_WRITE_TOKEN", validation_secrets)

    def test_trusted_aggregate_capability_context_is_parent_bound_and_lifecycle_separate(self) -> None:
        trusted_id = "22222222-2222-4222-8222-222222222222"

        accepted, _ = self.run_repository_request(
            operation="full",
            source_is_tag="true",
            trusted_capability_ci_run_id=trusted_id,
        )
        self.assertEqual(accepted.returncode, 0, accepted.stderr)

        product_caller, _ = self.run_repository_request(
            operation="full",
            source_is_tag="true",
            caller_repository="ExampleOrg/library-package",
            caller_ref="refs/tags/2.4.1",
            trusted_capability_ci_run_id=trusted_id,
        )
        self.assertNotEqual(product_caller.returncode, 0)
        self.assertIn("requires reviewed Central main", product_caller.stderr)

        dual_lifecycle, _ = self.run_repository_request(
            operation="full",
            source_is_tag="true",
            lifecycle_ci_run_id="11111111-1111-4111-8111-111111111111",
            trusted_capability_ci_run_id=trusted_id,
        )
        self.assertNotEqual(dual_lifecycle.returncode, 0)
        self.assertIn("cannot also own Agent State lifecycle", dual_lifecycle.stderr)

        wrong_operation, _ = self.run_repository_request(
            operation="build",
            source_is_tag="true",
            trusted_capability_ci_run_id=trusted_id,
        )
        self.assertNotEqual(wrong_operation.returncode, 0)
        self.assertIn("accepted only by full/release operations", wrong_operation.stderr)

        parent, values = self.run_capability_resolver(
            run_repository="ExampleOrg/library-package",
            source_repository="ExampleOrg/library-package",
            run_ref="2.4.1",
            source_ref="2.4.1",
            run_is_tag=True,
            source_is_tag="true",
            operation="full",
            run_profile="publish",
            run_workflow="release.library-package",
            ci_run_id="",
            trusted_capability_ci_run_id=trusted_id,
            project_state={
                "repository_ci": {
                    "schemaVersion": 1,
                    "repository": "ExampleOrg/library-package",
                    "capabilities": [
                        "private_network",
                        "github_git",
                        "registry_netrc",
                        "gradle_maven",
                    ],
                }
            },
        )
        self.assertEqual(parent.returncode, 0, parent.stderr)
        for capability in ("private_network", "github_git", "registry_netrc", "gradle_maven"):
            self.assertEqual(values[capability], "true")
        self.assertEqual(values["registry_oci_publish"], "false")
        self.assertEqual(values["auth_enabled"], "true")

        wrong_parent, _ = self.run_capability_resolver(
            run_repository="ExampleOrg/library-package",
            source_repository="ExampleOrg/library-package",
            run_ref="2.4.1",
            source_ref="2.4.1",
            run_is_tag=True,
            source_is_tag="true",
            operation="full",
            run_profile="publish",
            run_workflow="release.maven",
            ci_run_id="",
            trusted_capability_ci_run_id=trusted_id,
        )
        self.assertNotEqual(wrong_parent.returncode, 0)
        self.assertIn(
            "trusted aggregate capability parent is not release.library-package/publish",
            wrong_parent.stderr,
        )

    def test_package_auth_is_generic_file_configuration_not_product_secret_env(self) -> None:
        auth = self.steps_by_name[
            "Configure ephemeral generic package-manager authentication"
        ]
        script = auth["run"]
        self.assertIn("USE_GITHUB_GIT", auth["env"])
        self.assertIn("USE_REGISTRY_NETRC", auth["env"])
        self.assertIn("USE_GRADLE_MAVEN", auth["env"])
        self.assertIn("machine github.com", script)
        self.assertIn("machine git.faruqi.dev", script)
        self.assertIn("centralRegistryUsername=", script)
        self.assertIn("centralRegistryReadToken=", script)

        execute = self.steps_by_name["Execute fixed repository-owned entrypoint"]
        execute_env = execute["env"]
        for forbidden in (
            "REGISTRY_USERNAME",
            "REGISTRY_READ_TOKEN",
            "GITHUB_SOURCE_TOKEN",
            "TS_OAUTH_CLIENT_ID",
            "TS_OAUTH_SECRET",
            "GOOGLE_DRIVE_CLIENT_ID",
            "GOOGLE_DRIVE_CLIENT_SECRET",
            "GOOGLE_DRIVE_REFRESH_TOKEN",
        ):
            self.assertNotIn(forbidden, execute_env)

        checkpoint_only = {
            name
            for name in execute_env
            if name.startswith("CHECKPOINT_GOOGLE_DRIVE_") or name.startswith("CI_SECRET_")
        }
        self.assertTrue(checkpoint_only)
        script = execute["run"]
        product_index = script.index('"./${ENTRYPOINT}"')
        unset_index = script.index("unset \\")
        self.assertLess(unset_index, product_index)
        for name in checkpoint_only:
            self.assertIn(name, script[unset_index:product_index])

    def test_repository_log_checkpointing_replaces_one_seeded_drive_object(self) -> None:
        by_name = self.steps_by_name
        seed = by_name["Seed stable private repository CI log object"]
        execute = by_name["Execute fixed repository-owned entrypoint"]
        final_upload = by_name["Upload private repository CI log to Google Drive"]

        self.assertTrue(seed["continue-on-error"])
        self.assertEqual(
            seed["if"],
            "${{ inputs.ci_run_id != '' || inputs.upload_private_log }}",
        )
        self.assertEqual(
            seed["uses"],
            "StreamScapeTV/ci-workflows/actions/google-drive@main",
        )
        stable_name = "${{ github.run_id }}-${{ github.run_attempt }}-repository-${{ inputs.operation }}.txt"
        self.assertEqual(seed["with"]["file_name"], stable_name)
        self.assertEqual(final_upload["with"]["file_name"], stable_name)
        self.assertEqual(
            seed["with"]["file_path"],
            "${{ runner.temp }}/central-repository-ci-log-checkpoint-seed.txt",
        )
        init = by_name["Initialize private execution paths"]["run"]
        self.assertIn('checkpoint_seed="${RUNNER_TEMP}/central-repository-ci-log-checkpoint-seed.txt"', init)
        self.assertIn(': > "${checkpoint_seed}"', init)
        self.assertNotEqual(seed["with"]["file_path"], "${{ runner.temp }}/central-repository-ci.log")

        env = execute["env"]
        self.assertEqual(env["CHECKPOINT_FILE_ID"], "${{ steps.log_seed.outputs.file_id }}")
        self.assertEqual(
            env["CHECKPOINT_SEED_SHA256"],
            "${{ steps.log_seed.outputs.file_sha256 }}",
        )
        script = execute["run"]
        self.assertIn("central-ci/scripts/ci/repository_log_checkpoint.py", script)
        self.assertIn('--file-id "${CHECKPOINT_FILE_ID}"', script)
        self.assertIn('--seed-sha256 "${CHECKPOINT_SEED_SHA256}"', script)
        self.assertIn('--timeline-state-path "${RUNNER_TEMP}/central-repository-ci-timeline.json"', script)
        self.assertIn('--progress-path "${CI_PROGRESS_FILE}"', script)
        self.assertNotIn("--repository", script)
        self.assertNotIn("--file-name", script)
        self.assertNotIn("--folder-id", script)
        self.assertIn('kill -TERM "${checkpoint_pid}"', script)
        self.assertIn("trap terminate_with_checkpoint TERM INT", script)
        self.assertLess(
            script.index("stop_checkpoint", script.index('"./${ENTRYPOINT}"')),
            script.index("printf 'exit_code=%s"),
        )

        timeline_seed = by_name["Record private-capabilities completion and start repository entrypoint"]["run"]
        self.assertIn('cp -- "${CI_LOG}" "${seed}"', timeline_seed)
        self.assertLess(
            [step.get("name") for step in self.workflow["jobs"]["execute"]["steps"]].index(
                "Record private-capabilities completion and start repository entrypoint"
            ),
            [step.get("name") for step in self.workflow["jobs"]["execute"]["steps"]].index(
                "Seed stable private repository CI log object"
            ),
        )

        cleanup = by_name["Cleanup ephemeral registry and repository evidence"]["run"]
        self.assertIn("central-repository-ci-log-checkpoint-seed.txt", cleanup)
        self.assertIn("central-repository-ci-log-checkpoint-status.json", cleanup)
        self.assertIn("central-repository-ci-timeline.json", cleanup)

    def test_repository_timeline_has_fixed_central_phases_and_terminal_entrypoint_states(self) -> None:
        by_name = self.steps_by_name
        names = [step.get("name") for step in self.workflow["jobs"]["execute"]["steps"]]
        self.assertLess(names.index("Check out reviewed repository CI contract"), names.index("Start bounded Central repository CI timeline"))
        self.assertLess(names.index("Record observed source SHA"), names.index("Record source-admission completion"))
        self.assertLess(names.index("Configure ephemeral generic package-manager authentication"), names.index("Record private-capabilities completion and start repository entrypoint"))
        self.assertLess(names.index("Record private-capabilities completion and start repository entrypoint"), names.index("Execute fixed repository-owned entrypoint"))

        start = by_name["Start bounded Central repository CI timeline"]["run"]
        source = by_name["Record source-admission completion"]["run"]
        capabilities = by_name["Record private-capabilities completion and start repository entrypoint"]["run"]
        execute = by_name["Execute fixed repository-owned entrypoint"]["run"]
        reconcile = by_name["Reconcile bounded Central timeline after early exit"]
        self.assertEqual(reconcile["if"], "${{ always() }}")
        timeline_status = reconcile["env"]["TIMELINE_STATUS"]
        self.assertEqual(
            timeline_status,
            "${{ job.status == 'cancelled' && 'cancelled' || 'failed' }}",
        )
        self.assertIn("job.status", timeline_status)
        for status_check in ("cancelled()", "failure()", "success()", "always()"):
            self.assertNotIn(status_check, timeline_status)
        self.assertIn("finish-active", reconcile["run"])
        self.assertLess(names.index("Execute fixed repository-owned entrypoint"), names.index("Reconcile bounded Central timeline after early exit"))
        self.assertLess(names.index("Reconcile bounded Central timeline after early exit"), names.index("Scrub configured CI secrets from private text evidence"))
        self.assertIn("--phase source-admission", start)
        self.assertIn("--phase source-admission", source)
        self.assertIn("--phase private-capabilities", source)
        self.assertIn("--phase private-capabilities", capabilities)
        self.assertIn("--phase repository-entrypoint", capabilities)
        self.assertIn("finish_entrypoint_timeline complete", execute)
        self.assertIn("finish_entrypoint_timeline failed", execute)
        self.assertIn("finish_entrypoint_timeline cancelled", execute)
        for forbidden in ("inputs.timeline", "inputs.phase", "phase_name", "metadata_json"):
            self.assertNotIn(forbidden, start + source + capabilities + execute)

    def test_central_owns_log_paths_scrubbing_transport_and_cleanup(self) -> None:
        by_name = self.steps_by_name
        init = by_name["Initialize private execution paths"]["run"]
        for name in ("CI_LOG_DIR", "CI_ARTIFACT_DIR", "CI_PROGRESS_FILE"):
            self.assertIn(name, init)

        scrub = by_name["Scrub configured CI secrets from private text evidence"]
        self.assertEqual(scrub["if"], "${{ always() }}")
        self.assertIn('log_root = Path(os.environ["CI_LOG_DIR"])', scrub["run"])
        self.assertIn('artifact_root = Path(os.environ["CI_ARTIFACT_DIR"])', scrub["run"])
        self.assertIn("central-repository-ci-artifact-selection.json", scrub["run"])
        self.assertIn("artifact_selection_sha256=", scrub["run"])
        self.assertNotIn('for name in ("CI_LOG_DIR", "CI_ARTIFACT_DIR")', scrub["run"])

        package = by_name["Package bounded repository CI evidence"]
        self.assertEqual(
            package["env"]["ARTIFACT_SELECTION_SHA256"],
            "${{ steps.scrub.outputs.artifact_selection_sha256 }}",
        )
        self.assertIn("central-repository-ci-artifact-selection.json", package["run"])
        self.assertIn("artifact selection changed after scrub", package["run"])
        self.assertLess(
            package["run"].index("hashlib.sha256(artifact_selection_bytes)"),
            package["run"].index("json.loads(artifact_selection_bytes.decode"),
        )
        self.assertNotIn('for path in sorted(artifact_root.rglob("*"))', package["run"])
        self.assertIn("evidence exceeds 256 files", package["run"])
        self.assertIn("evidence exceeds 64 MiB", package["run"])
        self.assertIn("artifact evidence symlink escapes its Central-owned root", package["run"])
        self.assertIn("stat.S_IFLNK", package["run"])

        upload = by_name["Upload private repository CI log to Google Drive"]
        self.assertEqual(
            upload["uses"],
            "StreamScapeTV/ci-workflows/actions/google-drive@main",
        )
        self.assertEqual(upload["with"]["mime_type"], "text/plain")
        evidence_upload = by_name[
            "Upload bounded repository CI evidence to Google Drive"
        ]
        self.assertEqual(evidence_upload["with"]["mime_type"], "application/zip")

        cleanup = by_name[
            "Cleanup ephemeral registry and repository evidence"
        ]
        self.assertEqual(cleanup["if"], "${{ always() }}")
        self.assertIn("central-registry-auth", cleanup["run"])
        self.assertIn("central-oci-publish", cleanup["run"])
        self.assertIn("central-repository-ci-artifact-selection.json", cleanup["run"])

    def test_evidence_archive_output_is_one_existing_regular_file_path(self) -> None:
        by_name = self.steps_by_name
        scrub = by_name["Scrub configured CI secrets from private text evidence"]["run"]
        package = by_name["Package bounded repository CI evidence"]
        evidence_upload = by_name["Upload bounded repository CI evidence to Google Drive"]
        self.assertEqual(
            evidence_upload["with"]["file_path"],
            "${{ steps.evidence.outputs.archive }}",
        )

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            log_dir = root / "logs"
            artifact_dir = root / "artifacts"
            log_dir.mkdir()
            artifact_dir.mkdir()
            ci_log = root / "central.log"
            ci_log.write_text("central log\n", encoding="utf-8")
            (log_dir / "tool.log").write_text("bounded log\n", encoding="utf-8")
            (artifact_dir / "result.json").write_text("{}\n", encoding="utf-8")
            progress = root / "progress.txt"
            progress.write_text("complete\n", encoding="utf-8")
            github_output = root / "github-output"
            github_output.write_text("", encoding="utf-8")
            env = {
                **os.environ,
                "RUNNER_TEMP": str(root),
                "CI_LOG": str(ci_log),
                "CI_LOG_DIR": str(log_dir),
                "CI_ARTIFACT_DIR": str(artifact_dir),
                "CI_PROGRESS_FILE": str(progress),
                "GITHUB_OUTPUT": str(github_output),
            }

            scrubbed = subprocess.run(
                ["bash", "-c", scrub],
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(scrubbed.returncode, 0, scrubbed.stderr)
            self.bind_artifact_selection_digest(env, github_output)
            result = subprocess.run(
                ["bash", "-c", package["run"]],
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            output_lines = github_output.read_text(encoding="utf-8").splitlines()
            archive_lines = [line for line in output_lines if line.startswith("archive=")]
            self.assertEqual(len(archive_lines), 1)
            archive_value = archive_lines[0].split("=", 1)[1]
            self.assertNotIn("\\n", archive_value)
            archive = Path(archive_value)
            self.assertEqual(archive, root / "central-repository-ci-evidence.zip")
            self.assertTrue(archive.is_file())
            self.assertFalse(archive.is_symlink())

    def test_framework_style_artifact_symlinks_survive_scrub_and_package_without_dereference(self) -> None:
        by_name = self.steps_by_name
        scrub = by_name["Scrub configured CI secrets from private text evidence"]["run"]
        package = by_name["Package bounded repository CI evidence"]["run"]
        drive_if = by_name["Upload private repository CI log to Google Drive"]["if"]
        evidence_if = by_name["Upload bounded repository CI evidence to Google Drive"]["if"]
        self.assertNotIn("steps.execute_contract.outcome == 'success'", drive_if)
        self.assertNotIn("steps.execute_contract.outcome == 'success'", evidence_if)

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            log_dir = root / "logs"
            artifact_dir = root / "artifacts"
            framework = artifact_dir / "Example.framework"
            headers = framework / "Versions" / "A" / "Headers"
            headers.mkdir(parents=True)
            log_dir.mkdir()
            (headers / "Example.h").write_bytes(b"binary-safe-header\n")
            (framework / "Versions" / "Current").symlink_to("A", target_is_directory=True)
            (framework / "Headers").symlink_to(
                "Versions/Current/Headers",
                target_is_directory=True,
            )
            secret = "registry-secret-value"
            ci_log = root / "central.log"
            ci_log.write_text(f"timeout {secret}\n", encoding="utf-8")
            (log_dir / "tool.log").write_text(f"tool {secret}\n", encoding="utf-8")
            progress = root / "progress.txt"
            progress.write_text("timed-out\n", encoding="utf-8")
            github_output = root / "github-output"
            github_output.write_text("", encoding="utf-8")
            env = {
                **os.environ,
                "RUNNER_TEMP": str(root),
                "CI_LOG": str(ci_log),
                "CI_PROGRESS_FILE": str(progress),
                "CI_LOG_DIR": str(log_dir),
                "CI_ARTIFACT_DIR": str(artifact_dir),
                "CI_SECRET_TEST": secret,
                "GITHUB_OUTPUT": str(github_output),
            }

            scrubbed = subprocess.run(
                ["bash", "-c", scrub],
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(scrubbed.returncode, 0, scrubbed.stderr)
            self.bind_artifact_selection_digest(env, github_output)
            self.assertTrue((framework / "Headers").is_symlink())
            self.assertNotIn(secret, ci_log.read_text(encoding="utf-8"))
            self.assertNotIn(secret, (log_dir / "tool.log").read_text(encoding="utf-8"))

            packaged = subprocess.run(
                ["bash", "-c", package],
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(packaged.returncode, 0, packaged.stderr)
            archive = root / "central-repository-ci-evidence.zip"
            self.assertTrue(archive.is_file())
            with zipfile.ZipFile(archive) as bundle:
                for name, target in (
                    ("artifacts/Example.framework/Headers", b"Versions/Current/Headers"),
                    ("artifacts/Example.framework/Versions/Current", b"A"),
                ):
                    info = bundle.getinfo(name)
                    self.assertEqual((info.external_attr >> 16) & 0o170000, stat.S_IFLNK)
                    self.assertEqual(bundle.read(name), target)
                self.assertEqual(
                    bundle.read("artifacts/Example.framework/Versions/A/Headers/Example.h"),
                    b"binary-safe-header\n",
                )

    def test_secret_bearing_artifacts_are_quarantined_without_suppressing_safe_diagnostics(self) -> None:
        by_name = self.steps_by_name
        scrub = by_name["Scrub configured CI secrets from private text evidence"]["run"]
        package = by_name["Package bounded repository CI evidence"]["run"]
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            log_dir = root / "logs"
            artifact_dir = root / "artifacts"
            log_dir.mkdir()
            artifact_dir.mkdir()
            secret = "registry-secret-value"
            multiline_secret = "registry-line-one\nregistry-line-two"
            ci_log = root / "central.log"
            ci_log.write_text("timeout diagnostics\n", encoding="utf-8")
            (log_dir / "tool.log").write_text("bounded log\n", encoding="utf-8")
            unsafe = artifact_dir / "arm64-apple-macos.abi.json"
            unsafe.write_bytes(
                b'{"credential":"' + secret.encode() + b'"}\n'
            )
            escaped_unsafe = artifact_dir / "escaped.json"
            escaped_unsafe.write_bytes(
                multiline_secret.encode().replace(b"\n", b"\\n")
            )
            safe = artifact_dir / "result.json"
            safe.write_text('{"status":"cancelled"}\n', encoding="utf-8")
            progress = root / "progress.txt"
            progress.write_text("cancelled\n", encoding="utf-8")
            github_output = root / "github-output"
            github_output.write_text("", encoding="utf-8")
            env = {
                **os.environ,
                "RUNNER_TEMP": str(root),
                "CI_LOG": str(ci_log),
                "CI_PROGRESS_FILE": str(progress),
                "CI_LOG_DIR": str(log_dir),
                "CI_ARTIFACT_DIR": str(artifact_dir),
                "CI_SECRET_TEST": secret,
                "CI_SECRET_MULTILINE": multiline_secret,
                "GITHUB_OUTPUT": str(github_output),
            }

            scrubbed = subprocess.run(
                ["bash", "-c", scrub],
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(scrubbed.returncode, 0, scrubbed.stderr)
            self.bind_artifact_selection_digest(env, github_output)
            selection_path = root / "central-repository-ci-artifact-selection.json"
            selection = json.loads(selection_path.read_text(encoding="utf-8"))
            retained = [item["path"] for item in selection["artifacts"]]
            self.assertNotIn(unsafe.name, retained)
            self.assertNotIn(escaped_unsafe.name, retained)
            self.assertIn(safe.name, retained)
            self.assertIn("quarantined 2 secret-bearing", scrubbed.stdout)

            packaged = subprocess.run(
                ["bash", "-c", package],
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(packaged.returncode, 0, packaged.stderr)
            archive = root / "central-repository-ci-evidence.zip"
            self.assertTrue(archive.is_file())
            with zipfile.ZipFile(archive) as bundle:
                names = set(bundle.namelist())
                self.assertNotIn(f"artifacts/{unsafe.name}", names)
                self.assertNotIn(f"artifacts/{escaped_unsafe.name}", names)
                self.assertIn(f"artifacts/{safe.name}", names)
                self.assertIn("logs/tool.log", names)
                self.assertIn("progress.txt", names)
                retained_bytes = b"".join(bundle.read(name) for name in names)
                self.assertNotIn(secret.encode(), retained_bytes)
                self.assertNotIn(
                    multiline_secret.encode().replace(b"\n", b"\\n"),
                    retained_bytes,
                )

            self.assertIn(
                "steps.scrub.outcome == 'success'",
                by_name["Upload private repository CI log to Google Drive"]["if"],
            )
            self.assertIn(
                "steps.evidence.outcome == 'success'",
                by_name["Upload bounded repository CI evidence to Google Drive"]["if"],
            )

    def test_artifact_selection_rejects_post_scrub_mutation(self) -> None:
        scrub = self.steps_by_name["Scrub configured CI secrets from private text evidence"]["run"]
        package = self.steps_by_name["Package bounded repository CI evidence"]["run"]
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            log_dir = root / "logs"
            artifact_dir = root / "artifacts"
            log_dir.mkdir()
            artifact_dir.mkdir()
            secret = "registry-secret-value"
            ci_log = root / "central.log"
            ci_log.write_text("bounded diagnostics\n", encoding="utf-8")
            (log_dir / "tool.log").write_text("bounded tool log\n", encoding="utf-8")
            artifact = artifact_dir / "result.json"
            artifact.write_text('{"status":"safe"}\n', encoding="utf-8")
            progress = root / "progress.txt"
            progress.write_text("running\n", encoding="utf-8")
            github_output = root / "github-output"
            github_output.write_text("", encoding="utf-8")
            env = {
                **os.environ,
                "RUNNER_TEMP": str(root),
                "CI_LOG": str(ci_log),
                "CI_PROGRESS_FILE": str(progress),
                "CI_LOG_DIR": str(log_dir),
                "CI_ARTIFACT_DIR": str(artifact_dir),
                "CI_SECRET_TEST": secret,
                "GITHUB_OUTPUT": str(github_output),
            }

            scrubbed = subprocess.run(
                ["bash", "-c", scrub],
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(scrubbed.returncode, 0, scrubbed.stderr)
            self.bind_artifact_selection_digest(env, github_output)
            artifact.write_bytes(secret.encode())

            packaged = subprocess.run(
                ["bash", "-c", package],
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertNotEqual(packaged.returncode, 0)
            self.assertIn("artifact selection changed after scrub", packaged.stderr)
            self.assertFalse((root / "central-repository-ci-evidence.zip").exists())
            self.assertNotIn("archive=", github_output.read_text(encoding="utf-8"))

    def test_artifact_selection_manifest_tamper_is_rejected_before_parse(self) -> None:
        scrub = self.steps_by_name["Scrub configured CI secrets from private text evidence"]["run"]
        package = self.steps_by_name["Package bounded repository CI evidence"]["run"]
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            log_dir = root / "logs"
            artifact_dir = root / "artifacts"
            log_dir.mkdir()
            artifact_dir.mkdir()
            secret = "registry-secret-value"
            ci_log = root / "central.log"
            ci_log.write_text("bounded diagnostics\n", encoding="utf-8")
            (log_dir / "tool.log").write_text("bounded tool log\n", encoding="utf-8")
            unsafe = artifact_dir / "arm64-apple-macos.abi.json"
            unsafe_bytes = b'{"credential":"' + secret.encode() + b'"}\n'
            unsafe.write_bytes(unsafe_bytes)
            safe = artifact_dir / "result.json"
            safe.write_text('{"status":"cancelled"}\n', encoding="utf-8")
            progress = root / "progress.txt"
            progress.write_text("cancelled\n", encoding="utf-8")
            github_output = root / "github-output"
            github_output.write_text("", encoding="utf-8")
            env = {
                **os.environ,
                "RUNNER_TEMP": str(root),
                "CI_LOG": str(ci_log),
                "CI_PROGRESS_FILE": str(progress),
                "CI_LOG_DIR": str(log_dir),
                "CI_ARTIFACT_DIR": str(artifact_dir),
                "CI_SECRET_TEST": secret,
                "GITHUB_OUTPUT": str(github_output),
            }

            scrubbed = subprocess.run(
                ["bash", "-c", scrub],
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(scrubbed.returncode, 0, scrubbed.stderr)
            original_digest = self.bind_artifact_selection_digest(env, github_output)
            selection_path = root / "central-repository-ci-artifact-selection.json"
            selection = json.loads(selection_path.read_text(encoding="utf-8"))
            self.assertNotIn(unsafe.name, [item["path"] for item in selection["artifacts"]])
            selection["artifacts"].append(
                {
                    "kind": "file",
                    "path": unsafe.name,
                    "sha256": hashlib.sha256(unsafe_bytes).hexdigest(),
                    "size": len(unsafe_bytes),
                }
            )
            selection["artifacts"] = sorted(selection["artifacts"], key=lambda item: item["path"])
            selection_path.write_text(
                json.dumps(selection, sort_keys=True, separators=(",", ":")) + "\n",
                encoding="utf-8",
            )
            self.assertNotEqual(
                hashlib.sha256(selection_path.read_bytes()).hexdigest(),
                original_digest,
            )

            packaged = subprocess.run(
                ["bash", "-c", package],
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertNotEqual(packaged.returncode, 0)
            self.assertIn("artifact selection changed after scrub", packaged.stderr)
            self.assertFalse((root / "central-repository-ci-evidence.zip").exists())
            self.assertNotIn("archive=", github_output.read_text(encoding="utf-8"))

    def test_oversized_artifact_is_excluded_without_suppressing_timeout_diagnostics(self) -> None:
        scrub = self.steps_by_name["Scrub configured CI secrets from private text evidence"]["run"]
        package = self.steps_by_name["Package bounded repository CI evidence"]["run"]

        artifact_guard = scrub.index(
            "if path.stat().st_size > 16 * 1024 * 1024:",
            scrub.index("resolved_artifact_root"),
        )
        artifact_read = scrub.index("data = path.read_bytes()", artifact_guard)
        self.assertIn("continue", scrub[artifact_guard:artifact_read])

        self.assertIn("central-repository-ci-artifact-selection.json", scrub)
        self.assertIn("central-repository-ci-artifact-selection.json", package)
        self.assertNotIn('for path in sorted(artifact_root.rglob("*"))', package)
        self.assertIn("artifact selection changed after scrub", package)

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            log_dir = root / "logs"
            artifact_dir = root / "artifacts"
            log_dir.mkdir()
            artifact_dir.mkdir()

            secret = "registry-secret-value"
            ci_log = root / "central.log"
            ci_log.write_text(f"timeout {secret}\n", encoding="utf-8")
            (log_dir / "tool.log").write_text("bounded tool log\n", encoding="utf-8")
            progress = root / "progress.txt"
            progress.write_text("timed-out\n", encoding="utf-8")

            oversized = artifact_dir / "build.db"
            oversized.write_bytes(b"oversized-artifact-prefix")
            with oversized.open("r+b") as handle:
                handle.truncate(16 * 1024 * 1024 + 1)
            small = artifact_dir / "result.json"
            small.write_text('{"status":"failed"}\n', encoding="utf-8")

            github_output = root / "github-output"
            github_output.write_text("", encoding="utf-8")
            env = {
                **os.environ,
                "RUNNER_TEMP": str(root),
                "CI_LOG": str(ci_log),
                "CI_PROGRESS_FILE": str(progress),
                "CI_LOG_DIR": str(log_dir),
                "CI_ARTIFACT_DIR": str(artifact_dir),
                "CI_SECRET_TEST": secret,
                "GITHUB_OUTPUT": str(github_output),
            }

            scrubbed = subprocess.run(
                ["bash", "-c", scrub],
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(scrubbed.returncode, 0, scrubbed.stderr)
            self.bind_artifact_selection_digest(env, github_output)
            self.assertNotIn(secret, ci_log.read_text(encoding="utf-8"))
            self.assertEqual(oversized.stat().st_size, 16 * 1024 * 1024 + 1)

            packaged = subprocess.run(
                ["bash", "-c", package],
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(packaged.returncode, 0, packaged.stderr)

            archive = root / "central-repository-ci-evidence.zip"
            self.assertTrue(archive.is_file())
            with zipfile.ZipFile(archive) as bundle:
                names = set(bundle.namelist())
                self.assertNotIn("artifacts/build.db", names)
                self.assertIn("artifacts/result.json", names)
                self.assertIn("logs/tool.log", names)
                self.assertIn("progress.txt", names)
                self.assertNotIn(
                    b"oversized-artifact-prefix",
                    b"".join(bundle.read(name) for name in names),
                )

            self.assertIn(
                "steps.scrub.outcome == 'success'",
                self.steps_by_name["Upload private repository CI log to Google Drive"]["if"],
            )

    def test_artifact_symlink_escape_is_rejected_without_reading_outside_bytes(self) -> None:
        scrub = self.steps_by_name["Scrub configured CI secrets from private text evidence"]["run"]
        package = self.steps_by_name["Package bounded repository CI evidence"]["run"]
        self.assertIn("artifact evidence symlink escapes its Central-owned root", package)
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            log_dir = root / "logs"
            artifact_dir = root / "artifacts"
            log_dir.mkdir()
            artifact_dir.mkdir()
            ci_log = root / "central.log"
            ci_log.write_text("bounded central log\n", encoding="utf-8")
            (log_dir / "tool.log").write_text("bounded log\n", encoding="utf-8")
            outside = root / "outside-secret.bin"
            outside_bytes = b"must-never-enter-archive"
            outside.write_bytes(outside_bytes)
            (artifact_dir / "Headers").symlink_to(outside)
            progress = root / "progress.txt"
            progress.write_text("failed\n", encoding="utf-8")
            github_output = root / "github-output"
            github_output.write_text("", encoding="utf-8")
            env = {
                **os.environ,
                "RUNNER_TEMP": str(root),
                "CI_LOG": str(ci_log),
                "CI_LOG_DIR": str(log_dir),
                "CI_ARTIFACT_DIR": str(artifact_dir),
                "CI_PROGRESS_FILE": str(progress),
                "GITHUB_OUTPUT": str(github_output),
            }

            result = subprocess.run(
                ["bash", "-c", scrub],
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("artifact evidence symlink escapes its Central-owned root", result.stderr)
            self.assertEqual(outside.read_bytes(), outside_bytes)
            selection = root / "central-repository-ci-artifact-selection.json"
            self.assertFalse(selection.exists())
            archive = root / "central-repository-ci-evidence.zip"
            self.assertFalse(archive.exists())
            self.assertEqual(github_output.read_text(encoding="utf-8"), "")

    def test_text_evidence_scrub_fails_closed_on_oversize_or_symlink_and_redacts_normal_log(self) -> None:
        by_name = self.steps_by_name
        script = by_name[
            "Scrub configured CI secrets from private text evidence"
        ]["run"]
        drive_if = by_name["Upload private repository CI log to Google Drive"]["if"]
        evidence_if = by_name[
            "Upload bounded repository CI evidence to Google Drive"
        ]["if"]
        self.assertIn("steps.scrub.outcome == 'success'", drive_if)
        self.assertIn("steps.evidence.outcome == 'success'", evidence_if)

        def run_case(
            *,
            oversized_log: bool = False,
            oversized_progress: bool = False,
            symlink_log: bool = False,
        ):
            with tempfile.TemporaryDirectory() as td:
                root = Path(td)
                log_dir = root / "logs"
                artifact_dir = root / "artifacts"
                log_dir.mkdir()
                artifact_dir.mkdir()
                secret = "registry-secret-value"
                target = root / "target.log"
                target.write_text(f"prefix {secret} suffix\n", encoding="utf-8")
                ci_log = root / "central.log"
                if symlink_log:
                    ci_log.symlink_to(target)
                else:
                    ci_log.write_text(
                        f"prefix {secret} suffix\n",
                        encoding="utf-8",
                    )
                    if oversized_log:
                        with ci_log.open("r+b") as handle:
                            handle.truncate(16 * 1024 * 1024 + 1)
                progress = root / "progress.txt"
                progress.write_text("ok\n", encoding="utf-8")
                if oversized_progress:
                    with progress.open("r+b") as handle:
                        handle.truncate(16 * 1024 * 1024 + 1)
                github_output = root / "github-output"
                github_output.write_text("", encoding="utf-8")
                env = {
                    **os.environ,
                    "RUNNER_TEMP": str(root),
                    "CI_LOG": str(ci_log),
                    "CI_PROGRESS_FILE": str(progress),
                    "CI_LOG_DIR": str(log_dir),
                    "CI_ARTIFACT_DIR": str(artifact_dir),
                    "CI_SECRET_TEST": secret,
                    "GITHUB_OUTPUT": str(github_output),
                }
                result = subprocess.run(
                    ["bash", "-c", script],
                    env=env,
                    text=True,
                    capture_output=True,
                    check=False,
                )
                log_text = (
                    ci_log.read_text(encoding="utf-8", errors="replace")
                    if ci_log.exists()
                    else ""
                )
                target_text = target.read_text(
                    encoding="utf-8",
                    errors="replace",
                )
                return result, log_text, target_text, secret

        normal, log_text, _, secret = run_case()
        self.assertEqual(normal.returncode, 0, normal.stderr)
        self.assertNotIn(secret, log_text)
        self.assertIn("[REDACTED]", log_text)

        oversized_log, _, _, _ = run_case(oversized_log=True)
        self.assertNotEqual(oversized_log.returncode, 0)
        self.assertIn("exceeds 16 MiB before scrub", oversized_log.stderr)

        oversized_progress, _, _, _ = run_case(oversized_progress=True)
        self.assertNotEqual(oversized_progress.returncode, 0)
        self.assertIn("exceeds 16 MiB before scrub", oversized_progress.stderr)

        symlinked, _, target_text, secret = run_case(symlink_log=True)
        self.assertNotEqual(symlinked.returncode, 0)
        self.assertIn("regular non-symlink file", symlinked.stderr)
        self.assertIn(secret, target_text)

    def test_repository_release_forwards_existing_apple_signing_secrets_only_to_macos_release(self) -> None:
        secret_names = (
            "APPLE_TEAM_ID",
            "APP_STORE_CONNECT_KEY_ID",
            "APP_STORE_CONNECT_ISSUER_ID",
            "APP_STORE_CONNECT_API_KEY_P8_BASE64",
        )
        workflow_secrets = self.workflow["on"]["workflow_call"]["secrets"]
        for name in secret_names:
            self.assertIn(name, workflow_secrets)
            self.assertFalse(workflow_secrets[name]["required"])

        release_secrets = self.dispatch["jobs"]["repository_release"]["secrets"]
        validation_secrets = self.dispatch["jobs"]["repository"]["secrets"]
        for name in secret_names:
            expression = release_secrets[name]
            self.assertIn("fromJSON(needs.request.outputs.inputs_json).host_os == 'macos'", expression)
            self.assertIn(f"secrets.{name}", expression)
            self.assertNotIn(name, validation_secrets)

        execute_env = self.steps_by_name["Execute fixed repository-owned entrypoint"]["env"]
        expected_product_vars = {
            "CI_APPLE_TEAM_ID": "APPLE_TEAM_ID",
            "CI_APP_STORE_CONNECT_KEY_ID": "APP_STORE_CONNECT_KEY_ID",
            "CI_APP_STORE_CONNECT_ISSUER_ID": "APP_STORE_CONNECT_ISSUER_ID",
            "CI_APP_STORE_CONNECT_API_KEY_P8_BASE64": "APP_STORE_CONNECT_API_KEY_P8_BASE64",
        }
        for env_name, secret_name in expected_product_vars.items():
            expression = execute_env[env_name]
            self.assertIn("inputs.operation == 'release'", expression)
            self.assertIn("needs.resolve_host.outputs.host_os == 'macos'", expression)
            self.assertIn(f"secrets.{secret_name}", expression)

        scrub_env = self.steps_by_name["Scrub configured CI secrets from private text evidence"]["env"]
        for env_name in (
            "CI_SECRET_APPLE_TEAM_ID",
            "CI_SECRET_APP_STORE_CONNECT_KEY_ID",
            "CI_SECRET_APP_STORE_CONNECT_ISSUER_ID",
            "CI_SECRET_APP_STORE_CONNECT_API_KEY",
        ):
            self.assertIn(env_name, scrub_env)
            self.assertIn("inputs.operation == 'release'", scrub_env[env_name])
            self.assertIn("needs.resolve_host.outputs.host_os == 'macos'", scrub_env[env_name])

    def test_non_migrated_product_workflows_remain_selected_by_existing_lanes(self) -> None:
        jobs = self.dispatch["jobs"]
        self.assertEqual(jobs["apple"]["uses"], "./.github/workflows/apple.yml")
        self.assertEqual(jobs["android"]["uses"], "./.github/workflows/android.yml")
        self.assertEqual(jobs["maven"]["uses"], "./.github/workflows/maven.yml")
        self.assertIn("repository", jobs["settle_cancelled"]["needs"])
        self.assertIn(
            "needs.repository.result == 'cancelled'",
            jobs["settle_cancelled"]["if"],
        )

    def test_inventory_exposes_one_generic_workflow_and_one_contract(self) -> None:
        inventory = yaml.safe_load(INVENTORY.read_text(encoding="utf-8"))
        self.assertEqual(
            inventory["workflows"]["repository"],
            ".github/workflows/repository.yml",
        )
        self.assertEqual(
            inventory["contracts"]["repository_ci_v1"],
            "contracts/repository-ci-v1.json",
        )
        self.assertEqual(
            inventory["scripts"]["repository_log_timeline"],
            "scripts/ci/repository_log_timeline.py",
        )
        self.assertFalse(
            (ROOT / ".github/workflows/repository-build.yml").exists()
        )
        self.assertFalse(
            (ROOT / ".github/workflows/repository-test.yml").exists()
        )
        self.assertFalse(
            (ROOT / ".github/workflows/repository-release.yml").exists()
        )


if __name__ == "__main__":
    unittest.main()
