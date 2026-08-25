from __future__ import annotations

import re
import unittest
from pathlib import Path

import yaml

from ci_workflows.validation_model import ActionsLoader

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "r2-diagnostics-smoke.yml"
EXPECTED_SECRETS = {
    "R2_ACCOUNT_ID",
    "R2_BUCKET",
    "R2_ACCESS_KEY_ID",
    "R2_SECRET_ACCESS_KEY",
}


class R2DiagnosticsSmokeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.source = WORKFLOW.read_text(encoding="utf-8")
        self.workflow = yaml.load(self.source, Loader=ActionsLoader)
        self.job = self.workflow["jobs"]["smoke"]

    def test_smoke_is_manual_hosted_and_read_only(self) -> None:
        self.assertEqual(set(self.workflow["on"]), {"workflow_dispatch"})
        self.assertEqual(self.workflow["permissions"], {"contents": "read"})
        self.assertEqual(set(self.workflow["jobs"]), {"smoke"})
        self.assertEqual(self.job["runs-on"], ["ubuntu-latest"])
        self.assertEqual(self.job["timeout-minutes"], 10)
        self.assertNotIn("workflow_call", self.workflow["on"])

    def test_smoke_uses_only_fixed_r2_secret_names(self) -> None:
        secrets = set(re.findall(r"secrets\.([A-Z0-9_]+)", self.source))
        self.assertEqual(secrets, EXPECTED_SECRETS)
        self.assertNotIn("inputs.", self.source)
        self.assertNotIn("secrets: inherit", self.source)

    def test_smoke_delegates_remote_storage_to_bounded_helpers(self) -> None:
        self.assertEqual(self.source.count("scripts/ci/r2_diagnostics.py"), 1)
        self.assertIn("DiagnosticReader", self.source)
        self.assertIn("encode_receipt_capability", self.source)
        self.assertIn("reader.retrieve(capability)", self.source)
        self.assertIn("actual != expected", self.source)
        for forbidden in (
            "curl ",
            "wget ",
            "aws s3",
            "rclone",
            "wrangler r2",
            "upload-artifact",
            "presigned",
            "public-url",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, self.source.lower())

    def test_smoke_retrieves_the_exact_uploaded_receipt(self) -> None:
        upload = next(step for step in self.job["steps"] if step.get("id") == "upload")
        retrieve = next(
            step
            for step in self.job["steps"]
            if step.get("name") == "Retrieve and verify decompressed diagnostic"
        )
        self.assertEqual(upload["name"], "Upload and verify private R2 diagnostic")
        self.assertEqual(retrieve["env"]["R2_OBJECT_KEY"], "${{ steps.upload.outputs.object_key }}")
        self.assertEqual(retrieve["env"]["R2_SHA256"], "${{ steps.upload.outputs.sha256 }}")
        self.assertEqual(
            retrieve["env"]["R2_READ_ACCESS_KEY_ID"],
            "${{ secrets.R2_ACCESS_KEY_ID }}",
        )
        self.assertEqual(
            retrieve["env"]["R2_READ_SECRET_ACCESS_KEY"],
            "${{ secrets.R2_SECRET_ACCESS_KEY }}",
        )
        self.assertIn('Path(os.environ["DIAGNOSTIC_PATH"]).read_bytes()', retrieve["run"])

    def test_smoke_diagnostic_is_synthetic_and_cleanup_is_always_run(self) -> None:
        self.assertIn('synthetic Central R2 diagnostic smoke\\n', self.source)
        self.assertNotIn("env |", self.source)
        self.assertNotIn("printenv", self.source)
        self.assertNotIn("git log", self.source)
        cleanup = next(
            step for step in self.job["steps"] if step.get("name") == "Remove local smoke diagnostic"
        )
        self.assertEqual(cleanup["if"], "always()")
        self.assertIn('rm -f "${DIAGNOSTIC_PATH}"', cleanup["run"])
        self.assertIn('test ! -e "${DIAGNOSTIC_PATH}"', cleanup["run"])


if __name__ == "__main__":
    unittest.main()
