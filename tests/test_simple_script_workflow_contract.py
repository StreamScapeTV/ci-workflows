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


class SimpleScriptWorkflowContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.source = WORKFLOW.read_text(encoding="utf-8")
        self.workflow = yaml.load(self.source, Loader=ActionsLoader)

    def test_public_shape_is_small_read_only_and_product_owned(self) -> None:
        self.assertEqual({"workflow_call"}, set(self.workflow["on"]))
        self.assertEqual({"contents": "read"}, self.workflow["permissions"])
        call = self.workflow["on"]["workflow_call"]
        self.assertEqual(
            {"admitted_sha", "validation_profile", "working_directory", "script_path"},
            set(call["inputs"]),
        )
        self.assertEqual({"result"}, set(call["outputs"]))
        self.assertNotIn("secrets", call)
        self.assertTrue(call["inputs"]["admitted_sha"]["required"])
        self.assertTrue(call["inputs"]["validation_profile"]["required"])
        self.assertTrue(call["inputs"]["script_path"]["required"])
        self.assertEqual(".", call["inputs"]["working_directory"]["default"])

    def test_runner_selection_is_bounded_and_product_cannot_select_labels(self) -> None:
        jobs = self.workflow["jobs"]
        self.assertEqual(["linux", "amd64", "general", "small"], jobs["plan"]["runs-on"])
        self.assertEqual(
            "${{ fromJSON(needs.plan.outputs.runs_on_json) }}",
            jobs["validate"]["runs-on"],
        )
        plan = next(step for step in jobs["plan"]["steps"] if step.get("id") == "plan")["run"]
        for profile, selector in (
            ("general", '["linux","amd64","general","small"]'),
            ("mobile", '["linux","amd64","mobile"]'),
            ("apple", '["macOS","ARM64"]'),
        ):
            self.assertIn(profile, plan)
            self.assertIn(selector, plan)
        for forbidden in ("self-hosted", "runner_labels", "scale-set"):
            self.assertNotIn(forbidden, self.source)

    def test_apple_profile_uses_runner_authority_selector_not_semantic_label(self) -> None:
        contract = json.loads(RUNNER_PROFILES.read_text(encoding="utf-8"))
        profiles = {row["id"]: row for row in contract["profiles"]}
        apple = profiles["apple"]
        selector = apple["default_internal_selector"]
        encoded = json.dumps(selector, separators=(",", ":"))

        plan = next(
            step
            for step in self.workflow["jobs"]["plan"]["steps"]
            if step.get("id") == "plan"
        )["run"]
        self.assertEqual(["macOS", "ARM64"], selector)
        self.assertEqual([selector], apple["internal_selectors"])
        self.assertNotIn("apple", selector)
        self.assertIn(f"apple) runs_on='{encoded}'", plan)

        guide = RUNNERS_DOC.read_text(encoding="utf-8")
        self.assertIn("Semantic profile IDs are not GitHub runner labels", guide)
        self.assertIn("runs-on: [macOS, ARM64]", guide)

    def test_specialized_capacity_fails_closed_before_runner_selection(self) -> None:
        steps = self.workflow["jobs"]["plan"]["steps"]
        trust = steps[0]
        self.assertEqual("Admit specialized runner trust", trust["name"])
        self.assertEqual("${{ github.repository }}", trust["env"]["CALLER_REPOSITORY"])
        self.assertEqual("${{ github.event_name }}", trust["env"]["EVENT_NAME"])
        self.assertIn("pull_request.head.repo.full_name", trust["env"]["PR_HEAD_REPOSITORY"])
        script = trust["run"]
        self.assertIn('^[[0-9a-f]{40}$'.replace('[[', '['), script)
        self.assertIn("mobile|apple", script)
        self.assertIn('"${PR_HEAD_REPOSITORY}" == "${CALLER_REPOSITORY}"', script)
        self.assertIn("push|workflow_dispatch", script)
        self.assertIn("rejects fork pull requests", script)
        self.assertIn("same-repository PR or exact non-PR source", script)
        self.assertLess(self.source.index("Admit specialized runner trust"), self.source.index("Resolve bounded semantic runner"))

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
        self.assertEqual(24, index["workflow_count"])
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