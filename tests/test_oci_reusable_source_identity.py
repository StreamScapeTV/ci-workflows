from __future__ import annotations

from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/reusable-oci-build.yml"


class OciReusableSourceIdentityTests(unittest.TestCase):
    def test_both_jobs_checkout_and_verify_called_workflow_identity(self) -> None:
        source = WORKFLOW.read_text(encoding="utf-8")
        self.assertEqual(2, source.count("repository: ${{ job.workflow_repository }}"))
        self.assertEqual(2, source.count("ref: ${{ job.workflow_sha }}"))
        self.assertEqual(
            2,
            source.count("EXPECTED_REPOSITORY: ${{ job.workflow_repository }}"),
        )
        self.assertEqual(2, source.count("EXPECTED_SHA: ${{ job.workflow_sha }}"))
        self.assertEqual(
            2,
            source.count('test "${EXPECTED_REPOSITORY}" = "StreamScapeTV/ci-workflows"'),
        )
        self.assertEqual(
            2,
            source.count('test "$(git rev-parse HEAD)" = "${EXPECTED_SHA}"'),
        )
        self.assertNotIn("github.workflow_sha", source)
        self.assertNotIn("GITHUB_WORKFLOW_SHA", source)


if __name__ == "__main__":
    unittest.main()
