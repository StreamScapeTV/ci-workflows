from __future__ import annotations

import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
CHECKOUT_SHA = "3d3c42e5aac5ba805825da76410c181273ba90b1"
FOUNDATION_SHA = "70e08d4ddf8930046632a7135950e924b82e22bf"


class ServiceComposeWorkflowContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.path = ROOT / ".github/workflows/reusable-service-compose.yml"
        cls.source = cls.path.read_text(encoding="utf-8")
        cls.workflow = yaml.safe_load(cls.source)
        cls.inputs = cls.workflow[True]["workflow_call"]["inputs"]
        cls.jobs = cls.workflow["jobs"]

    def test_public_surface_is_product_neutral_and_container_engine_is_central_owned(self) -> None:
        self.assertEqual(
            {
                "admitted_sha",
                "working_directory",
                "compose_file",
                "services_json",
                "env_files_json",
                "readiness_json",
                "validation_script_path",
                "validation_timeout_seconds",
            },
            set(self.inputs),
        )
        self.assertEqual({"contents": "read"}, self.workflow["permissions"])
        for forbidden in (
            "runner",
            "runs_on",
            "runner_labels",
            "container_engine",
            "compose_tool",
            "docker_command",
            "podman_command",
            "shell",
            "command",
            "environment_json",
            "secret_name",
            "product_id",
        ):
            self.assertNotIn(forbidden, self.inputs)
        self.assertNotIn("secrets:", self.source)
        self.assertNotIn("secrets: inherit", self.source)
        self.assertNotIn("actions/cache", self.source)
        self.assertNotIn("upload-artifact", self.source)
        self.assertNotIn("self-hosted", self.source)

    def test_planner_admits_exact_same_repository_source_before_compose_capacity(self) -> None:
        plan = self.jobs["plan"]
        self.assertEqual(["linux", "amd64", "general", "small"], plan["runs-on"])
        self.assertEqual(5, plan["timeout-minutes"])
        self.assertIn("admitted_sha must be an exact lowercase commit SHA", self.source)
        self.assertIn("service/Compose validation rejects fork pull requests", self.source)
        self.assertIn("PR_HEAD_REPOSITORY", self.source)
        self.assertIn("CALLER_REPOSITORY", self.source)
        capacity = next(
            step
            for step in plan["steps"]
            if step.get("name") == "Select the bounded daemonless Compose runner"
        )
        self.assertIn('["linux","amd64","buildah","small"]', capacity["run"])
        self.assertNotIn("inputs.runner", self.source)
        self.assertNotIn("inputs.compose_tool", self.source)

    def test_execution_uses_exact_caller_source_and_exact_central_workflow_source(self) -> None:
        validate = self.jobs["validate"]
        self.assertEqual(
            "${{ fromJSON(needs.plan.outputs.runs_on_json) }}",
            validate["runs-on"],
        )
        steps = validate["steps"]
        caller = next(
            step
            for step in steps
            if step.get("name") == "Check out exact admitted caller source"
        )
        central = next(
            step
            for step in steps
            if step.get("name") == "Check out exact Central reusable-workflow source"
        )
        self.assertEqual(f"actions/checkout@{CHECKOUT_SHA}", caller["uses"])
        self.assertEqual("${{ inputs.admitted_sha }}", caller["with"]["ref"])
        self.assertFalse(caller["with"]["persist-credentials"])
        self.assertFalse(caller["with"]["set-safe-directory"])
        self.assertNotIn("path", caller["with"])
        self.assertEqual(f"actions/checkout@{CHECKOUT_SHA}", central["uses"])
        self.assertEqual("StreamScapeTV/ci-workflows", central["with"]["repository"])
        self.assertEqual("${{ github.workflow_sha }}", central["with"]["ref"])
        self.assertEqual(".ciw", central["with"]["path"])
        self.assertFalse(central["with"]["persist-credentials"])
        self.assertFalse(central["with"]["set-safe-directory"])
        self.assertIn('test "$(git rev-parse HEAD)" = "${GITHUB_WORKFLOW_SHA}"', self.source)

    def test_workspace_and_runtime_are_fixed_and_cleanup_is_terminal(self) -> None:
        steps = self.jobs["validate"]["steps"]
        prepare = next(
            step
            for step in steps
            if step.get("name") == "Prepare one isolated container workspace"
        )
        cleanup = next(
            step
            for step in steps
            if step.get("name") == "Remove and verify all registered workspace state"
        )
        remove_central = next(
            step
            for step in steps
            if step.get("name") == "Remove exact Central helper checkout"
        )
        clean = next(
            step
            for step in steps
            if step.get("name") == "Verify exact caller source remained clean after cleanup"
        )
        self.assertEqual(
            f"StreamScapeTV/ci-workflows/actions/prepare-workspace@{FOUNDATION_SHA}",
            prepare["uses"],
        )
        self.assertEqual("container", prepare["with"]["profile"])
        self.assertEqual("disabled", prepare["with"]["cache_mode"])
        self.assertEqual(
            f"StreamScapeTV/ci-workflows/actions/cleanup-workspace@{FOUNDATION_SHA}",
            cleanup["uses"],
        )
        self.assertEqual("always()", cleanup["if"])
        self.assertEqual("always()", remove_central["if"])
        self.assertEqual("always()", clean["if"])
        self.assertIn("rm -rf -- .ciw", remove_central["run"])
        self.assertIn('test ! -e .ciw', remove_central["run"])
        self.assertIn('test "$(git rev-parse HEAD)" = "${EXPECTED_SHA}"', clean["run"])
        self.assertIn("podman --version", self.source)
        self.assertIn("podman-compose --version", self.source)
        self.assertIn("podman compose version", self.source)
        self.assertIn("INPUT_COMPOSE_TOOL: podman", self.source)

    def test_thin_workflow_delegates_service_lifecycle_to_tested_python_adapter(self) -> None:
        execute = next(
            step
            for step in self.jobs["validate"]["steps"]
            if step.get("name")
            == "Start services, wait, validate, diagnose failures, and tear down"
        )
        self.assertIn("python3 .ciw/scripts/ci/ciw.py compose validate", execute["run"])
        self.assertEqual(
            "${{ steps.execute.outcome == 'success' && steps.cleanup.outcome == 'success' && steps.clean.outcome == 'success' && 'success' || 'failure' }}",
            self.jobs["validate"]["outputs"]["result"],
        )
        adapter = (ROOT / "src/ci_workflows/ciw_compose.py").read_text(encoding="utf-8")
        for required in (
            "compose_up(",
            "wait_for_compose_services(",
            "capture_compose_logs(",
            "cleanup_compose_stack(",
            "run_process(",
            "finally:",
        ):
            self.assertIn(required, adapter)
        for forbidden in ("shell=True", "os.system(", "subprocess.run(", "TOP_SECRET"):
            self.assertNotIn(forbidden, adapter)

    def test_docs_keep_caller_owned_topology_failure_diagnostics_and_no_cache_boundary(self) -> None:
        docs = (ROOT / "docs/workflows/service-compose.md").read_text(encoding="utf-8").lower()
        for required in (
            "product-neutral",
            "caller",
            "readiness",
            "validation_script_path",
            "always",
            "teardown",
            "failure diagnostics",
            "github actions cache",
            "container engine",
        ):
            self.assertIn(required, docs)
        self.assertIn("runner prerequisite before public workflow publication", docs)


if __name__ == "__main__":
    unittest.main()
