from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INTEGRATED_HELPER_SHA = "2ea47520b9d84b9b0a71c23de3da03f02a5bea9c"
STALE_HELPER_SHA = "c82cd9fba134ff736621b8bbd636594c2a6fe923"


class AppleValidateHelperPinActivationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.workflow = (
            ROOT / ".github/workflows/reusable-apple.yml"
        ).read_text(encoding="utf-8")

    def test_all_protected_full_validate_phases_use_integrated_helper(self) -> None:
        pins = re.findall(
            r"uses: StreamScapeTV/ci-workflows/actions/validate-apple@([0-9a-f]{40})",
            self.workflow,
        )
        self.assertEqual(pins, [INTEGRATED_HELPER_SHA] * 4)
        for phase in ("plan", "execute", "cleanup", "residue"):
            self.assertEqual(self.workflow.count(f"phase: {phase}"), 1)

    def test_stale_pre_cleanup_helper_is_not_reachable(self) -> None:
        self.assertNotIn(STALE_HELPER_SHA, self.workflow)


if __name__ == "__main__":
    unittest.main()
