from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SECURITY_DOC = ROOT / "docs/architecture/security-and-artifacts.md"


class SecurityBoundaryDocumentationTests(unittest.TestCase):
    def test_private_source_credentials_and_logs_remain_confidential(self) -> None:
        guide = SECURITY_DOC.read_text(encoding="utf-8")
        for phrase in (
            "private repository source",
            "credentials",
            "private command output",
            "private R2",
            "`secrets: inherit` remains forbidden",
            "Checkout credentials are not persisted after checkout",
        ):
            self.assertIn(phrase, guide)
        self.assertIn("must never become a public Actions artifact", guide)

    def test_global_action_lock_and_zero_artifact_registry_are_retired(self) -> None:
        guide = SECURITY_DOC.read_text(encoding="utf-8")
        self.assertIn("There is no repository-wide action SHA allowlist", guide)
        self.assertIn("There is no global zero-artifact registry", guide)
        self.assertIn("requirements/validation.txt", guide)
        self.assertNotIn("contracts/action-tool-lock.json", guide)
        self.assertNotIn("contracts/artifact-policy.json", guide)

    def test_release_correctness_remains_feature_scoped(self) -> None:
        guide = SECURITY_DOC.read_text(encoding="utf-8")
        self.assertIn("Exact Git tags", guide)
        self.assertIn("remote read-back", guide)
        self.assertIn("release correctness", guide)


if __name__ == "__main__":
    unittest.main()
