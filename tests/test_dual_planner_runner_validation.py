from __future__ import annotations

import unittest

from ci_workflows.validation_helpers import _validate_runner
from ci_workflows.validation_model import Finding, HarnessConfig


_SINGLE = "${{ fromJSON(needs.plan.outputs.runs_on_json) }}"
_DUAL = (
    "${{ fromJSON(needs.plan.outputs.runs_on_json || "
    "needs.plan_organization.outputs.runs_on_json) }}"
)


class DualPlannerRunnerValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = HarnessConfig(
            max_inline_run_lines=20,
            max_matrix_jobs=8,
            max_timeout_minutes=240,
            allowed_runner_profiles=frozenset({"ubuntu-latest"}),
            required_fixture_callers=frozenset(),
            required_event_fixtures=frozenset(),
            required_service_scenarios={},
            exceptions={},
        )

    def findings_for(self, runner: str) -> list[Finding]:
        findings: list[Finding] = []
        _validate_runner(
            runner,
            ".github/workflows/reusable-example.yml",
            self.config,
            findings,
        )
        return findings

    def test_exact_single_and_dual_planner_projections_are_trusted(self) -> None:
        for runner in (_SINGLE, _DUAL):
            with self.subTest(runner=runner):
                self.assertEqual([], self.findings_for(runner))

    def test_near_miss_dynamic_runner_expressions_remain_rejected(self) -> None:
        cases = (
            "${{ fromJSON(needs.plan_organization.outputs.runs_on_json) }}",
            "${{ fromJSON(needs.plan_organization.outputs.runs_on_json || needs.plan.outputs.runs_on_json) }}",
            "${{ fromJSON(needs.plan.outputs.runs_on_json || needs.other.outputs.runs_on_json) }}",
            "${{ inputs.runner }}",
        )
        for runner in cases:
            with self.subTest(runner=runner):
                findings = self.findings_for(runner)
                self.assertEqual(["dynamic-runner"], [item.rule for item in findings])


if __name__ == "__main__":
    unittest.main()
