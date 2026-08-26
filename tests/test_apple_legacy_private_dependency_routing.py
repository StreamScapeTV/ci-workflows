from __future__ import annotations

import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "reusable-apple.yml"
RELEASE_HELPER = "StreamScapeTV/ci-workflows/actions/materialize-private-release-asset@"


class AppleLegacyPrivateDependencyRoutingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = WORKFLOW.read_text(encoding="utf-8")
        cls.workflow = yaml.safe_load(cls.source)
        cls.steps = cls.workflow["jobs"]["apple"]["steps"]

    @classmethod
    def step(cls, step_id: str) -> dict[str, object]:
        return next(step for step in cls.steps if step.get("id") == step_id)

    def test_legacy_dependency_without_config_skips_release_planning(self) -> None:
        detector = self.step("release_config")
        self.assertEqual(
            "${{ inputs.validation_scope == 'protected-full' && needs.plan.outputs.private_dependency_used == 'true' }}",
            detector["if"],
        )
        script = str(detector["run"])
        self.assertIn("source/.github/central-ci.json", script)
        self.assertIn("test -e", script)
        self.assertIn("test -L", script)
        self.assertIn("present=false", script)

        release_plan = self.step("release_plan")
        condition = str(release_plan["if"])
        self.assertIn("inputs.validation_scope == 'protected-full'", condition)
        self.assertIn("needs.plan.outputs.private_dependency_used != 'true'", condition)
        self.assertIn("steps.release_config.outputs.present == 'true'", condition)

    def test_modern_release_path_still_always_plans_in_protected_full(self) -> None:
        release_plan = self.step("release_plan")
        condition = str(release_plan["if"])
        self.assertIn("needs.plan.outputs.private_dependency_used != 'true'", condition)
        self.assertTrue(str(release_plan["uses"]).startswith(RELEASE_HELPER))
        self.assertEqual(
            "${{ inputs.private_dependency_repository }}",
            release_plan["with"]["private_dependency_repository"],
        )

    def test_legacy_dependency_path_and_mixed_mode_guard_are_preserved(self) -> None:
        dependency = self.step("dependency")
        self.assertEqual(
            "${{ needs.plan.outputs.private_dependency_used == 'true' }}",
            dependency["if"],
        )
        self.assertEqual(
            "${{ needs.plan.outputs.private_dependency_repository }}",
            dependency["with"]["repository"],
        )
        self.assertEqual(
            "${{ needs.plan.outputs.private_dependency_sha }}",
            dependency["with"]["admitted_sha"],
        )

        release_plan = self.step("release_plan")
        self.assertEqual(
            "${{ inputs.private_dependency_repository }}",
            release_plan["with"]["private_dependency_repository"],
        )

    def test_release_materialization_remains_driven_by_strict_plan_output(self) -> None:
        release = self.step("release_asset")
        self.assertEqual(
            "${{ steps.release_plan.outputs.used == 'true' }}",
            release["if"],
        )
        cleanup = self.step("release_cleanup")
        self.assertIn("steps.release_plan.outputs.used == 'true'", str(cleanup["if"]))


if __name__ == "__main__":
    unittest.main()
