from __future__ import annotations

import importlib.util
import io
from pathlib import Path
import tempfile
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/ci/repository_log_capture.py"
WORKFLOW = ROOT / ".github/workflows/repository.yml"

_spec = importlib.util.spec_from_file_location("repository_log_capture", SCRIPT)
assert _spec is not None and _spec.loader is not None
capture = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(capture)


class RepositoryLogCaptureTests(unittest.TestCase):
    def capture(self, payload: bytes, *, overlap_bytes: int = 0) -> bytes:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "central-repository-ci.log"
            path.write_bytes(b"central-startup-context\n")
            path.chmod(0o600)
            consumed, rollovers = capture.capture_stream(
                path,
                io.BytesIO(payload),
                overlap_bytes=overlap_bytes,
            )
            self.assertEqual(consumed, len(payload))
            self.assertGreaterEqual(rollovers, 1)
            data = path.read_bytes()
            self.assertLessEqual(len(data), capture.MAX_LOG_BYTES)
            self.assertIn(b"central repository log rollover", data)
            self.assertTrue(data.startswith(b"central-startup-context\n"))
            return data

    def test_more_than_16_mib_product_success_keeps_terminal_verdict(self) -> None:
        payload = b"x" * (17 * 1024 * 1024) + b"\nPRODUCT-RESULT success\n"
        data = self.capture(payload)
        self.assertIn(b"PRODUCT-RESULT success", data)

    def test_more_than_16_mib_product_failure_near_end_keeps_terminal_diagnostic(self) -> None:
        payload = b"x" * (17 * 1024 * 1024) + b"\nPRODUCT-RESULT failed exit=17\n"
        data = self.capture(payload)
        self.assertIn(b"PRODUCT-RESULT failed exit=17", data)

    def test_secret_crossing_rollover_boundary_remains_whole_for_final_scrub(self) -> None:
        secret = b"fixture-secret-" + b"s" * 8192
        before = b"a" * (2 * 1024 * 1024 - len(secret) // 2)
        after = b"b" * (10 * 1024 * 1024 + 128 * 1024)
        data = self.capture(
            before + secret + after,
            overlap_bytes=len(secret),
        )
        self.assertIn(secret, data)
        scrubbed = data.replace(secret, b"[REDACTED]")
        self.assertNotIn(secret, scrubbed)
        self.assertIn(b"[REDACTED]", scrubbed)

    def test_cancel_or_timeout_tail_keeps_last_progress_diagnostic(self) -> None:
        payload = (
            b"x" * (17 * 1024 * 1024)
            + b"\nlast-progress: native compilation still active\n"
        )
        data = self.capture(payload)
        self.assertIn(b"last-progress: native compilation still active", data)

    def test_capture_failure_still_drains_product_stream(self) -> None:
        payload = b"x" * (2 * 1024 * 1024)
        source = io.BytesIO(payload)
        with tempfile.TemporaryDirectory() as td:
            missing = Path(td) / "missing.log"
            with self.assertRaises(capture.CaptureError):
                capture.capture_stream(missing, source, overlap_bytes=0)
        self.assertEqual(source.tell(), len(payload))

    def test_workflow_preserves_product_and_capture_exit_codes(self) -> None:
        workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
        execute = next(
            step
            for step in workflow["jobs"]["execute"]["steps"]
            if step.get("name") == "Execute fixed repository-owned entrypoint"
        )
        script = execute["run"]
        self.assertIn("repository_log_capture.py", script)
        self.assertIn('pipeline_status=("${PIPESTATUS[@]}")', script)
        self.assertIn("capture_status=", script)
        self.assertIn("capture_exit_code=", script)
        self.assertIn("MAX_OVERLAP_BYTES = 1024 * 1024", SCRIPT.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
