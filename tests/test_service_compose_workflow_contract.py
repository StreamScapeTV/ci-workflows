from __future__ import annotations

import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
FOUNDATION_SHA = "70e08d4ddf8930046632a7135950e924b82e22bf"
COMPOSE_ACTION_SHA = "cef7fcd5ff2ee634544c1cb95d8e862a16f98f90"


class ServiceComposeWorkflowContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.path = ROOT / ".github/workflows/reusable-service-compose.yml"
        cls.source = cls.path.read_text(encoding="utf-8")
        cls.workflow = yaml.safe_load(cls.source)
        cls.inputs = cls.workflow[True]["workflow_call"]["inputs"]
        cls.jobs = cls.workflow["jobs"]

    def test_public_surface_reuses_standard_validation_inputs_and_hides_infrastructure(self) -> None:
        self.assertEqual(
            {"admitted_sha", "working_directory", "validation_plan_json"},
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

    def test_general_planner_admits_exact_same_repository_source_before_container_capacity(self) -> None:
        plan = self.jobs["plan"]
        self.assertEqual(["linux", "amd64", "general", "small"], plan["runs-on"])
        self.assertEqual(5, plan["timeout-minutes"])
        self.assertIn("admitted_sha must be an exact lowercase commit SHA", self.source)
        self.assertIn("service/Compose validation rejects fork pull requests", self.source)
        self.assertIn("PR_HEAD_REPOSITORY", self.source)
        self.assertIn("CALLER_REPOSITORY", self.source)
        self.assertNotIn("inputs.runner", self.source)
        self.assertNotIn("inputs.compose_tool", self.source)

    def test_execution_uses_fixed_central_container_capacity_and_exact_checkout_once(self) -> None:
        validate = self.jobs["validate"]
        self.assertEqual(["linux", "amd64", "buildah", "small"], validate["runs-on"])
        steps = validate["steps"]
        checkout = next(
            step
            for step in steps
            if step.get("name") == "Check out exact admitted caller source once"
        )
        self.assertEqual(
            f"StreamScapeTV/ci-workflows/actions/exact-checkout@{FOUNDATION_SHA}",
            checkout["uses"],
        )
        self.assertEqual("${{ inputs.admitted_sha }}", checkout["with"]["admitted_sha"])
        self.assertEqual("source", checkout["with"]["path"])
        self.assertEqual("1", checkout["with"]["fetch_depth"])
        self.assertNotIn("actions/checkout@", self.source)
        self.assertNotIn(".ciw", self.source)

    def test_workspace_runtime_compose_action_and_terminal_cleanup_are_fixed(self) -> None:
        steps = self.jobs["validate"]["steps"]
        prepare = next(
            step
            for step in steps
            if step.get("name") == "Prepare one isolated container workspace"
        )
        compose = next(
            step
            for step in steps
            if step.get("name")
            == "Start services, wait, validate, diagnose failures, and tear down"
        )
        cleanup = next(
            step
            for step in steps
            if step.get("name") == "Remove and verify all registered workspace state"
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
        self.assertEqual("${{ inputs.admitted_sha }}", prepare["with"]["source_sha"])
        self.assertEqual(
            f"StreamScapeTV/ci-workflows/actions/validate-service-compose@{COMPOSE_ACTION_SHA}",
            compose["uses"],
        )
        self.assertEqual(
            {"admitted_sha", "working_directory", "validation_plan_json"},
            set(compose["with"]),
        )
        self.assertEqual(
            f"StreamScapeTV/ci-workflows/actions/cleanup-workspace@{FOUNDATION_SHA}",
            cleanup["uses"],
        )
        self.assertEqual("always()", cleanup["if"])
        self.assertEqual("always()", clean["if"])
        self.assertIn("cd source", clean["run"])
        self.assertIn('test "$(git rev-parse HEAD)" = "${{ inputs.admitted_sha }}"', clean["run"])
        self.assertIn("podman --version", self.source)
        self.assertIn("podman-compose --version", self.source)
        self.assertIn("podman compose version", self.source)
        self.assertEqual(
            "${{ steps.compose.outcome == 'success' && steps.cleanup.outcome == 'success' && steps.clean.outcome == 'success' && 'success' || 'failure' }}",
            validate["outputs"]["result"],
        )

    def test_composite_action_uses_hardened_executable_boundary_not_shared_ciw_registry(self) -> None:
        action_path = ROOT / "actions/validate-service-compose/action.yml"
        action_source = action_path.read_text(encoding="utf-8")
        action = yaml.safe_load(action_source)
        self.assertEqual(
            {"admitted_sha", "working_directory", "validation_plan_json"},
            set(action["inputs"]),
        )
        self.assertEqual("composite", action["runs"]["using"])
        self.assertIn("INPUT_VALIDATION_PLAN_JSON", action_source)
        self.assertIn('GITHUB_WORKSPACE="${GITHUB_WORKSPACE}/source"', action_source)
        self.assertIn("-m ci_workflows.ciw_compose_entrypoint", action_source)
        self.assertNotIn("ciw.py", action_source)
        self.assertNotIn("shell=True", action_source)

        entrypoint = (
            ROOT / "src/ci_workflows/ciw_compose_entrypoint.py"
        ).read_text(encoding="utf-8")
        self.assertIn("_MAX_PLAN_BYTES = 16 * 1024", entrypoint)
        self.assertIn('"compose_file"', entrypoint)
        self.assertIn('"services"', entrypoint)
        self.assertIn('"env_files"', entrypoint)
        self.assertIn('"readiness"', entrypoint)
        self.assertIn('"validation_script_path"', entrypoint)
        self.assertIn('"validation_timeout_seconds"', entrypoint)
        self.assertIn('result["INPUT_COMPOSE_TOOL"] = "podman"', entrypoint)
        self.assertIn('result["GITHUB_SHA"] = admitted_sha', entrypoint)
        self.assertIn("_validate_selected_readiness", entrypoint)
        self.assertIn("_emit_early_failure_outputs", entrypoint)
        self.assertIn("execute_compose_validate", entrypoint)
        self.assertNotIn("subprocess", entrypoint)
        self.assertNotIn("os.system", entrypoint)

    def test_python_adapter_owns_service_lifecycle_diagnostics_and_cleanup(self) -> None:
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

    def test_docs_keep_bounded_plan_failure_diagnostics_cache_and_runner_boundary(self) -> None:
        docs = (ROOT / "docs/workflows/service-compose.md").read_text(encoding="utf-8").lower()
        for required in (
            "product-neutral",
            "validation_plan_json",
            "readiness",
            "validation_script_path",
            "always",
            "teardown",
            "failure diagnostics",
            "github actions cache",
            "container engine",
            "buildah-small",
        ):
            self.assertIn(required, docs)
        self.assertIn("runner prerequisite before public workflow publication", docs)


if __name__ == "__main__":
    unittest.main()
