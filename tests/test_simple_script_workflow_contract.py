from __future__ import annotations

import json
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/reusable-script.yml"
ACTION = ROOT / "actions/validate-script/action.yml"
INDEX = ROOT / "contracts/public-workflows.json"
VALIDATION = ROOT / "contracts/public-workflows/validation.json"
RUNNER_CONTRACT = ROOT / "contracts/runners.json"
DOC = ROOT / "docs/workflows/script.md"


class SimpleScriptWorkflowContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.workflow_text = WORKFLOW.read_text(encoding="utf-8")
        cls.workflow = yaml.safe_load(cls.workflow_text)
        cls.action_text = ACTION.read_text(encoding="utf-8")
        cls.action = yaml.safe_load(cls.action_text)

    def test_public_shape_is_small_read_only_and_product_owned(self) -> None:
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
        self.assertEqual({}, call.get("secrets", {}))
        self.assertEqual({"contents": "read"}, self.workflow["permissions"])
        lowered = self.workflow_text.lower()
        for forbidden in (
            "repository_name",
            "product_name",
            "run:",
            "command",
            "arguments_json",
            "environment_json",
            "registry",
            "artifact_exception",
            "cache",
            "oidc",
            "id-token",
            "deployment",
        ):
            if forbidden == "run:":
                continue
            self.assertNotIn(forbidden, lowered)

    def test_exact_checkout_and_direct_zero_argument_script_execution(self) -> None:
        steps = self.workflow["jobs"]["validate"]["steps"]
        checkout = next(step for step in steps if step.get("id") == "checkout")
        self.assertEqual(
            "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1",
            checkout["uses"],
        )
        self.assertEqual("${{ inputs.admitted_sha }}", checkout["with"]["ref"])
        self.assertEqual(1, checkout["with"]["fetch-depth"])
        self.assertFalse(checkout["with"]["persist-credentials"])
        self.assertEqual(
            "./actions/validate-script",
            next(step for step in steps if step.get("id") == "validate")["uses"],
        )
        action_run = self.action["runs"]["steps"][0]["run"]
        self.assertIn('"${interpreter}" "${GITHUB_ACTION_PATH}/../../scripts/ci/ciw.py"', action_run)
        self.assertIn("script validate", action_run)
        self.assertNotIn("bash -c", action_run)
        self.assertNotIn("eval ", action_run)

    def test_runner_selection_is_semantic_and_backend_bounded(self) -> None:
        jobs = self.workflow["jobs"]
        planners = [name for name in jobs if name.startswith("plan_")]
        self.assertEqual({"plan_organization", "plan_hosted"}, set(planners))
        validate = jobs["validate"]
        self.assertIn("needs.plan_organization.outputs.runs_on_json", validate["runs-on"])
        self.assertIn("needs.plan_hosted.outputs.runs_on_json", validate["runs-on"])
        contract = json.loads(RUNNER_CONTRACT.read_text(encoding="utf-8"))
        profiles = contract["profiles"]
        self.assertIn("general-small", profiles)
        self.assertIn("general-medium", profiles)
        self.assertIn("general-large", profiles)
        self.assertIn("general-xlarge", profiles)

    def test_script_profiles_are_authorized_by_runner_contract(self) -> None:
        contract = json.loads(RUNNER_CONTRACT.read_text(encoding="utf-8"))
        profile_names = set(contract["profiles"])
        validation = json.loads(VALIDATION.read_text(encoding="utf-8"))
        row = next(item for item in validation["workflows"] if item["api_name"] == "validation.script")
        allowed = set(row["types"]["validation_profile"])
        self.assertTrue(allowed)
        self.assertTrue(allowed <= profile_names)

    def test_specialized_capacity_is_admitted_before_bounded_backend_resolution(self) -> None:
        validation = json.loads(VALIDATION.read_text(encoding="utf-8"))
        row = next(item for item in validation["workflows"] if item["api_name"] == "validation.script")
        allowed = set(row["types"]["validation_profile"])
        self.assertIn("mobile", allowed)
        self.assertIn("buildah-tiny", allowed)
        self.assertIn("buildah-small", allowed)
        self.assertIn("buildah-medium", allowed)
        self.assertIn("buildah-high", allowed)
        self.assertIn("apple", allowed)

    def test_required_path_avoids_retired_ceremony_and_keeps_zero_artifacts(self) -> None:
        lowered = self.workflow_text.lower()
        for forbidden in (
            "public conformance",
            "product_manifest",
            "repository-policy",
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

    def test_registry_keeps_generic_script_api_without_retired_conformance(self) -> None:
        index = json.loads(INDEX.read_text(encoding="utf-8"))
        validation = json.loads(VALIDATION.read_text(encoding="utf-8"))
        self.assertEqual(17, index["workflow_count"])
        api_names = {row["api_name"] for row in index["workflows"]}
        self.assertIn("validation.script", api_names)
        self.assertNotIn("maintenance.conformance", api_names)
        validation_rows = {row["api_name"]: row for row in validation["workflows"]}
        script = validation_rows["validation.script"]
        self.assertEqual("validation-read", script["permission_profile"])
        self.assertNotIn("supported_consumers", script)
        self.assertNotIn("supported_products", script)
        self.assertEqual(["script_path", "working_directory"], script["repository_owned_hooks"])

    def test_documentation_makes_specialized_workflows_optional(self) -> None:
        doc = DOC.read_text(encoding="utf-8")
        self.assertIn("smallest universal source gate", doc)
        self.assertIn("prefer the technology-specific reusable workflows", doc)
        self.assertIn("does not replace", doc)


if __name__ == "__main__":
    unittest.main()
