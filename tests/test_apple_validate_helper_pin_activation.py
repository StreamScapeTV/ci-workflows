from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INTEGRATED_HELPER_SHA = "d946291afbf32353a959adcd3f6cbb92513a4cbe"
STALE_HELPER_SHA = "c82cd9fba134ff736621b8bbd636594c2a6fe923"
PRE_SIMULATOR_CONFIDENCE_SHA = "2ea47520b9d84b9b0a71c23de3da03f02a5bea9c"


class AppleValidateHelperPinActivationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.workflow = (
            ROOT / ".github/workflows/reusable-apple.yml"
        ).read_text(encoding="utf-8")

    def test_all_apple_validate_phases_use_current_integrated_checkpoint(self) -> None:
        pins = re.findall(
            r"uses: StreamScapeTV/ci-workflows/actions/validate-apple@([0-9a-f]{40})",
            self.workflow,
        )
        self.assertEqual(pins, [INTEGRATED_HELPER_SHA] * 4)
        for phase in ("plan", "execute", "cleanup", "residue"):
            self.assertEqual(self.workflow.count(f"phase: {phase}"), 1)

    def test_stale_helpers_are_not_reachable(self) -> None:
        self.assertNotIn(STALE_HELPER_SHA, self.workflow)
        self.assertNotIn(PRE_SIMULATOR_CONFIDENCE_SHA, self.workflow)

    def test_simulator_confidence_stays_inside_canonical_validation_apple(self) -> None:
        self.assertIn("validation_scope:", self.workflow)
        self.assertIn("simulator-confidence", self.workflow)
        self.assertIn("runs-on: ${{ fromJSON(needs.plan.outputs.runs_on_json) }}", self.workflow)
        self.assertNotIn("reusable-apple-simulator", self.workflow)


if __name__ == "__main__":
    unittest.main()
