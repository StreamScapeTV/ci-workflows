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
        host_class: str = "macos-high-capacity",
        observed_source_sha: str = "a" * 40,
        normalized_inputs: dict | None = None,
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
        host_class: str = "macos-high-capacity",
        observed_source_sha: str = "a" * 40,
        normalized_inputs: dict | None = None,
    ):
        script = self.steps_by_name[
            "Resolve trusted private infrastructure capabilities"
        ]["run"]
        source_repository = source_repository or run_repository
        source_ref = source_ref or run_ref
        source_is_tag = source_is_tag or ("true" if run_is_tag else "false")
        run_profile = run_profile or operation
        if normalized_inputs is None:
            normalized_inputs = {"schemaVersion": 1, "inputs": {}}
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
                    "HOST_CLASS": host_class,
                    "OBSERVED_SOURCE_SHA": observed_source_sha,
                    "NORMALIZED_INPUTS": json.dumps(normalized_inputs, sort_keys=True, separators=(",", ":")),
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
                "device-test": ".ci/device-test.sh",
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
        self.assertIn("CI_PROTECTED_DEPLOYED_CONFORMANCE", adoption["scriptEnvironment"])
        for name in (
            "CI_BACKUP_S3_ENDPOINT",
            "CI_BACKUP_S3_BUCKET",
            "CI_BACKUP_S3_REGION",
            "CI_BACKUP_S3_ACCESS_KEY_ID",
            "CI_BACKUP_S3_SECRET_ACCESS_KEY",
        ):
            self.assertIn(name, adoption["scriptEnvironment"])
        self.assertIn("CI_DEVICE_CONTEXT_FILE", adoption["scriptEnvironment"])
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
            "device-test) entrypoint=.ci/device-test.sh",
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

        for operation in ("build", "test", "full"):
            result, operation_output = self.run_repository_request(
                operation=operation,
                host_os="macos",
                semantic={
                    "product_target": "ios",
                    "build_configuration": "release",
                    **({"test_selectors": ["Suite/Test"]} if operation == "test" else {}),
                },
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            normalized_line = next(
                line
                for line in operation_output.splitlines()
                if line.startswith("semantic_inputs_json=")
            )
            operation_inputs = json.loads(normalized_line.split("=", 1)[1])["inputs"]
            self.assertEqual(operation_inputs["build_configuration"], "release")
            self.assertEqual(operation_inputs["product_target"], "ios")

        rejected_configuration, _ = self.run_repository_request(
            operation="build",
            host_os="macos",
            semantic={"product_target": "ios", "build_configuration": "Release"},
        )
        self.assertNotEqual(rejected_configuration.returncode, 0)
        self.assertIn("outside the reviewed enum", rejected_configuration.stderr)

        ui_configuration, _ = self.run_repository_request(
            operation="ui-test",
            host_os="macos",
            semantic={
                "product_target": "ios",
                "ui_mode": "smoke",
                "build_configuration": "release",
            },
        )
        self.assertNotEqual(ui_configuration.returncode, 0)
        self.assertIn("unknown field", ui_configuration.stderr)

        ui, _ = self.run_repository_request(
            operation="ui-test",
            host_os="macos",
            semantic={"product_target": "tvos", "ui_mode": "full-ordinary"},
        )
        self.assertEqual(ui.returncode, 0, ui.stderr)

        ui_custom, ui_custom_output = self.run_repository_request(
            operation="ui-test",
            host_os="macos",
            semantic={
                "product_target": "ios",
                "test_selectors": [
                    "AppUITests/NavigationRegression/testCredentialFreeRoute",
                    "AppUITests/LaunchSmoke/testSignedOutRoot",
                ],
            },
        )
        self.assertEqual(ui_custom.returncode, 0, ui_custom.stderr)
        ui_inputs = json.loads(
            next(
                line
                for line in ui_custom_output.splitlines()
                if line.startswith("semantic_inputs_json=")
            ).split("=", 1)[1]
        )["inputs"]
        self.assertEqual(
            ui_inputs["test_selectors"],
            [
                "AppUITests/NavigationRegression/testCredentialFreeRoute",
                "AppUITests/LaunchSmoke/testSignedOutRoot",
            ],
        )

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

        device, device_output = self.run_repository_request(
            operation="device-test",
            host_os="macos",
            semantic={"device_platform": "ios", "test_selectors": ["physical-core"]},
        )
        self.assertEqual(device.returncode, 0, device.stderr)
        self.assertIn("entrypoint=.ci/device-test.sh\n", device_output)
        device_inputs = json.loads(
            next(line for line in device_output.splitlines() if line.startswith("semantic_inputs_json=")).split("=", 1)[1]
        )["inputs"]
        self.assertEqual(device_inputs, {"device_platform": "ios", "test_selectors": ["physical-core"]})

        missing_device_selector, _ = self.run_repository_request(
            operation="device-test", host_os="macos", semantic={"device_platform": "ios"}
        )
        self.assertNotEqual(missing_device_selector.returncode, 0)
        self.assertIn("omit a required field", missing_device_selector.stderr)

        wrong_device_platform, _ = self.run_repository_request(
            operation="device-test", host_os="macos", semantic={"device_platform": "tvos", "test_selectors": ["physical-core"]}
        )
        self.assertNotEqual(wrong_device_platform.returncode, 0)
        self.assertIn("outside the reviewed enum", wrong_device_platform.stderr)

        invalid_host, _ = self.run_repository_request(host_os="ios")
        self.assertNotEqual(invalid_host.returncode, 0)
        self.assertIn("host_os must be linux or macos", invalid_host.stderr)

    def test_release_configuration_semantic_stays_generic_and_unsigned(self) -> None:
        fields = self.contract["semanticInputs"]["fields"]
        self.assertEqual(
            fields["build_configuration"],
            {"type": "enum", "values": ["debug", "release"]},
        )
        operations = self.contract["semanticInputs"]["operations"]
        for operation in ("build", "test", "full"):
            self.assertIn("build_configuration", operations[operation]["allowed"])
        self.assertIn("test_selectors", operations["ui-test"]["allowed"])
        self.assertNotIn("build_configuration", operations["ui-test"]["allowed"])
        self.assertNotIn("build_configuration", operations["release"]["allowed"])

        self.assertNotIn("release-build", self.workflow_text)
        self.assertNotIn("bootstrap-streamscape-media-binary.sh", self.workflow_text)
        self.assertNotIn("Vendor/StreamscapeMediaApple", self.workflow_text)

        execute = self.steps_by_name["Execute fixed repository-owned entrypoint"]["run"]
        self.assertIn('"./${ENTRYPOINT}"', execute)
        self.assertNotIn("--configuration", execute)
        self.assertNotIn("--platform", execute)
        self.assertNotIn("xcodebuild", execute)
        self.assertNotIn("CODE_SIGNING_ALLOWED", execute)
        self.assertNotIn("CODE_SIGNING_REQUIRED", execute)

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

        ui_custom = self.run_dispatch_request(
            "ui-test",
            {
                "host_os": "macos",
                "semantic_inputs": json.dumps(
                    {
                        "product_target": "ios",
                        "test_selectors": ["AppUITests/NavigationRegression/testCredentialFreeRoute"],
                    }
                ),
            },
        )
        self.assertEqual(ui_custom.returncode, 0, ui_custom.stderr)

        ui_injected = self.run_dispatch_request(
            "ui-test",
            {
                "host_os": "macos",
                "semantic_inputs": json.dumps({"test_selectors": ["-only-testing:Injected"]}),
            },
        )
        self.assertNotEqual(ui_injected.returncode, 0)
        self.assertIn("invalid selector", ui_injected.stderr)

        physical = self.run_dispatch_request(
            "device-test",
            {
                "host_os": "macos",
                "semantic_inputs": json.dumps({"device_platform": "ios", "test_selectors": ["physical-core"]}),
            },
        )
        self.assertEqual(physical.returncode, 0, physical.stderr)
        physical_linux = self.run_dispatch_request(
            "device-test",
            {
                "host_os": "linux",
                "semantic_inputs": json.dumps({"device_platform": "ios", "test_selectors": ["physical-core"]}),
            },
        )
        self.assertNotEqual(physical_linux.returncode, 0)
        self.assertIn("device-test requires host_os=macos", physical_linux.stderr)

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
            'profile not in {"build", "test", "full", "ui-test", "device-test"}',
            admission["run"],
        )
        job = self.dispatch["jobs"]["repository"]
        self.assertEqual(
            job["if"],
            "${{ needs.request.outputs.workflow_key == 'validation.repository' }}",
        )
        self.assertEqual(job["uses"], "./.github/workflows/repository-plan.yml")
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
        self.assertEqual(
            self.contract["trustedCapabilityGrant"]["acceptedSchemaVersions"],
            [1, 2, 3],
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

    def test_targeted_linux_gradle_cache_is_bounded_and_secret_safe(self) -> None:
        steps = self.workflow["jobs"]["execute"]["steps"]
        by_name = {step.get("name"): step for step in steps if step.get("name")}
        names = [step.get("name") for step in steps]

        expected_order = (
            "Configure ephemeral generic package-manager authentication",
            "Resolve bounded Gradle cache identity",
            "Restore bounded Gradle user-home cache",
            "Record bounded Gradle cache restoration",
            "Execute fixed repository-owned entrypoint",
            "Prepare bounded Gradle cache save",
            "Save bounded Gradle user-home cache",
            "Cleanup ephemeral registry and repository evidence",
        )
        positions = [names.index(name) for name in expected_order]
        self.assertEqual(positions, sorted(positions))

        identity = by_name["Resolve bounded Gradle cache identity"]
        self.assertIn("inputs.operation == 'test'", identity["if"])
        self.assertIn("needs.resolve_host.outputs.host_os == 'linux'", identity["if"])
        self.assertIn("steps.capabilities.outputs.gradle_maven == 'true'", identity["if"])
        self.assertEqual(
            identity["env"]["OBSERVED_SOURCE_SHA"],
            "${{ steps.source_identity.outputs.source_sha }}",
        )
        self.assertEqual(identity["env"]["SOURCE_REPOSITORY"], "${{ inputs.repository }}")
        identity_script = identity["run"]
        for token in (
            'relative.startswith("gradle/")',
            'relative.endswith((".gradle", ".gradle.kts"))',
            '{"buildSrc", "build-logic", ".ci"}',
            'path.parts[0] == "scripts" and path.parts[1] == "ci"',
            '"gradle/wrapper/gradle-wrapper.properties" not in selected',
            "reviewed 512-file limit",
            "exceeds 1 MiB",
            "reviewed 8 MiB total",
            'hashlib.sha256(b"repository-gradle-cache-v1\\0")',
            'output.write(f"cache_key={prefix}{source_sha}\\n")',
        ):
            self.assertIn(token, identity_script)
        for forbidden in ("inputs.ref", "semantic_inputs_json", "NORMALIZED_INPUTS"):
            self.assertNotIn(forbidden, identity_script)

        restore = by_name["Restore bounded Gradle user-home cache"]
        self.assertEqual(restore["uses"], "actions/cache/restore@v4")
        self.assertEqual(restore["with"]["key"], "${{ steps.gradle_cache_identity.outputs.cache_key }}")
        self.assertIn("${{ steps.gradle_cache_identity.outputs.restore_key }}", restore["with"]["restore-keys"])
        for path in (
            ".gradle/caches/modules-2/files-2.1",
            ".gradle/caches/transforms-*",
            ".gradle/caches/jars-*",
            ".gradle/caches/*/generated-gradle-jars",
            ".gradle/caches/*/kotlin-dsl",
            ".gradle/wrapper/dists",
        ):
            self.assertIn(path, restore["with"]["path"])
        for forbidden in (
            "gradle.properties",
            ".netrc",
            "source/build",
            "REGISTRY_READ_TOKEN",
            ".gradle/caches/build-cache-",
        ):
            self.assertNotIn(forbidden, restore["with"]["path"])

        record = by_name["Record bounded Gradle cache restoration"]
        self.assertEqual(record["env"]["GRADLE_CACHE_HIT"], "${{ steps.gradle_cache_restore.outputs.cache-hit }}")
        self.assertIn("cache_hit=%s", record["run"])
        self.assertIn("fingerprint=%s", record["run"])

        prepare = by_name["Prepare bounded Gradle cache save"]
        self.assertIn("steps.execute_contract.outcome == 'success'", prepare["if"])
        self.assertIn("steps.gradle_cache_restore.outputs.cache-hit != 'true'", prepare["if"])
        self.assertEqual(prepare["env"]["CACHE_SECRET_REGISTRY_READ_TOKEN"], "${{ secrets.REGISTRY_READ_TOKEN }}")
        prepare_script = prepare["run"]
        for token in (
            '"cache exceeds the reviewed 100000-file limit"',
            '"cache exceeds the reviewed 2 GiB limit"',
            '"cache contains a configured credential value"',
            'path.name.endswith(".lock")',
            'path.name == "gc.properties"',
            "save_allowed=",
        ):
            self.assertIn(token, prepare_script)

        save = by_name["Save bounded Gradle user-home cache"]
        self.assertEqual(save["uses"], "actions/cache/save@v4")
        self.assertEqual(save["with"]["key"], "${{ steps.gradle_cache_identity.outputs.cache_key }}")
        self.assertEqual(save["with"]["path"], restore["with"]["path"])
        self.assertIn("steps.gradle_cache_save_prepare.outputs.save_allowed == 'true'", save["if"])

        cleanup = by_name["Cleanup ephemeral registry and repository evidence"]["run"]
        self.assertIn('"${RUNNER_TEMP}/central-registry-auth"', cleanup)
        self.assertNotIn("actions/setup-java", self.workflow_text)

    def test_gradle_cache_identity_tracks_build_inputs_not_product_source(self) -> None:
        identity = self.steps_by_name["Resolve bounded Gradle cache identity"]
        script = identity["run"]

        with tempfile.TemporaryDirectory() as td:
            workspace = Path(td)
            source = workspace / "source"
            auth_home = workspace / "auth-home"
            (source / "gradle/wrapper").mkdir(parents=True)
            (source / "gradle/libs.versions.toml").write_text("kotlin = '2.2.0'\n", encoding="utf-8")
            (source / "gradle/wrapper/gradle-wrapper.properties").write_text(
                "distributionUrl=https://services.gradle.org/distributions/gradle-9.0-bin.zip\n",
                encoding="utf-8",
            )
            (source / "build.gradle.kts").write_text("plugins {}\n", encoding="utf-8")
            (source / ".ci").mkdir()
            (source / ".ci/test.sh").write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
            (source / "scripts/ci").mkdir(parents=True)
            (source / "scripts/ci/run-android-gradle.sh").write_text(
                "#!/usr/bin/env bash\nexit 0\n", encoding="utf-8"
            )
            (source / "src").mkdir()
            (source / "src/Main.kt").write_text("fun main() = Unit\n", encoding="utf-8")
            (auth_home / ".gradle").mkdir(parents=True)
            (auth_home / ".gradle/gradle.properties").write_text("credential-fixture\n", encoding="utf-8")
            subprocess.run(["git", "init", "-q"], cwd=source, check=True)
            subprocess.run(["git", "add", "."], cwd=source, check=True)

            def invoke(source_sha: str) -> dict[str, str]:
                output = workspace / "github-output"
                output.write_text("", encoding="utf-8")
                result = subprocess.run(
                    ["bash", "-c", script],
                    cwd=ROOT,
                    env={
                        **os.environ,
                        "AUTH_HOME": str(auth_home),
                        "OBSERVED_SOURCE_SHA": source_sha,
                        "SOURCE_REPOSITORY": "ExampleOrg/android-application",
                        "GITHUB_WORKSPACE": str(workspace),
                        "GITHUB_OUTPUT": str(output),
                        "RUNNER_OS": "Linux",
                        "RUNNER_ARCH": "X64",
                    },
                    text=True,
                    capture_output=True,
                    check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                return dict(
                    line.split("=", 1)
                    for line in output.read_text(encoding="utf-8").splitlines()
                )

            baseline = invoke("a" * 40)
            (source / "src/Main.kt").write_text('fun main() = println("changed")\n', encoding="utf-8")
            source_only = invoke("b" * 40)
            self.assertEqual(source_only["fingerprint"], baseline["fingerprint"])
            self.assertEqual(source_only["restore_key"], baseline["restore_key"])
            self.assertNotEqual(source_only["cache_key"], baseline["cache_key"])

            output = workspace / "github-output"
            output.write_text("", encoding="utf-8")
            different_repository = subprocess.run(
                ["bash", "-c", script],
                cwd=ROOT,
                env={
                    **os.environ,
                    "AUTH_HOME": str(auth_home),
                    "OBSERVED_SOURCE_SHA": "b" * 40,
                    "SOURCE_REPOSITORY": "ExampleOrg/other-android-application",
                    "GITHUB_WORKSPACE": str(workspace),
                    "GITHUB_OUTPUT": str(output),
                    "RUNNER_OS": "Linux",
                    "RUNNER_ARCH": "X64",
                },
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(different_repository.returncode, 0, different_repository.stderr)
            different_repository_values = dict(
                line.split("=", 1)
                for line in output.read_text(encoding="utf-8").splitlines()
            )
            self.assertNotEqual(
                different_repository_values["restore_key"],
                baseline["restore_key"],
            )

            for relative, replacement in (
                ("build.gradle.kts", 'plugins { id("java") }\n'),
                ("gradle/libs.versions.toml", "kotlin = '2.3.0'\n"),
                (
                    "gradle/wrapper/gradle-wrapper.properties",
                    "distributionUrl=https://services.gradle.org/distributions/gradle-9.1-bin.zip\n",
                ),
                (".ci/test.sh", "#!/usr/bin/env bash\nprintf 'changed\\n'\n"),
                ("scripts/ci/run-android-gradle.sh", "#!/usr/bin/env bash\nprintf 'changed\\n'\n"),
            ):
                target = source / relative
                original = target.read_text(encoding="utf-8")
                target.write_text(replacement, encoding="utf-8")
                changed = invoke("b" * 40)
                self.assertNotEqual(changed["fingerprint"], baseline["fingerprint"], relative)
                target.write_text(original, encoding="utf-8")

    def test_gradle_cache_save_rejects_secret_bytes_and_prunes_locks(self) -> None:
        prepare = self.steps_by_name["Prepare bounded Gradle cache save"]
        script = prepare["run"]

        def invoke(cache_bytes: bytes, *, secret: str) -> tuple[subprocess.CompletedProcess[str], dict[str, str], bool]:
            with tempfile.TemporaryDirectory() as td:
                root = Path(td)
                gradle = root / ".gradle"
                cache_dir = gradle / "caches/modules-2/files-2.1/example/module/1.0"
                cache_dir.mkdir(parents=True)
                (gradle / "gradle.properties").write_text("credential-file-outside-cache\n", encoding="utf-8")
                (cache_dir / "artifact.jar").write_bytes(cache_bytes)
                lock = cache_dir / "artifact.jar.lock"
                lock.write_text("lock\n", encoding="utf-8")
                output = root / "github-output"
                output.write_text("", encoding="utf-8")
                result = subprocess.run(
                    ["bash", "-c", script],
                    cwd=ROOT,
                    env={
                        **os.environ,
                        "AUTH_HOME": str(root),
                        "CACHE_SECRET_SOURCE_TOKEN": secret,
                        "CACHE_SECRET_REGISTRY_READ_TOKEN": "",
                        "GITHUB_OUTPUT": str(output),
                    },
                    text=True,
                    capture_output=True,
                    check=False,
                )
                values = dict(
                    line.split("=", 1)
                    for line in output.read_text(encoding="utf-8").splitlines()
                )
                return result, values, lock.exists()

        unsafe, unsafe_values, _ = invoke(b"prefix-secret-value-suffix", secret="secret-value")
        self.assertEqual(unsafe.returncode, 0, unsafe.stderr)
        self.assertEqual(unsafe_values["save_allowed"], "false")
        self.assertIn("configured credential value", unsafe.stdout)

        safe, safe_values, lock_exists = invoke(b"safe-gradle-cache-bytes", secret="secret-value")
        self.assertEqual(safe.returncode, 0, safe.stderr)
        self.assertEqual(safe_values["save_allowed"], "true")
        self.assertFalse(lock_exists)

        split_secret = b"x" * (1024 * 1024 - 4) + b"secr" + b"et-value"
        split, split_values, _ = invoke(split_secret, secret="secret-value")
        self.assertEqual(split.returncode, 0, split.stderr)
        self.assertEqual(split_values["save_allowed"], "false")
        self.assertIn("configured credential value", split.stdout)

    def test_private_agent_state_capability_grant_is_exactly_bound_and_fail_closed(self) -> None:
        self.assertEqual(
            set(self.contract["capabilityTypes"]),
            {"private_network", "github_git", "registry_netrc", "gradle_maven", "registry_oci_publish", "backup_s3_read", "protected_deployed_conformance", "apple_physical_device"},
        )
        backup_contract = self.contract["trustedCapabilityGrant"]["capabilityConfiguration"]["backup_s3_read"]
        self.assertEqual(backup_contract["projectStateKey"], "repository_ci_backup_s3_read_v1")
        self.assertEqual(backup_contract["schemaVersion"], 1)
        self.assertEqual(
            backup_contract["binding"],
            ["project_key", "ci_run_id", "repository", "ref", "source_is_tag", "source_sha", "operation"],
        )
        physical_contract = self.contract["trustedCapabilityGrant"]["capabilityConfiguration"]["apple_physical_device"]
        self.assertEqual(physical_contract["projectStateKey"], "repository_ci_apple_physical_device_v1")
        self.assertEqual(physical_contract["schemaVersion"], 1)
        self.assertEqual(physical_contract["maximumAuthorizationSeconds"], 86400)
        self.assertEqual(
            physical_contract["binding"],
            ["repository", "ref", "source_is_tag", "source_sha", "operation", "platform", "device_class", "semantic_inputs_sha256"],
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
        self.assertEqual(trusted_values["backup_s3_read"], "false")
        self.assertEqual(trusted_values["protected_deployed_conformance"], "false")
        self.assertEqual(trusted_values["apple_physical_device"], "false")
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
        self.assertEqual(v2_values["backup_s3_read"], "false")
        self.assertEqual(v2_values["protected_deployed_conformance"], "false")
        self.assertEqual(v2_values["apple_physical_device"], "false")
        self.assertEqual(v2_values["auth_enabled"], "true")

        v3, v3_values = self.run_capability_resolver(
            project_state={
                "repository_ci": {
                    "schemaVersion": 3,
                    "repository": "ExampleOrg/apple-application",
                    "capabilities": ["private_network", "github_git"],
                    "hostPolicy": {
                        "operatingSystems": {
                            "linux": {
                                "default": "linux-hosted",
                                "operations": {},
                            },
                            "macos": {
                                "default": "macos-high-capacity",
                                "operations": {},
                            },
                        }
                    },
                }
            }
        )
        self.assertEqual(v3.returncode, 0, v3.stderr)
        self.assertEqual(v3_values["private_network"], "true")
        self.assertEqual(v3_values["github_git"], "true")
        self.assertEqual(v3_values["registry_oci_publish"], "false")
        self.assertEqual(v3_values["backup_s3_read"], "false")
        self.assertEqual(v3_values["protected_deployed_conformance"], "false")
        self.assertEqual(v3_values["apple_physical_device"], "false")
        self.assertEqual(v3_values["auth_enabled"], "true")

        from datetime import datetime, timedelta, timezone
        now = datetime.now(timezone.utc)
        authorized_at = (now - timedelta(minutes=1)).isoformat().replace("+00:00", "Z")
        expires_at = (now + timedelta(hours=1)).isoformat().replace("+00:00", "Z")
        physical_inputs = {"schemaVersion": 1, "inputs": {"device_platform": "ios", "test_selectors": ["physical-core"]}}
        physical_inputs_sha256 = hashlib.sha256(
            json.dumps(physical_inputs, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        physical_state = {
            "repository_ci": {
                "schemaVersion": 3,
                "repository": "ExampleOrg/apple-application",
                "capabilities": ["apple_physical_device"],
                "hostPolicy": {"operatingSystems": {"macos": {"default": "macos-high-capacity", "operations": {}}}},
            },
            "repository_ci_apple_physical_device_v1": {
                "schemaVersion": 1,
                "repository": "ExampleOrg/apple-application",
                "ref": "feature",
                "sourceIsTag": False,
                "sourceSha": "a" * 40,
                "operation": "device-test",
                "platform": "ios",
                "deviceClass": "iphone",
                "semanticInputsSha256": physical_inputs_sha256,
                "authorizedAt": authorized_at,
                "expiresAt": expires_at,
            },
        }
        physical, physical_values = self.run_capability_resolver(
            operation="device-test",
            host_os="macos",
            run_profile="device-test",
            project_state=physical_state,
            normalized_inputs=physical_inputs,
        )
        self.assertEqual(physical.returncode, 0, physical.stderr)
        self.assertEqual(physical_values["apple_physical_device"], "true")
        self.assertEqual(physical_values["apple_physical_device_authorized_at"], authorized_at)
        self.assertEqual(physical_values["apple_physical_device_expires_at"], expires_at)

        changed_packet, _ = self.run_capability_resolver(
            operation="device-test",
            host_os="macos",
            run_profile="device-test",
            project_state=physical_state,
            normalized_inputs={"schemaVersion": 1, "inputs": {"device_platform": "ios", "test_selectors": ["different-packet"]}},
        )
        self.assertNotEqual(changed_packet.returncode, 0)
        self.assertIn("does not match this exact request", changed_packet.stderr)

        wrong_host, _ = self.run_capability_resolver(
            operation="device-test", host_os="macos", host_class="macos-hosted", run_profile="device-test",
            project_state=physical_state,
            normalized_inputs=physical_inputs,
        )
        self.assertNotEqual(wrong_host.returncode, 0)
        self.assertIn("high-capacity macOS host", wrong_host.stderr)

        expired_state = json.loads(json.dumps(physical_state))
        expired_state["repository_ci_apple_physical_device_v1"]["authorizedAt"] = (now - timedelta(hours=2)).isoformat().replace("+00:00", "Z")
        expired_state["repository_ci_apple_physical_device_v1"]["expiresAt"] = (now - timedelta(hours=1)).isoformat().replace("+00:00", "Z")
        expired, _ = self.run_capability_resolver(
            operation="device-test", host_os="macos", run_profile="device-test", project_state=expired_state,
            normalized_inputs=physical_inputs,
        )
        self.assertNotEqual(expired.returncode, 0)
        self.assertIn("not currently active", expired.stderr)

        invalid_v3, _ = self.run_capability_resolver(
            project_state={
                "repository_ci": {
                    "schemaVersion": 3,
                    "repository": "ExampleOrg/apple-application",
                    "capabilities": ["private_network"],
                }
            }
        )
        self.assertNotEqual(invalid_v3.returncode, 0)
        self.assertIn("capability grant version is invalid", invalid_v3.stderr)

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


    def test_backup_s3_read_is_exact_release_run_bound_and_uses_fixed_secret_bundle(self) -> None:
        run_id = "11111111-1111-4111-8111-111111111111"
        source_sha = "b" * 40
        descriptor = {
            "schemaVersion": 1,
            "projectKey": "private-project",
            "ciRunId": run_id,
            "repository": "ExampleOrg/service-backend",
            "ref": "3.2.3",
            "sourceIsTag": True,
            "sourceSha": source_sha,
            "operation": "release",
        }
        state = {
            "repository_ci": {
                "schemaVersion": 1,
                "repository": "ExampleOrg/service-backend",
                "capabilities": ["backup_s3_read"],
            },
            "repository_ci_backup_s3_read_v1": descriptor,
        }
        accepted, values = self.run_capability_resolver(
            run_repository="ExampleOrg/service-backend",
            run_ref="3.2.3",
            source_ref="3.2.3",
            run_is_tag=True,
            source_is_tag="true",
            operation="release",
            host_os="linux",
            run_profile="linux",
            run_workflow="release.repository",
            project_state=state,
            ci_run_id=run_id,
            observed_source_sha=source_sha,
        )
        self.assertEqual(accepted.returncode, 0, accepted.stderr)
        self.assertEqual(values["backup_s3_read"], "true")
        self.assertEqual(values["auth_enabled"], "false")

        build, build_values = self.run_capability_resolver(
            run_repository="ExampleOrg/service-backend",
            operation="build",
            host_os="linux",
            project_state=state,
            ci_run_id=run_id,
            observed_source_sha=source_sha,
        )
        self.assertEqual(build.returncode, 0, build.stderr)
        self.assertEqual(build_values["backup_s3_read"], "false")

        changed_sha, _ = self.run_capability_resolver(
            run_repository="ExampleOrg/service-backend",
            run_ref="3.2.3",
            source_ref="3.2.3",
            run_is_tag=True,
            source_is_tag="true",
            operation="release",
            host_os="linux",
            run_profile="linux",
            run_workflow="release.repository",
            project_state=state,
            ci_run_id=run_id,
            observed_source_sha="c" * 40,
        )
        self.assertNotEqual(changed_sha.returncode, 0)
        self.assertIn("does not match this exact release run", changed_sha.stderr)

        changed_run, _ = self.run_capability_resolver(
            run_repository="ExampleOrg/service-backend",
            run_ref="3.2.3",
            source_ref="3.2.3",
            run_is_tag=True,
            source_is_tag="true",
            operation="release",
            host_os="linux",
            run_profile="linux",
            run_workflow="release.repository",
            project_state=state,
            ci_run_id="22222222-2222-4222-8222-222222222222",
            observed_source_sha=source_sha,
        )
        self.assertNotEqual(changed_run.returncode, 0)
        self.assertIn("does not match this exact release run", changed_run.stderr)

        missing_descriptor, _ = self.run_capability_resolver(
            run_repository="ExampleOrg/service-backend",
            run_ref="3.2.3",
            source_ref="3.2.3",
            run_is_tag=True,
            source_is_tag="true",
            operation="release",
            host_os="linux",
            run_profile="linux",
            run_workflow="release.repository",
            project_state={
                "repository_ci": {
                    "schemaVersion": 1,
                    "repository": "ExampleOrg/service-backend",
                    "capabilities": ["backup_s3_read"],
                }
            },
            ci_run_id=run_id,
            observed_source_sha=source_sha,
        )
        self.assertNotEqual(missing_descriptor.returncode, 0)
        self.assertIn("authorization descriptor is missing or invalid", missing_descriptor.stderr)

        secret_names = (
            "BACKUP_S3_ENDPOINT",
            "BACKUP_S3_BUCKET",
            "BACKUP_S3_REGION",
            "BACKUP_S3_ACCESS_KEY_ID",
            "BACKUP_S3_SECRET_ACCESS_KEY",
        )
        workflow_secrets = self.workflow["on"]["workflow_call"]["secrets"]
        for name in secret_names:
            self.assertIn(name, workflow_secrets)
            self.assertFalse(workflow_secrets[name]["required"])

        release_secrets = self.dispatch["jobs"]["repository_release"]["secrets"]
        validation_secrets = self.dispatch["jobs"]["repository"]["secrets"]
        for name in secret_names:
            self.assertEqual(release_secrets[name], f"${{{{ secrets.{name} }}}}")
            self.assertNotIn(name, validation_secrets)

        plan = yaml.safe_load(
            (ROOT / ".github/workflows/repository-plan.yml").read_text(encoding="utf-8")
        )
        child_secrets = plan["jobs"]["execute"]["secrets"]
        for name in secret_names:
            expression = child_secrets[name]
            self.assertIn("inputs.operation == 'release'", expression)
            self.assertIn(f"secrets.{name}", expression)

        execute = self.steps_by_name["Execute fixed repository-owned entrypoint"]
        self.assertEqual(
            execute["env"]["BACKUP_S3_READ_ENABLED"],
            "${{ steps.capabilities.outputs.backup_s3_read }}",
        )
        self.assertIn("steps.capabilities.outputs.backup_s3_read != 'true'", execute["env"]["CHECKPOINT_ENABLED"])
        for name in secret_names:
            secret_env = f"CI_SECRET_{name}"
            self.assertIn(secret_env, execute["env"])
            self.assertIn("steps.capabilities.outputs.backup_s3_read == 'true'", execute["env"][secret_env])
            self.assertIn(secret_env, execute["run"])
            self.assertIn(secret_env, self.steps_by_name["Scrub configured CI secrets from private text evidence"]["env"])
        for env_name in (
            "CI_BACKUP_S3_ENDPOINT",
            "CI_BACKUP_S3_BUCKET",
            "CI_BACKUP_S3_REGION",
            "CI_BACKUP_S3_ACCESS_KEY_ID",
            "CI_BACKUP_S3_SECRET_ACCESS_KEY",
        ):
            self.assertIn(f'{env_name}="${{backup_s3_', execute["run"])
            self.assertNotIn(env_name, execute["env"])
        self.assertIn("reviewed backup S3 read capability is missing its fixed connection bundle", execute["run"])
        self.assertIn(
            "unset backup_s3_endpoint backup_s3_bucket backup_s3_region backup_s3_access_key_id backup_s3_secret_access_key",
            execute["run"],
        )

    def test_backup_s3_secret_is_scrubbed_and_secret_artifact_is_quarantined(self) -> None:
        scrub = self.steps_by_name["Scrub configured CI secrets from private text evidence"]["run"]
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            log_dir = root / "logs"
            artifact_dir = root / "artifacts"
            log_dir.mkdir()
            artifact_dir.mkdir()
            secret = "backup-secret-value"
            ci_log = root / "central.log"
            ci_log.write_text(f"restore credential={secret}\n", encoding="utf-8")
            progress = root / "progress.txt"
            progress.write_text("running\n", encoding="utf-8")
            secret_artifact = artifact_dir / "unsafe.json"
            secret_artifact.write_text(json.dumps({"credential": secret}), encoding="utf-8")
            safe_artifact = artifact_dir / "safe.json"
            safe_artifact.write_text('{"status":"ok"}\n', encoding="utf-8")
            github_output = root / "github-output"
            github_output.write_text("", encoding="utf-8")
            result = subprocess.run(
                ["bash", "-c", scrub],
                cwd=ROOT,
                env={
                    **os.environ,
                    "RUNNER_TEMP": str(root),
                    "CI_LOG": str(ci_log),
                    "CI_PROGRESS_FILE": str(progress),
                    "CI_LOG_DIR": str(log_dir),
                    "CI_ARTIFACT_DIR": str(artifact_dir),
                    "CI_SECRET_BACKUP_S3_SECRET_ACCESS_KEY": secret,
                    "GITHUB_OUTPUT": str(github_output),
                },
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn(secret, ci_log.read_text(encoding="utf-8"))
            self.assertIn("[REDACTED]", ci_log.read_text(encoding="utf-8"))
            selection = json.loads((root / "central-repository-ci-artifact-selection.json").read_text(encoding="utf-8"))
            selected = [item["path"] for item in selection["artifacts"]]
            self.assertEqual(selected, ["safe.json"])

    def test_protected_deployed_conformance_is_full_linux_private_network_only(self) -> None:
        grant = {
            "repository_ci": {
                "schemaVersion": 1,
                "repository": "ExampleOrg/service-backend",
                "capabilities": ["private_network", "protected_deployed_conformance"],
            }
        }
        full, full_values = self.run_capability_resolver(
            run_repository="ExampleOrg/service-backend",
            operation="full",
            host_os="linux",
            project_state=grant,
        )
        self.assertEqual(full.returncode, 0, full.stderr)
        self.assertEqual(full_values["private_network"], "true")
        self.assertEqual(full_values["protected_deployed_conformance"], "true")
        self.assertEqual(full_values["auth_enabled"], "false")
        self.assertEqual(full_values["project_key"], "private-project")

        build, build_values = self.run_capability_resolver(
            run_repository="ExampleOrg/service-backend",
            operation="build",
            host_os="linux",
            project_state=grant,
        )
        self.assertEqual(build.returncode, 0, build.stderr)
        self.assertEqual(build_values["protected_deployed_conformance"], "false")

        macos, macos_values = self.run_capability_resolver(
            run_repository="ExampleOrg/service-backend",
            operation="full",
            host_os="macos",
            project_state=grant,
        )
        self.assertEqual(macos.returncode, 0, macos.stderr)
        self.assertEqual(macos_values["protected_deployed_conformance"], "false")

        missing_network, _ = self.run_capability_resolver(
            run_repository="ExampleOrg/service-backend",
            operation="full",
            host_os="linux",
            project_state={
                "repository_ci": {
                    "schemaVersion": 1,
                    "repository": "ExampleOrg/service-backend",
                    "capabilities": ["protected_deployed_conformance"],
                }
            },
        )
        self.assertNotEqual(missing_network.returncode, 0)
        self.assertIn("requires the reviewed private_network capability", missing_network.stderr)

        self.assertNotIn("Materialize protected deployed conformance bundle", self.steps_by_name)
        for secret_name in (
            "PROTECTED_DEPLOYED_HTTPS_TARGET",
            "PROTECTED_DEPLOYED_WSS_TARGET",
            "PROTECTED_DEPLOYED_HTTP_SCENARIO_GZIP_BASE64",
            "PROTECTED_DEPLOYED_WEBSOCKET_SCENARIO_GZIP_BASE64",
            "PROTECTED_DEPLOYED_SETUP_CREDENTIAL",
        ):
            self.assertIn(secret_name, self.workflow["on"]["workflow_call"]["secrets"])

        execute = self.steps_by_name["Execute fixed repository-owned entrypoint"]
        script = execute["run"]
        self.assertIn("CI_PROTECTED_DEPLOYED_CONFORMANCE", script)
        self.assertIn("get_project_state", script)
        self.assertIn("protected_deployed_conformance.py materialize", script)
        self.assertIn("protected_deployed_conformance.py run", script)
        self.assertIn("--source-root .", script)
        self.assertIn('--artifact-dir "${CI_ARTIFACT_DIR}"', script)
        self.assertNotIn("jq -r '.ok'", script)
        self.assertLess(script.index('"./${ENTRYPOINT}"'), script.index("get_project_state"))
        self.assertLess(script.index("get_project_state"), script.index("protected_deployed_conformance.py materialize"))
        self.assertLess(script.index("protected_deployed_conformance.py materialize"), script.index("protected_deployed_conformance.py run"))
        self.assertNotIn("PROTECTED_DEPLOYED_HTTPS_TARGET", execute["env"])
        self.assertNotIn("PROTECTED_DEPLOYED_SETUP_CREDENTIAL", execute["env"])
        self.assertIn("CI_SECRET_PROTECTED_DEPLOYED_HTTPS_TARGET", execute["env"])
        self.assertIn("CI_SECRET_PROTECTED_DEPLOYED_SETUP_CREDENTIAL", execute["env"])
        self.assertIn("steps.capabilities.outputs.protected_deployed_conformance == 'true'", execute["env"]["CI_SECRET_PROTECTED_DEPLOYED_HTTPS_TARGET"])
        self.assertIn("steps.capabilities.outputs.protected_deployed_conformance == 'true'", execute["env"]["CI_SECRET_PROTECTED_DEPLOYED_SETUP_CREDENTIAL"])
        unset_index = script.index('unset \\')
        self.assertLess(unset_index, script.index('"./${ENTRYPOINT}"'))
        self.assertGreater(script.index('CI_SECRET_PROTECTED_DEPLOYED_HTTPS_TARGET', unset_index), unset_index)
        self.assertLess(script.index('CI_SECRET_PROTECTED_DEPLOYED_HTTPS_TARGET', unset_index), script.index('"./${ENTRYPOINT}"'))

        scrub = self.steps_by_name["Scrub configured CI secrets from private text evidence"]["env"]
        self.assertIn("CI_SECRET_PROTECTED_DEPLOYED_HTTPS_TARGET", scrub)
        self.assertIn("CI_SECRET_PROTECTED_DEPLOYED_SETUP_CREDENTIAL", scrub)
        cleanup = self.steps_by_name["Cleanup ephemeral registry and repository evidence"]["run"]
        self.assertIn("central-protected-deployed-conformance", cleanup)
        self.assertIn("central-protected-deployed-project-state.json", cleanup)

        plan = yaml.safe_load(
            (ROOT / ".github/workflows/repository-plan.yml").read_text(encoding="utf-8")
        )
        child_secrets = plan["jobs"]["execute"]["secrets"]
        for secret_name in (
            "PROTECTED_DEPLOYED_HTTPS_TARGET",
            "PROTECTED_DEPLOYED_WSS_TARGET",
            "PROTECTED_DEPLOYED_HTTP_SCENARIO_GZIP_BASE64",
            "PROTECTED_DEPLOYED_WEBSOCKET_SCENARIO_GZIP_BASE64",
            "PROTECTED_DEPLOYED_SETUP_CREDENTIAL",
        ):
            self.assertIn("matrix.host_os == 'linux'", child_secrets[secret_name])

        validation_secrets = self.dispatch["jobs"]["repository"]["secrets"]
        release_secrets = self.dispatch["jobs"]["repository_release"]["secrets"]
        self.assertIn("PROTECTED_DEPLOYED_HTTPS_TARGET", validation_secrets)
        self.assertNotIn("PROTECTED_DEPLOYED_HTTPS_TARGET", release_secrets)

    def test_protected_deployed_project_state_uses_actual_envelope_after_product_success(self) -> None:
        script = self.steps_by_name["Execute fixed repository-owned entrypoint"]["run"]
        self.assertIn('value.get("project_key") != os.environ["EXPECTED_PROJECT"]', script)
        self.assertIn('not isinstance(state, dict)', script)
        self.assertNotIn("jq -r '.ok'", script)
        self.assertIn('PROJECT_RESPONSE="${project_response}"', script)
        self.assertLess(script.index('"./${ENTRYPOINT}"'), script.index('project_response="$(curl'))
        self.assertIn('rm -f -- "${protected_state_file}"', script)

    def test_execute_step_shell_parses_with_protected_project_state_heredoc(self) -> None:
        script = self.steps_by_name["Execute fixed repository-owned entrypoint"]["run"]
        result = subprocess.run(
            ["bash", "-n"],
            input=script,
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("\nPY_PROTECTED_STATE\n", script)

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

        macos_release, macos_values = self.run_capability_resolver(
            run_repository="ExampleOrg/service-backend",
            run_ref="3.2.3",
            source_ref="3.2.3",
            run_is_tag=True,
            source_is_tag="true",
            operation="release",
            host_os="macos",
            run_profile="macos",
            run_workflow="release.repository",
            project_state=grant,
        )
        self.assertEqual(macos_release.returncode, 0, macos_release.stderr)
        self.assertEqual(macos_values["private_network"], "true")
        self.assertEqual(macos_values["registry_oci_publish"], "false")

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
        self.assertEqual(
            release_secrets["REGISTRY_WRITE_TOKEN"],
            "${{ secrets.FORGEJO_REGISTRY_TOKEN }}",
        )
        self.assertNotIn("REGISTRY_WRITE_TOKEN", validation_secrets)

        plan = yaml.safe_load(
            (ROOT / ".github/workflows/repository-plan.yml").read_text(encoding="utf-8")
        )
        child_secrets = plan["jobs"]["execute"]["secrets"]
        write_expression = child_secrets["REGISTRY_WRITE_TOKEN"]
        self.assertIn("matrix.host_os == 'linux'", write_expression)
        self.assertIn("secrets.REGISTRY_WRITE_TOKEN", write_expression)
        for secret_name in (
            "APPLE_TEAM_ID",
            "APP_STORE_CONNECT_KEY_ID",
            "APP_STORE_CONNECT_ISSUER_ID",
            "APP_STORE_CONNECT_API_KEY_P8_BASE64",
        ):
            expression = child_secrets[secret_name]
            self.assertIn("matrix.host_os == 'macos'", expression)
            self.assertIn(f"secrets.{secret_name}", expression)

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

        planned_validation_child, _ = self.run_repository_request(
            operation="build",
            source_is_tag="false",
            trusted_capability_ci_run_id=trusted_id,
        )
        self.assertEqual(planned_validation_child.returncode, 0, planned_validation_child.stderr)

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
        self.assertEqual(values["backup_s3_read"], "false")
        self.assertEqual(values["protected_deployed_conformance"], "false")
        self.assertEqual(values["apple_physical_device"], "false")
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
            "trusted capability parent is invalid",
            wrong_parent.stderr,
        )

    def test_device_test_uses_generic_repository_entrypoint_and_private_device_wrapper(self) -> None:
        request = self.steps_by_name["Validate bounded repository operation"]
        self.assertIn("device-test) entrypoint=.ci/device-test.sh", request["run"])
        execute = self.steps_by_name["Execute fixed repository-owned entrypoint"]
        script = execute["run"]
        self.assertIn("repository_apple_physical_device.py execute", script)
        self.assertIn('if test "${OPERATION}" = device-test', script)
        self.assertIn("repository device-test requires the reviewed Apple physical-device capability", script)
        self.assertNotIn("ios-central-device-adapter.py", self.workflow_text)
        self.assertNotIn("run-ios-device-packet", self.workflow_text)
        self.assertNotIn("STREAMSCAPE_APPLE_HARDWARE_UDID", self.workflow_text)
        self.assertNotIn("device_identifier", self.workflow["on"]["workflow_call"]["inputs"])
        self.assertIn("inputs.operation != 'device-test'", execute["env"]["CHECKPOINT_ENABLED"])

        scrub = self.steps_by_name["Scrub configured CI secrets from private text evidence"]
        self.assertIn("central-apple-physical-device-secrets.txt", scrub["env"]["CI_DYNAMIC_SECRET_FILE"])
        self.assertIn("dynamic_secret_path", scrub["run"])
        cleanup = self.steps_by_name["Cleanup ephemeral registry and repository evidence"]["run"]
        self.assertIn("central-apple-physical-device-context.json", cleanup)
        self.assertIn("central-apple-physical-device-secrets.txt", cleanup)
        self.assertIn("central-apple-physical-device-inventory.json", cleanup)
        self.assertIn("repository CI physical-device private residue cleanup failed", cleanup)
        self.assertIn('test -e "${private_device_path}" || test -L "${private_device_path}"', cleanup)

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
        stable_name = "${{ github.run_id }}-${{ github.run_attempt }}-repository-${{ inputs.operation }}-${{ needs.resolve_host.outputs.host_os }}.txt"
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
        checkpoint_start = script.index("central-ci/scripts/ci/repository_log_checkpoint.py")
        checkpoint_end = script.index("checkpoint_pid=$!", checkpoint_start)
        checkpoint_command = script[checkpoint_start:checkpoint_end]
        self.assertNotIn("--repository", checkpoint_command)
        self.assertNotIn("--file-name", checkpoint_command)
        self.assertNotIn("--folder-id", checkpoint_command)
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

    def test_macos_repository_test_without_runtime_probe_keeps_fixed_entrypoint_reachable(self) -> None:
        valid, output = self.run_repository_request(
            operation="test",
            host_os="macos",
            semantic={
                "product_target": "ios",
                "test_selectors": [
                    "streamscapetvTests/AboutLicensesProductionPathIntegrationTests"
                ],
            },
        )
        self.assertEqual(valid.returncode, 0, valid.stderr)
        self.assertIn("entrypoint=.ci/test.sh\n", output)

        execute = self.steps_by_name["Execute fixed repository-owned entrypoint"]["run"]
        self.assertIn(
            'if test "${HOST_OS}" = macos && test "${OPERATION}" = ui-test; then',
            execute,
        )
        self.assertIn("runtime_probe_options=()", execute)
        self.assertIn(
            '"${runtime_probe_options[@]+"${runtime_probe_options[@]}"}" &',
            execute,
        )
        self.assertIn('"./${ENTRYPOINT}"', execute)

    def test_macos_ui_runtime_probe_is_central_owned_and_bounded(self) -> None:
        execute = self.steps_by_name["Execute fixed repository-owned entrypoint"]["run"]
        self.assertIn(
            'if test "${HOST_OS}" = macos && test "${OPERATION}" = ui-test; then',
            execute,
        )
        self.assertIn(
            'runtime_probe_path="${CI_LOG_DIR}/central-runtime-probe.txt"',
            execute,
        )
        self.assertIn("repository_runtime_probe.py run", execute)
        self.assertIn("--runtime-probe-path", execute)
        self.assertIn("repository_runtime_probe.py summary", execute)
        self.assertNotIn("CI_RUNTIME_PROBE", self.workflow["on"]["workflow_call"]["inputs"])

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
            self.assertEqual(release_secrets[name], f"${{{{ secrets.{name} }}}}")
            self.assertNotIn(name, validation_secrets)

        plan = yaml.safe_load(
            (ROOT / ".github/workflows/repository-plan.yml").read_text(encoding="utf-8")
        )
        child_secrets = plan["jobs"]["execute"]["secrets"]
        for name in secret_names:
            expression = child_secrets[name]
            self.assertIn("matrix.host_os == 'macos'", expression)
            self.assertIn(f"secrets.{name}", expression)

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
            inventory["workflows"]["repository_plan"],
            ".github/workflows/repository-plan.yml",
        )
        self.assertEqual(
            inventory["contracts"]["repository_ci_v1"],
            "contracts/repository-ci-v1.json",
        )
        self.assertEqual(
            inventory["scripts"]["repository_execution_plan"],
            "scripts/ci/repository_execution_plan.py",
        )
        self.assertEqual(
            inventory["scripts"]["repository_log_timeline"],
            "scripts/ci/repository_log_timeline.py",
        )
        self.assertEqual(
            inventory["scripts"]["repository_runtime_probe"],
            "scripts/ci/repository_runtime_probe.py",
        )
        self.assertEqual(
            inventory["scripts"]["protected_deployed_conformance"],
            "scripts/ci/protected_deployed_conformance.py",
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
