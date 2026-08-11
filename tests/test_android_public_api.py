"""Focused public API agreement for ``validation.android``."""
from __future__ import annotations

import json
import unittest
from pathlib import Path

import yaml

from ci_workflows import public_api

ROOT = Path(__file__).resolve().parents[1]
FRAGMENT = ROOT / "contracts/public-workflows/validation.json"
INDEX = ROOT / "contracts/public-workflows.json"
TYPES = ROOT / "contracts/public-workflow-types.json"
WORKFLOW = ROOT / ".github/workflows/reusable-android.yml"

EXPECTED_INPUTS = {
    "admitted_sha",
    "validation_profile",
    "task_profile",
    "working_directory",
    "gradle_wrapper_path",
    "targeted_test_selector",
    "consumer_script_profile",
    "private_dependency_contract_id",
    "private_dependency_sha",
    "artifact_exception_id",
    "device_family",
    "device_request_id",
}


class AndroidPublicAPIContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.fragment = json.loads(FRAGMENT.read_text(encoding="utf-8"))
        cls.index = json.loads(INDEX.read_text(encoding="utf-8"))
        cls.types = json.loads(TYPES.read_text(encoding="utf-8"))
        cls.workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
        cls.row = next(
            row
            for row in cls.fragment["workflows"]
            if row["api_name"] == "validation.android"
        )
        cls.index_row = next(
            row
            for row in cls.index["workflows"]
            if row["api_name"] == "validation.android"
        )

    def test_android_is_implemented_with_exact_public_surface(self) -> None:
        self.assertEqual(self.row["status"], "implemented")
        self.assertEqual(self.index_row["status"], "implemented")
        self.assertEqual(self.row["api_version"], "1.0.0")
        self.assertEqual(self.row["stable_check_name"], "CI / Android validation")
        self.assertEqual({item["name"] for item in self.row["inputs"]}, EXPECTED_INPUTS)
        self.assertEqual(
            set(self.workflow[True]["workflow_call"]["inputs"]),
            EXPECTED_INPUTS,
        )
        self.assertEqual(self.row["secrets"], ["private_dependency_token"])
        self.assertEqual(
            set(self.workflow[True]["workflow_call"]["secrets"]),
            {"private_dependency_token"},
        )
        self.assertEqual(
            set(self.row["outputs"]),
            {"result", "test_summary", "artifact_exception_used"},
        )
        self.assertEqual(
            set(self.workflow[True]["workflow_call"]["outputs"]),
            {"result", "test_summary", "artifact_exception_used"},
        )

    def test_android_public_inputs_are_typed_and_bounded(self) -> None:
        catalog = self.types["input_catalog"]
        self.assertTrue(EXPECTED_INPUTS <= set(catalog))
        self.assertEqual(catalog["gradle_wrapper_path"]["default"], "gradlew")
        self.assertEqual(catalog["working_directory"]["default"], ".")
        self.assertEqual(
            catalog["targeted_test_selector"]["pattern"],
            r"^[A-Za-z_][A-Za-z0-9_$]*(?:\.[A-Za-z_][A-Za-z0-9_$]*){2,31}$",
        )
        self.assertEqual(
            catalog["device_family"]["enum"],
            ["android", "android-phone", "android-tv", "ios", "tvos"],
        )
        for forbidden in (
            "private_dependency_repository",
            "private_dependency_subdirectory",
            "script_path",
            "command_profile",
            "runner",
            "runs_on",
        ):
            self.assertNotIn(forbidden, EXPECTED_INPUTS)

    def test_repository_hooks_and_components_are_contract_owned(self) -> None:
        self.assertEqual(
            self.row["repository_owned_hooks"],
            ["task_profile", "consumer_script_profile"],
        )
        self.assertEqual(
            self.row["implementation_components"],
            ["ci_workflows.android.validate", "actions/validate-android"],
        )
        self.assertEqual(self.row["matrix_max_jobs"], 1)
        self.assertEqual(self.row["timeout_minutes"], 120)

    def test_complete_public_api_contract_validates(self) -> None:
        data = public_api.validate(ROOT)
        android = next(
            row for row in data.workflows if row["api_name"] == "validation.android"
        )
        self.assertEqual(android["status"], "implemented")


if __name__ == "__main__":
    unittest.main()
