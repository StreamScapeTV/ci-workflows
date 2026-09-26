from __future__ import annotations

import json
from pathlib import Path
import subprocess
import tempfile
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/ci/repository_execution_plan.py"
WORKFLOW = ROOT / ".github/workflows/repository-plan.yml"
CHILD = ROOT / ".github/workflows/repository.yml"


class RepositoryExecutionPlanTests(unittest.TestCase):
    def run_plan(self, *, operation: str = "test", host_os: str = "linux", plan: object | None = None, raw: str | None = None):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            command = [
                "python3",
                str(SCRIPT),
                "--operation",
                operation,
                "--legacy-host-os",
                host_os,
            ]
            if plan is not None or raw is not None:
                path = root / "execution-plan.json"
                path.write_text(raw if raw is not None else json.dumps(plan), encoding="utf-8")
                command += ["--plan", str(path)]
            return subprocess.run(command, cwd=ROOT, text=True, capture_output=True, check=False)

    def test_missing_plan_preserves_one_legacy_host_only(self) -> None:
        result = self.run_plan(operation="full", host_os="macos")
        self.assertEqual(result.returncode, 0, result.stderr)
        value = json.loads(result.stdout)
        self.assertEqual(value["source"], "legacy-host-os-compatibility")
        self.assertEqual(value["operatingSystems"], ["macos"])

    def test_tracked_plan_selects_two_reviewed_operating_systems(self) -> None:
        result = self.run_plan(
            operation="release",
            host_os="linux",
            plan={
                "schemaVersion": 1,
                "operations": {
                    "build": ["linux"],
                    "release": ["linux", "macos"],
                },
            },
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        value = json.loads(result.stdout)
        self.assertEqual(value["source"], "tracked-repository-plan")
        self.assertEqual(value["operatingSystems"], ["linux", "macos"])

    def test_plan_fails_closed_on_unknown_duplicate_or_missing_operation(self) -> None:
        cases = [
            ({"schemaVersion": 1, "operations": {"test": ["linux", "windows"]}}, "unknown operating system"),
            ({"schemaVersion": 1, "operations": {"test": ["linux", "linux"]}}, "duplicate operating system"),
            ({"schemaVersion": 1, "operations": {"build": ["linux"]}}, "does not declare"),
            ({"schemaVersion": 1, "operations": {"arbitrary": ["linux"]}}, "unknown operation"),
        ]
        for plan, message in cases:
            with self.subTest(plan=plan):
                result = self.run_plan(plan=plan)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(message, result.stderr)

    def test_plan_rejects_ambiguous_or_unbounded_json(self) -> None:
        duplicate = self.run_plan(raw='{"schemaVersion":1,"schemaVersion":1,"operations":{"test":["linux"]}}')
        self.assertNotEqual(duplicate.returncode, 0)
        self.assertIn("unambiguous", duplicate.stderr)

        extra = self.run_plan(plan={"schemaVersion": 1, "operations": {"test": ["linux"]}, "runner": "self-hosted"})
        self.assertNotEqual(extra.returncode, 0)
        self.assertIn("top-level schema", extra.stderr)

        oversized = self.run_plan(raw=" " * 9000)
        self.assertNotEqual(oversized.returncode, 0)
        self.assertIn("size is outside", oversized.stderr)

    def test_parent_workflow_owns_one_lifecycle_and_fans_out_fixed_child(self) -> None:
        workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
        jobs = workflow["jobs"]
        self.assertEqual(jobs["execute"]["uses"], "./.github/workflows/repository.yml")
        self.assertFalse(jobs["execute"]["strategy"]["fail-fast"])
        self.assertEqual(
            jobs["execute"]["strategy"]["matrix"],
            "${{ fromJSON(needs.plan.outputs.execution_matrix) }}",
        )
        self.assertEqual(jobs["execute"]["with"]["host_os"], "${{ matrix.host_os }}")
        self.assertEqual(jobs["execute"]["with"]["ci_run_id"], "")
        self.assertEqual(
            jobs["execute"]["with"]["trusted_capability_ci_run_id"],
            "${{ inputs.ci_run_id }}",
        )

        plan_steps = {step.get("name"): step for step in jobs["plan"]["steps"] if step.get("name")}
        self.assertIn("Mark parent Agent State run as running", plan_steps)
        self.assertIn("Record parent observed source SHA", plan_steps)
        self.assertIn("Select bounded repository-owned execution plan", plan_steps)
        settle = jobs["settle"]["steps"][0]
        self.assertEqual(settle["with"]["ci_run_id"], "${{ inputs.ci_run_id }}")
        status = settle["with"]["status"]
        self.assertIn("needs.plan.result == 'success'", status)
        self.assertIn("needs.execute.result == 'success'", status)

    def test_plan_is_tracked_source_authority_not_a_caller_matrix(self) -> None:
        workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
        inputs = workflow["on"]["workflow_call"]["inputs"]
        for forbidden in ("operating_systems", "matrix", "host_class", "runner", "runner_labels"):
            self.assertNotIn(forbidden, inputs)
        self.assertIn("host_os", inputs)
        self.assertIn("Compatibility fallback", inputs["host_os"]["description"])

        plan_step = next(
            step for step in workflow["jobs"]["plan"]["steps"]
            if step.get("name") == "Select bounded repository-owned execution plan"
        )
        script = plan_step["run"]
        self.assertIn(".ci/execution-plan.json", script)
        self.assertIn("git -C source ls-files --error-unmatch", script)
        self.assertIn('test "${mode}" = 100644', script)
        self.assertIn("repository CI execution plan must be tracked", script)

    def test_release_credentials_are_scoped_to_each_selected_child_os(self) -> None:
        workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
        secrets = workflow["jobs"]["execute"]["secrets"]
        self.assertIn("matrix.host_os == 'linux'", secrets["REGISTRY_WRITE_TOKEN"])
        for name in (
            "APPLE_TEAM_ID",
            "APP_STORE_CONNECT_KEY_ID",
            "APP_STORE_CONNECT_ISSUER_ID",
            "APP_STORE_CONNECT_API_KEY_P8_BASE64",
        ):
            self.assertIn("matrix.host_os == 'macos'", secrets[name])

    def test_child_executor_uses_os_scoped_service_resolver_for_repository_parent(self) -> None:
        text = CHILD.read_text(encoding="utf-8")
        self.assertIn("resolve_repository_ci_host_class_for_os", text)
        self.assertIn("p_host_os", text)
        self.assertIn("repository CI trusted planned-child host-class resolution returned mismatched identity", text)
        self.assertIn("repository_parent", text)


    def test_child_private_evidence_names_are_os_distinct_and_capabilities_repeat_per_child(self) -> None:
        child = yaml.safe_load(CHILD.read_text(encoding="utf-8"))
        steps = {
            step.get("name"): step
            for step in child["jobs"]["execute"]["steps"]
            if step.get("name")
        }
        for name in (
            "Seed stable private repository CI log object",
            "Upload private repository CI log to Google Drive",
            "Upload bounded repository CI evidence to Google Drive",
        ):
            self.assertIn("needs.resolve_host.outputs.host_os", steps[name]["with"]["file_name"])
        self.assertEqual(
            steps["Connect reviewed private network capability"]["if"],
            "${{ steps.capabilities.outputs.private_network == 'true' }}",
        )
        self.assertEqual(
            steps["Configure ephemeral generic package-manager authentication"]["if"],
            "${{ steps.capabilities.outputs.auth_enabled == 'true' }}",
        )


if __name__ == "__main__":
    unittest.main()
