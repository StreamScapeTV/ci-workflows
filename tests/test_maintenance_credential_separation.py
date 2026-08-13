from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/reusable-conformance.yml"


class MaintenanceCredentialSeparationTests(unittest.TestCase):
    def test_dry_run_and_update_credentials_are_mutually_exclusive(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("id: report_read\n        if: ${{ inputs.dry_run }}", text)
        self.assertIn("id: report_update\n        if: ${{ !inputs.dry_run }}", text)
        self.assertIn("token: ${{ secrets.organization_read_token }}", text)
        self.assertIn("token: ${{ secrets.organization_update_token }}", text)
        self.assertEqual(text.count("secrets.organization_read_token"), 1)
        self.assertEqual(text.count("secrets.organization_update_token"), 1)
        self.assertNotIn("inputs.dry_run && secrets", text)
        self.assertNotIn("secrets.organization_read_token ||", text)
        self.assertNotIn("|| secrets.organization_update_token", text)

    def test_job_outputs_accept_only_the_executed_conformance_step(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")
        for output in ("result", "mutation_count", "report_issue_url", "request_id"):
            self.assertIn(
                f"{output}: ${{{{ steps.report_read.outputs.{output} || steps.report_update.outputs.{output} }}}}",
                text,
            )


if __name__ == "__main__":
    unittest.main()
