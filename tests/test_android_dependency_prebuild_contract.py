"""Focused contract coverage for the optional Android private-dependency prebuild pass."""
from __future__ import annotations

import unittest
from pathlib import Path

import yaml

from ci_workflows.validation_model import ActionsLoader

ROOT = Path(__file__).resolve().parents[1]
REUSABLE = ROOT / ".github/workflows/reusable-android.yml"


class AndroidDependencyPrebuildContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = REUSABLE.read_text(encoding="utf-8")
        cls.workflow = yaml.load(cls.source, Loader=ActionsLoader)
        cls.job = cls.workflow["jobs"]["validate"]
        cls.steps = cls.job["steps"]
        cls.by_id = {step["id"]: step for step in cls.steps if "id" in step}

    def test_prebuild_is_optional_private_dependency_data(self) -> None:
        inputs = self.workflow["on"]["workflow_call"]["inputs"]
        prebuild = inputs["dependency_prebuild_plan_json"]
        self.assertFalse(prebuild["required"])
        self.assertEqual(prebuild["default"], "")
        self.assertEqual(prebuild["type"], "string")
        self.assertNotIn("cache", prebuild["description"].casefold())

    def test_prebuild_reuses_current_grouped_android_helper(self) -> None:
        expected = "StreamScapeTV/ci-workflows/actions/validate-android@main"
        for identifier in (
            "prebuild_plan",
            "prebuild_execute",
            "prebuild_cleanup",
            "prebuild_residue",
        ):
            step = self.by_id[identifier]
            self.assertEqual(step["uses"], expected)
            self.assertNotIn("run", step)
        self.assertEqual(self.by_id["prebuild_plan"]["with"]["validation_scope"], "protected-full")
        self.assertEqual(self.by_id["prebuild_execute"]["with"]["validation_scope"], "protected-full")
        self.assertEqual(
            self.by_id["prebuild_execute"]["with"]["validation_plan_json"],
            "${{ inputs.dependency_prebuild_plan_json }}",
        )

    def test_prebuild_requires_verified_dependency_and_cleans_before_validation(self) -> None:
        plan = self.by_id["prebuild_plan"]
        execute = self.by_id["prebuild_execute"]
        authoritative = self.by_id["execute"]
        self.assertIn("private_dependency_used == 'true'", plan["if"])
        self.assertIn("steps.dependency.outcome == 'success'", execute["if"])
        self.assertEqual(
            execute["with"]["private_dependency_verified"],
            "${{ steps.dependency.outputs.verified }}",
        )
        self.assertEqual(
            execute["with"]["private_dependency_credentials_erased"],
            "${{ steps.dependency.outputs.credentials_erased }}",
        )
        self.assertLess(self.steps.index(execute), self.steps.index(self.by_id["prebuild_cleanup"]))
        self.assertLess(
            self.steps.index(self.by_id["prebuild_cleanup"]),
            self.steps.index(self.by_id["prebuild_residue"]),
        )
        self.assertLess(self.steps.index(self.by_id["prebuild_residue"]), self.steps.index(authoritative))
        self.assertIn("steps.prebuild_execute.outcome == 'success'", authoritative["if"])
        self.assertIn("steps.prebuild_cleanup.outcome == 'success'", authoritative["if"])
        self.assertIn("steps.prebuild_residue.outcome == 'success'", authoritative["if"])

    def test_cleanup_result_requires_prebuild_cleanup_when_prebuild_is_requested(self) -> None:
        cleanup_result = self.job["outputs"]["cleanup_result"]
        self.assertIn("inputs.dependency_prebuild_plan_json == ''", cleanup_result)
        self.assertIn("steps.prebuild_cleanup.outcome == 'success'", cleanup_result)
        self.assertIn("steps.prebuild_residue.outcome == 'success'", cleanup_result)
        self.assertIn("steps.workspace_cleanup.outcome == 'success'", cleanup_result)

    def test_prebuild_does_not_expand_runner_or_cache_authority(self) -> None:
        self.assertEqual(
            self.job["runs-on"],
            "${{ fromJSON(needs.plan.outputs.runs_on_json || needs.plan_organization.outputs.runs_on_json) }}",
        )
        self.assertEqual(self.workflow["jobs"]["plan"]["runs-on"], ["ubuntu-latest"])
        self.assertEqual(
            self.workflow["jobs"]["plan_organization"]["runs-on"],
            ["linux", "amd64", "mobile"],
        )
        lowered = self.source.casefold()
        for forbidden in (
            "actions/cache",
            "id-token",
            "cache_endpoint",
            "cache_host",
            "streamscape-media",
            "iptv-android",
        ):
            self.assertNotIn(forbidden, lowered)


if __name__ == "__main__":
    unittest.main()
