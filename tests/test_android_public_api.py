from __future__ import annotations

import json
import unittest
from pathlib import Path

import yaml

from ci_workflows.validation_model import ActionsLoader

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/reusable-android.yml"
PUBLIC = ROOT / "contracts/public-workflows/validation.json"
TYPES = ROOT / "contracts/public-workflow-types.json"


class AndroidPublicApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.workflow = yaml.load(WORKFLOW.read_text(encoding="utf-8"), Loader=ActionsLoader)
        cls.public = json.loads(PUBLIC.read_text(encoding="utf-8"))
        cls.types = json.loads(TYPES.read_text(encoding="utf-8"))
        cls.android = next(
            row for row in cls.public["workflows"] if row["api_name"] == "validation.android"
        )

    def test_android_api_v2_matches_reusable_workflow(self) -> None:
        self.assertEqual(self.android["api_version"], "2.0.0")
        self.assertEqual(self.android["status"], "implemented")
        self.assertEqual(self.android["semantic_runner_profile"], "mobile")
        self.assertEqual(self.android["matrix_max_jobs"], 1)
        call = self.workflow["on"]["workflow_call"]
        self.assertEqual(set(call["inputs"]), {item["name"] for item in self.android["inputs"]})
        self.assertEqual(set(call["secrets"]), set(self.android["secrets"]))
        self.assertEqual(set(call["outputs"]), set(self.android["outputs"]))

    def test_android_api_uses_one_caller_owned_plan_instead_of_central_profiles(self) -> None:
        inputs = {item["name"] for item in self.android["inputs"]}
        self.assertEqual(
            inputs,
            {
                "admitted_sha",
                "validation_scope",
                "working_directory",
                "gradle_wrapper_path",
                "validation_plan_json",
                "private_dependency_repository",
                "private_dependency_sha",
                "private_dependency_subdirectory",
                "private_dependency_id",
            },
        )
        for forbidden in (
            "validation_profile",
            "task_profile",
            "consumer_script_profile",
            "gradle_tasks_json",
            "targeted_test_selector",
            "script_path",
            "script_arguments_json",
            "private_dependency_contract_id",
            "artifact_exception_id",
            "device_family",
            "device_request_id",
            "runner",
            "runs_on",
            "product_id",
        ):
            self.assertNotIn(forbidden, inputs)
        self.assertEqual(
            set(self.android["repository_owned_hooks"]),
            {"validation_plan_json"},
        )

    def test_android_types_include_protected_full_and_strict_plan_json(self) -> None:
        catalog = self.types["input_catalog"]
        self.assertIn("validation_scope", catalog)
        self.assertEqual(
            catalog["validation_scope"]["enum"],
            [
                "protected-full",
                "compile",
                "unit",
                "assemble",
                "lint",
                "targeted-unit",
                "gradle",
                "script",
            ],
        )
        self.assertEqual(
            catalog["validation_plan_json"],
            {"type": "json-object", "max_bytes": 16384},
        )
        self.assertIn("private_dependency_id", catalog)

    def test_android_outputs_and_components_are_primitive_backed(self) -> None:
        self.assertEqual(
            set(self.android["outputs"]),
            {"result", "test_summary", "cleanup_result"},
        )
        self.assertEqual(
            set(self.android["implementation_components"]),
            {
                "ci_workflows.ciw_android.execute_android_validate",
                "ci_workflows.language_primitives.run_gradle_tasks",
                "ci_workflows.language_primitives.android_targeted_test",
                "actions/validate-android",
            },
        )

    def test_android_breaking_change_is_explicitly_acknowledged(self) -> None:
        acknowledgement = next(
            row
            for row in self.types["breaking_change_acknowledgements"]
            if row["api_name"] == "validation.android"
        )
        self.assertEqual(acknowledgement["migration_issue"], "#332")
        self.assertEqual(acknowledgement["effective_version"], "2.0.0")
        self.assertEqual(acknowledgement["kind"], "technology-input-decoupling")


if __name__ == "__main__":
    unittest.main()
