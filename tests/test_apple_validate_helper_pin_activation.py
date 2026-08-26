from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APPLE_ACTION = "StreamScapeTV/ci-workflows/actions/validate-apple"


class AppleValidateHelperActivationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.workflow = (
            ROOT / ".github/workflows/reusable-apple.yml"
        ).read_text(encoding="utf-8")

    def test_all_apple_validate_phases_follow_main(self) -> None:
        self.assertEqual(self.workflow.count(f"uses: {APPLE_ACTION}@main"), 4)
        self.assertIsNone(
            re.search(
                rf"uses: {re.escape(APPLE_ACTION)}@[0-9a-f]{{40}}",
                self.workflow,
            )
        )
        for phase in ("plan", "execute", "cleanup", "residue"):
            self.assertEqual(self.workflow.count(f"phase: {phase}"), 1)

    def test_simulator_confidence_stays_inside_canonical_validation_apple(self) -> None:
        self.assertIn("validation_scope:", self.workflow)
        self.assertIn("simulator-confidence", self.workflow)
        self.assertIn("runs-on: ${{ fromJSON(needs.plan.outputs.runs_on_json) }}", self.workflow)
        self.assertNotIn("reusable-apple-simulator", self.workflow)


if __name__ == "__main__":
    unittest.main()
