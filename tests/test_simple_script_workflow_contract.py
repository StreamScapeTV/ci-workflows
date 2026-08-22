from __future__ import annotations

import json
import unittest
from pathlib import Path

import yaml

from ci_workflows.validation_model import ActionsLoader

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/reusable-script.yml"
INDEX = ROOT / "contracts/public-workflows.json"
VALIDATION = ROOT / "contracts/public-workflows/validation.json"
OPERATIONS = ROOT / "contracts/public-workflows/operations.json"
RUNNER_PROFILES = ROOT / "contracts/runner-profiles.json"
RUNNERS_DOC = ROOT / "RUNNERS.md"
DOC = ROOT / "docs/workflows/simple-validation.md"
EXECUTION_BACKEND_SHA = "01d1d10bafcc4fc1e4c51663f72b08f694dc4e35"


class SimpleScriptWorkflowContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.source = WORKFLOW.read_text(encoding="utf-8")
        self.workflow = yaml.load(self.source, Loader=ActionsLoader)

    def test_public_shape_is_small_read_only_and_product_owned(self) -> None:
        self.assertEqual({"workflow_call"}, set(self.workflow["on"]))
        self.assertEqual({"contents": "read"}, self.workflow["permissions"])
        call = self.workflow["on"]["workflow_call"]
        self.assertEqual(
            {
                "execution_backend",
                "admitted_sha",
                "validation_profile",
                "working_directory",
                "script_path",
            },
            set(call["inputs"]),
        )
        self.assertEqual({"result"}, set(call["outputs"]))
        self.assertNotIn("secrets", call)
        self.assertEqual("organization", call["inputs"]["execution_backend"]["default"])
        self.assertTrue(call["inputs"]["admitted_sha"]["required"])
        self.assertTrue(call["inputs"]["validation_profile"]["required"])
        self.assertTrue(call["inputs"]["script_path"]["required"])
        self.assertEqual(".", call["inputs"]["working_directory"]["default"])

    def test_runner_selection_is_semantic_and_backend_bounded(self) -> None:
        jobs = self.workflow["jobs"]
        hosted = jobs["plan"]
        organization = jobs["plan_organization"]
        execute = jobs["validate"]
        self.assertEqual(["ubuntu-latest"], hosted["runs-on"])
        self.assertEqual(
            "${{ inputs.execution_backend == 'github-hosted' }}",
            hosted["if"],
        )
        self.assertEqual(
            ["linux", "amd64", "general", "small"],
            organization["runs-on"],
        )
        self.assertEqual(
            "${{ inputs.execution_backend != 'github-hosted' }}",
            organization["if"],
        )
        self.assertEqual(["plan", "plan_organization"], execute["needs"])
        self.assertEqual(
            "${{ always() && (needs.plan.result == 'success' || needs.plan_organization.result == 'success') }}",
            execute["if"],
        )
        self.assertEqual(
            "${{ fromJSON(needs.plan.outputs.runs_on_json || needs.plan_organization.outputs.runs_on_json) }}",
            execute["runs-on"],
        )

        for planner in (hosted, organization):
            plan_step = next(
                step for step in planner["steps"] if step.get("id") == "plan"
            )
            plan = plan_step["run"]
            self.assertIn("general-small", plan)
            self.assertIn("mobile|apple", plan)
            self.assertNotIn('["linux","amd64","general","small"]', plan)
            self.assertNotIn('["linux","amd64","mobile"]', plan)
            self.assertNotIn('["macOS","ARM64"]', plan)

            backend = next(
                step for step in planner["steps"] if step.get("id") == "backend"
            )
            self.assertEqual(
                f"StreamScapeTV/ci-workflows/actions/resolve-execution-backend@{EXECUTION_BACKEND_SHA}",
                backend["uses"],
            )
            self.assertEqual("validation.script", backend["with"]["workflow_api"])
            self.assertEqual(
                "${{ inputs.execution_backend }}",
                backend["with"]["execution_backend"],
            )
            self.assertEqual(
                "${{ steps.plan.outputs.runner_profile }}",
                backend["with"]["runner_profile"],
            )
            self.assertEqual(
                "${{ steps.plan.outputs.source_trust }}",
                backend["with"]["source_trust"],
            )
        for forbidden in ("self-hosted", "runner_labels", "scale-set"):
            self.assertNotIn(forbidden, self.source)

    def test_script_profiles_are_authorized_by_runner_contract(self) -> None:
        contract = json.loads(RUNNER_PROFILES.read_text(encoding="utf-8"))
        profiles = {row["id"]: row for row in contract["profiles"]}
        bindings = {row["api"]: row for row in contract["workflow_bindings"]}
        self.assertEqual(
            ["general-small", "mobile", "apple"],
            bindings["validation.script"]["profiles"],
        )
        self.assertEqual("profile-contract", bindings["validation.script"]["strategy"])
        for profile in ("general-small", "mobile", "apple"):
            self.assertIn("validation.script", profiles[profile]["allowed_workflow_apis"])
        self.assertEqual(["macOS", "ARM64"], profiles["apple"]["default_internal_selector"])
        self.assertEqual(
            [["macOS", "ARM64"]],
            profiles["apple"]["internal_selectors"],
        )

        guide = RUNNERS_DOC.read_text(encoding="utf-8")
        self.assertIn("Semantic profile IDs are not GitHub runner labels", guide)
        self.assertIn("runs-on: [macOS, ARM64]", guide)

    def test_specialized_capacity_is_admitted_before_bounded_backend_resolution(self) -> None:
        for planner_name in ("plan", "plan_organization"):
            with self.subTest(planner=planner_name):
                steps = self.workflow["jobs"][planner_name]["steps"]
                plan = steps[0]
                self.assertEqual("Admit source trust and select semantic script profile", plan["name"])
                self.assertEqual("${{ github.repository }}", plan["env"]["CALLER_REPOSITORY"])
                self.assertEqual("${{ github.event_name }}", plan["env"]["EVENT_NAME"])
                self.assertIn("pull_request.head.repo.full_name", plan["env"]["PR_HEAD_REPOSITORY"])
                script = plan["run"]
                self.assertIn("^[0-9a-f]{40}$", script)
                self.assertIn("mobile|apple", script)
                self.assertIn('"${PR_HEAD_REPOSITORY}" == "${CALLER_REPOSITORY}"', script)
                self.assertIn("push|workflow_dispatch", script)
                self.assertIn("rejects fork pull requests", script)
                self.assertIn("same-repository PR or exact non-PR source", script)
                backend = next(step for step in steps if step.get("id") == "backend")
                self.assertEqual("${{ steps.plan.outputs.runner_profile }}", backend["with"]["runner_profile"])

    def test_exact_checkout_and_direct_zero_argument_script_execution(self) -> None:
        steps = self.workflow["jobs"]["validate"]["steps"]
        checkout = steps[0]
        self.assertTrue(str(checkout["uses"]).startswith("actions/checkout@"))
        self.assertEqual("${{ inputs.admitted_sha }}", checkout["with"]["ref"])
        self.assertEqual(1, checkout["with"]["fetch-depth"])
        self.assertTrue(checkout["with"]["clean"])
        self.assertFalse(checkout["with"]["persist-credentials"])
        self.assertFalse(checkout["with"]["set-safe-directory"])
        self.assertIn("pull_request.head.repo.full_name", checkout["with"]["repository"])
        verify = steps[1]["run"]
        self.assertIn("git rev-parse HEAD", verify)
        self.assertIn("EXPECTED_SHA", verify)

        execute = next(step for step in steps if step.get("id") == "execute")["run"]
        for required in (
            "test ! -L",
            "test -x",
            '[[ "${SCRIPT_PATH}" != /*',
            '[[ "${WORKING_DIRECTORY}" != /*',
            '[[ "${script_real}" == "${root}/"* ]]',
            '"${script_real}"',
        ):
            self.assertIn(required, execute)
        for forbidden in ("bash -c", "sh -c", "eval ", "${{ inputs.arguments }}"):
            self.assertNotIn(forbidden, execute)

    def test_required_path_avoids_retired_ceremony_and_keeps_zero_artifacts(self) -> None:
        lowered = self.source.casefold()
        for forbidden in (
            "action-tool-lock",
            "provenance",
            "evidence manifest",
            "actions/cache",
            "upload-artifact",
            "download-artifact",
            "registry",
            "release manifest",
        ):
            self.assertNotIn(forbidden, lowered)
        clean = next(
            step for step in self.workflow["jobs"]["validate"]["steps"] if step.get("id") == "clean"
        )
        self.assertEqual("always()", clean["if"])
        self.assertIn("git status --porcelain=v1 --untracked-files=all", clean["run"])

    def test_registry_replaces_unimplemented_conformance_with_generic_script_api(self) -> None:
        index = json.loads(INDEX.read_text(encoding="utf-8"))
        validation = json.loads(VALIDATION.read_text(encoding="utf-8"))
        operations = json.loads(OPERATIONS.read_text(encoding="utf-8"))
        self.assertEqual(25, index["workflow_count"])
        api_names = {row["api_name"] for row in index["workflows"]}
        self.assertIn("validation.script", api_names)
        self.assertNotIn("maintenance.conformance", api_names)
        validation_rows = {row["api_name"]: row for row in validation["workflows"]}
        script = validation_rows["validation.script"]
        self.assertEqual("validation-read", script["permission_profile"])
        self.assertNotIn("supported_consumers", script)
        self.assertNotIn("supported_products", script)
        self.assertEqual(["script_path", "working_directory"], script["repository_owned_hooks"])
        self.assertNotIn(
            "maintenance.conformance",
            {row["api_name"] for row in operations["workflows"]},
        )

    def test_documentation_makes_specialized_workflows_optional(self) -> None:
        text = DOC.read_text(encoding="utf-8")
        self.assertIn("product trigger -> reusable-script.yml -> checked-in product script", text)
        self.assertIn("zero injected arguments", text)
        self.assertIn("same-repository pull requests", text)
        self.assertIn("general", text)
        self.assertIn("They are not prerequisites", text)
        self.assertIn("@main", text)


if __name__ == "__main__":
    unittest.main()
