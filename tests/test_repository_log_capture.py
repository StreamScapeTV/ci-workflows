from __future__ import annotations

import importlib.util
import io
import os
from pathlib import Path
import tempfile
import subprocess
import sys
import time
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
    def test_actual_executor_preserves_early_failure_and_success_through_log_capture(self) -> None:
        workflow = yaml.safe_load(WORKFLOW.read_text())
        step = next(s for s in workflow["jobs"]["execute"]["steps"]
                    if s.get("name") == "Execute fixed repository-owned entrypoint")
        for body, status in (("exit 17", 17), ('set -u; echo "$UNSET_PRODUCT_VALUE"', 1), ("exit 0", 0)):
            with self.subTest(body=body), tempfile.TemporaryDirectory() as td:
                root = Path(td)
                (root / "central-ci").symlink_to(ROOT, target_is_directory=True)
                (root / "source/.ci").mkdir(parents=True)
                product = root / "source/.ci/test-ui.sh"
                product.write_text("#!/bin/bash\necho product-start\n" + body + "\n")
                product.chmod(0o700)
                log = root / "private.log"
                log.write_text("source-admitted\n")
                (root / "logs").mkdir()
                (root / "artifacts").mkdir()
                env = {**os.environ, **{key: "" for key in step["env"]},
                       "OPERATION": "ui-test", "HOST_OS": "linux", "HOST_CLASS": "hosted",
                       "ENTRYPOINT": ".ci/test-ui.sh", "NORMALIZED_INPUTS": "{}",
                       "AUTH_ENABLED": "false", "CHECKPOINT_ENABLED": "false",
                       "RUNNER_TEMP": td, "GITHUB_WORKSPACE": td,
                       "CI_LOG": str(log), "CI_LOG_DIR": str(root / "logs"),
                       "CI_ARTIFACT_DIR": str(root / "artifacts"),
                       "CI_PROGRESS_FILE": str(root / "progress"),
                       "GITHUB_OUTPUT": str(root / "outputs")}
                env.pop("UNSET_PRODUCT_VALUE", None)
                result = subprocess.run(["bash", "-c", step["run"]], cwd=root, env=env,
                                        capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, status, result.stderr)
                self.assertIn(f"exit_code={status}\n", (root / "outputs").read_text())
                self.assertIn("capture_exit_code=0\n", (root / "outputs").read_text())
                self.assertIn("product-start", log.read_text())

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

    def test_short_pipe_output_is_persisted_before_eof_and_survives_cancel(self) -> None:
        for cancel in (False, True):
            with self.subTest(cancel=cancel), tempfile.TemporaryDirectory() as td:
                path = Path(td) / "private.log"
                path.write_bytes(b"central-startup\n")
                process = subprocess.Popen(
                    [sys.executable, str(SCRIPT), "--log-path", str(path), "--overlap-bytes", "0"],
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                )
                try:
                    assert process.stdin is not None
                    line = b"android-toolchain:jdk25:probe:start\n"
                    process.stdin.write(line)
                    process.stdin.flush()
                    deadline = time.monotonic() + 3
                    while line not in path.read_bytes() and time.monotonic() < deadline:
                        time.sleep(0.01)
                    self.assertIsNone(process.poll(), "capture must still await producer EOF")
                    self.assertIn(line, path.read_bytes(), "short output must flush while pipe remains open")
                    if cancel:
                        process.terminate()
                    process.stdin.close()
                    process.wait(timeout=3)
                    if not cancel:
                        self.assertEqual(process.returncode, 0)
                    self.assertIn(line, path.read_bytes())
                finally:
                    if process.poll() is None:
                        process.kill()
                    process.wait(timeout=3)
                    for stream in (process.stdin, process.stdout, process.stderr):
                        if stream is not None:
                            stream.close()

    def test_unbuffered_binary_stream_falls_back_to_read(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = root / "source"
            source.write_bytes(b"short binary output\n")
            destination = root / "private.log"
            destination.write_bytes(b"")
            with source.open("rb", buffering=0) as stream:
                self.assertFalse(hasattr(stream, "read1"))
                consumed, rollovers = capture.capture_stream(destination, stream, overlap_bytes=0)
            self.assertEqual(consumed, source.stat().st_size)
            self.assertEqual(rollovers, 0)
            self.assertEqual(destination.read_bytes(), source.read_bytes())

    def test_text_stream_still_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "private.log"
            path.write_bytes(b"")
            with self.assertRaises(capture.CaptureError):
                capture.capture_stream(path, io.StringIO("not binary"), overlap_bytes=0)

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
