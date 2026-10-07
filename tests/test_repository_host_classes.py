from __future__ import annotations

import json
import os
import re
from pathlib import Path
import subprocess
import tempfile
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/repository.yml"
CONTRACT = ROOT / "contracts/repository-ci-v1.json"
DOC = ROOT / "docs/repository-ci.md"


class RepositoryHostClassTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
        cls.contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
        cls.doc = DOC.read_text(encoding="utf-8")
        cls.resolve = cls.workflow["jobs"]["resolve_host"]
        cls.script = cls.resolve["steps"][0]["run"]

    def run_resolver(
        self,
        *,
        host_class: str = "linux-hosted",
        operation: str = "full",
        host_os: str = "linux",
        workflow_key: str = "validation.repository",
        test_profile: str | None = None,
        source_is_tag: str = "false",
        lifecycle_ci_run_id: str = "11111111-1111-4111-8111-111111111111",
        trusted_capability_ci_run_id: str = "",
        resolver_ok: bool = True,
        resolver_code: str = "ok",
        repository: str = "ExampleOrg/example-repository",
        run_repository: str | None = None,
        run_host_os: str | None = None,
    ):
        test_profile = test_profile or operation
        run_repository = run_repository or repository
        run_host_os = host_os if run_host_os is None else run_host_os
        project_key = "fixture-project"
        claim = {
            "ok": True,
            "code": "ok",
            "replayed": True,
            "run": {
                "project_key": project_key,
                "repository": run_repository,
                "ref": "feature-host-policy",
                "is_tag": source_is_tag == "true",
                "workflow_key": workflow_key,
                "test_profile": test_profile,
                "inputs": {"host_os": run_host_os},
            },
        }
        selected_id = trusted_capability_ci_run_id or lifecycle_ci_run_id
        resolution = {
            "ok": resolver_ok,
            "code": resolver_code,
            "replayed": False,
            "project_key": project_key,
            "repository": repository,
            "ci_run_id": selected_id,
            "operation": operation,
            "host_os": host_os,
            "host_class": host_class,
        }

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            fake_bin = root / "bin"
            fake_bin.mkdir()
            curl = fake_bin / "curl"
            curl.write_text(
                "#!/usr/bin/env python3\n"
                "import json, sys\n"
                f"claim = {claim!r}\n"
                f"resolution = {resolution!r}\n"
                "url = sys.argv[-1]\n"
                "if url.endswith('/claim_ci_run'):\n"
                "    print(json.dumps(claim))\n"
                "elif url.endswith('/resolve_repository_ci_host_class') or url.endswith('/resolve_repository_ci_host_class_for_os'):\n"
                "    print(json.dumps(resolution))\n"
                "else:\n"
                "    raise SystemExit(97)\n",
                encoding="utf-8",
            )
            curl.chmod(0o755)
            output = root / "github-output"
            output.write_text("", encoding="utf-8")
            result = subprocess.run(
                ["bash", "-c", self.script],
                cwd=ROOT,
                env={
                    **os.environ,
                    "PATH": f"{fake_bin}:{os.environ['PATH']}",
                    "LIFECYCLE_CI_RUN_ID": lifecycle_ci_run_id,
                    "TRUSTED_CAPABILITY_CI_RUN_ID": trusted_capability_ci_run_id,
                    "SOURCE_REPOSITORY": repository,
                    "SOURCE_REF": "feature-host-policy",
                    "SOURCE_IS_TAG": source_is_tag,
                    "OPERATION": operation,
                    "LEGACY_HOST_OS": host_os,
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

    def test_public_catalog_is_finite_and_caller_cannot_select_host_class(self) -> None:
        inputs = self.workflow["on"]["workflow_call"]["inputs"]
        self.assertNotIn("host_class", inputs)
        self.assertNotIn("runner", inputs)
        self.assertNotIn("runner_label", inputs)
        host_classes = self.contract["adoption"]["hostClasses"]
        self.assertEqual(
            list(host_classes),
            ["linux-hosted", "linux-high-capacity", "macos-hosted", "macos-high-capacity"],
        )
        self.assertFalse(self.contract["adoption"]["hostSelection"]["callerSuppliesHostClass"])
        self.assertFalse(self.contract["adoption"]["hostSelection"]["callerSuppliesRunnerLabels"])
        self.assertEqual(
            self.contract["adoption"]["hostSelection"]["resolver"],
            "resolve_repository_ci_host_class(text,text,uuid,text)",
        )
        self.assertEqual(
            self.contract["adoption"]["hostSelection"]["plannedChildResolver"],
            "resolve_repository_ci_host_class_for_os(text,text,uuid,text,text)",
        )

    def test_execute_job_uses_only_resolver_output(self) -> None:
        execute = self.workflow["jobs"]["execute"]
        self.assertEqual(execute["needs"], "resolve_host")
        self.assertEqual(
            execute["runs-on"],
            "${{ fromJSON(needs.resolve_host.outputs.runs_on) }}",
        )
        for step in execute["steps"]:
            env = step.get("env", {})
            if "HOST_OS" in env:
                self.assertEqual(env["HOST_OS"], "${{ needs.resolve_host.outputs.host_os }}")
        entry = next(
            step for step in execute["steps"]
            if step.get("name") == "Execute fixed repository-owned entrypoint"
        )
        self.assertEqual(entry["env"]["HOST_CLASS"], "${{ needs.resolve_host.outputs.host_class }}")
        self.assertIn('export CI_HOST_CLASS="${HOST_CLASS}"', entry["run"])

    def test_all_reviewed_host_classes_map_to_expected_host_os_and_central_runner(self) -> None:
        cases = {
            "linux-hosted": ("linux", ["ubuntu-latest"]),
            "linux-high-capacity": ("linux", ["self-hosted", "linux", "x64", "ubuntu-latest-xl"]),
            "macos-hosted": ("macos", ["macos-latest"]),
            "macos-high-capacity": ("macos", ["macOS", "ARM64"]),
        }
        for host_class, (expected_os, expected_runs_on) in cases.items():
            with self.subTest(host_class=host_class):
                result, values = self.run_resolver(host_class=host_class)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(values["host_class"], host_class)
                self.assertEqual(values["host_os"], expected_os)
                self.assertEqual(json.loads(values["runs_on"]), expected_runs_on)

    def test_linux_high_capacity_runtime_assertion_is_self_hosted_linux_x64_only(self) -> None:
        execute = self.workflow["jobs"]["execute"]
        step = next(
            item for item in execute["steps"]
            if item.get("name") == "Assert reviewed Linux high-capacity runner"
        )
        self.assertEqual(
            step["if"],
            "${{ needs.resolve_host.outputs.host_class == 'linux-high-capacity' }}",
        )
        self.assertEqual(step["env"]["RUNNER_ENVIRONMENT"], "${{ runner.environment }}")
        self.assertEqual(step["env"]["RUNNER_OS"], "${{ runner.os }}")
        self.assertEqual(step["env"]["RUNNER_ARCH"], "${{ runner.arch }}")
        for token in (
            '"${RUNNER_ENVIRONMENT}" = self-hosted',
            '"${RUNNER_OS}" = Linux',
            '"${RUNNER_ARCH}" = X64',
            "ubuntu-latest-xl selector",
        ):
            self.assertIn(token, step["run"])
        self.assertNotIn("runner.name", json.dumps(step))

    def test_persistent_workspace_cleanup_is_terminally_accounted(self) -> None:
        execute = self.workflow["jobs"]["execute"]
        step = next(
            item for item in execute["steps"]
            if item.get("name") == "Cleanup persistent runner workspace"
        )
        self.assertEqual(step["if"], "${{ always() }}")
        self.assertIn('"${GITHUB_WORKSPACE}/source"', step["run"])
        self.assertIn('"${GITHUB_WORKSPACE}/central-ci"', step["run"])
        self.assertIn("persistent workspace cleanup failed", step["run"])
        finish = next(
            item for item in execute["steps"]
            if item.get("name") == "Finish Agent State run"
        )
        self.assertIn("steps.workspace_cleanup.outcome == 'success'", finish["with"]["status"])

    def test_unknown_or_failed_agent_state_resolution_fails_closed(self) -> None:
        unknown, _ = self.run_resolver(host_class="arbitrary-runner")
        self.assertNotEqual(unknown.returncode, 0)
        self.assertIn("unsupported host class", unknown.stderr)

        denied, _ = self.run_resolver(
            resolver_ok=False,
            resolver_code="repository_mismatch",
        )
        self.assertNotEqual(denied.returncode, 0)
        self.assertIn("repository_mismatch", denied.stderr)

        mismatch, _ = self.run_resolver(run_repository="ExampleOrg/other")
        self.assertNotEqual(mismatch.returncode, 0)
        self.assertIn("not bound to the exact Agent State request", mismatch.stderr)

        host_mismatch, _ = self.run_resolver(host_os="linux", run_host_os="macos")
        self.assertNotEqual(host_mismatch.returncode, 0)
        self.assertIn("not bound to the exact Agent State request", host_mismatch.stderr)

    def test_missing_project_config_uses_only_reviewed_hosted_compatibility(self) -> None:
        linux, linux_values = self.run_resolver(
            host_os="linux",
            resolver_ok=False,
            resolver_code="repository_ci_not_configured",
        )
        self.assertEqual(linux.returncode, 0, linux.stderr)
        self.assertEqual(linux_values["host_class"], "linux-hosted")
        self.assertEqual(json.loads(linux_values["runs_on"]), ["ubuntu-latest"])

        macos, macos_values = self.run_resolver(
            host_os="macos",
            resolver_ok=False,
            resolver_code="repository_ci_not_configured",
        )
        self.assertEqual(macos.returncode, 0, macos.stderr)
        self.assertEqual(macos_values["host_class"], "macos-hosted")
        self.assertEqual(json.loads(macos_values["runs_on"]), ["macos-latest"])
        self.assertNotEqual(macos_values["host_class"], "macos-high-capacity")

    def test_release_resolution_is_bound_to_legacy_profile_but_policy_selects_runner(self) -> None:
        result, values = self.run_resolver(
            host_class="macos-high-capacity",
            operation="release",
            host_os="macos",
            workflow_key="release.repository",
            test_profile="macos",
            source_is_tag="true",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(values["host_class"], "macos-high-capacity")
        self.assertEqual(values["host_os"], "macos")

    def test_library_package_parent_resolves_children_through_repository_host_policy(self) -> None:
        trusted_id = "22222222-2222-4222-8222-222222222222"
        cases = (
            ("full", "macos", "macos-high-capacity", ["macOS", "ARM64"]),
            ("release", "macos", "macos-high-capacity", ["macOS", "ARM64"]),
            ("full", "linux", "linux-hosted", ["ubuntu-latest"]),
        )
        for operation, host_os, host_class, expected_runs_on in cases:
            with self.subTest(operation=operation, host_os=host_os):
                result, values = self.run_resolver(
                    host_class=host_class,
                    operation=operation,
                    host_os=host_os,
                    workflow_key="release.library-package",
                    test_profile="publish",
                    source_is_tag="true",
                    lifecycle_ci_run_id="",
                    trusted_capability_ci_run_id=trusted_id,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(values["host_class"], host_class)
                self.assertEqual(values["host_os"], host_os)
                self.assertEqual(json.loads(values["runs_on"]), expected_runs_on)

        unsupported, _ = self.run_resolver(
            operation="build",
            host_os="macos",
            workflow_key="release.library-package",
            test_profile="publish",
            source_is_tag="true",
            lifecycle_ci_run_id="",
            trusted_capability_ci_run_id=trusted_id,
        )
        self.assertNotEqual(unsupported.returncode, 0)
        self.assertIn(
            "supports only full readiness or release preparation",
            unsupported.stderr,
        )
        self.assertIn("resolve_repository_ci_host_class_for_os", self.script)


    def test_repository_parent_resolves_selected_child_os_through_service_resolver(self) -> None:
        trusted_id = "22222222-2222-4222-8222-222222222222"
        result, values = self.run_resolver(
            host_class="macos-high-capacity",
            operation="full",
            host_os="macos",
            workflow_key="validation.repository",
            test_profile="full",
            lifecycle_ci_run_id="",
            trusted_capability_ci_run_id=trusted_id,
            run_host_os="linux",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(values["host_class"], "macos-high-capacity")
        self.assertEqual(values["host_os"], "macos")
        self.assertIn("resolve_repository_ci_host_class_for_os", self.script)

    def test_hosted_runner_images_use_rolling_latest_aliases(self) -> None:
        numeric_ubuntu = re.compile(r"\bubuntu-[0-9]+\.[0-9]+\b")
        numeric_macos = re.compile(r"\bmacos-[0-9]+\b")
        for path in sorted((ROOT / ".github/workflows").glob("*.yml")):
            workflow_text = path.read_text(encoding="utf-8")
            self.assertIsNone(
                numeric_ubuntu.search(workflow_text),
                f"{path.name} reintroduced a numerically pinned GitHub-hosted Ubuntu image",
            )
            self.assertIsNone(
                numeric_macos.search(workflow_text),
                f"{path.name} reintroduced a numerically pinned GitHub-hosted macOS image",
            )

        self.assertIn('runs_on=\'["ubuntu-latest"]\'', self.script)
        self.assertIn('runs_on=\'["self-hosted","linux","x64","ubuntu-latest-xl"]\'', self.script)
        self.assertIn('runs_on=\'["macos-latest"]\'', self.script)
        self.assertIn('runs_on=\'["macOS","ARM64"]\'', self.script)

    def test_docs_use_generic_non_identifying_policy_examples(self) -> None:
        for value in (
            "linux-hosted",
            "linux-high-capacity",
            "macos-hosted",
            "macos-high-capacity",
            "resolve_repository_ci_host_class",
            "CI_HOST_CLASS",
            "private_network",
        ):
            self.assertIn(value, self.doc)
        self.assertIn("organization/example-native-library", self.doc)
        self.assertNotIn("StreamScapeTV/", self.doc)
        self.assertNotIn("self-hosted machine name", self.doc)


if __name__ == "__main__":
    unittest.main()
