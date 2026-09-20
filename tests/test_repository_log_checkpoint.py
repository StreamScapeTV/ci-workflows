from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import threading
import unittest
import sys
import urllib.error


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/ci/repository_log_checkpoint.py"
SPEC = importlib.util.spec_from_file_location("repository_log_checkpoint", SCRIPT)
assert SPEC and SPEC.loader
mod = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = mod
SPEC.loader.exec_module(mod)


class FakeUpdater:
    def __init__(self, failures: list[Exception] | None = None) -> None:
        self.failures = list(failures or [])
        self.calls: list[tuple[str, bytes]] = []

    def replace(self, file_id: str, data: bytes) -> None:
        self.calls.append((file_id, data))
        if self.failures:
            raise self.failures.pop(0)


class RepositoryLogCheckpointTests(unittest.TestCase):
    def test_checkpoint_interval_is_fixed_to_three_minutes(self) -> None:
        self.assertEqual(mod.INTERVAL_SECONDS, 180)
        self.assertLessEqual(mod.INTERVAL_SECONDS, 300)

    def test_snapshot_scrubs_raw_and_escaped_configured_secrets(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            log = Path(td) / "log.txt"
            log.write_bytes(b"before secret-value middle line1\\nline2 after")
            data = mod.scrubbed_snapshot(
                log,
                {
                    "CI_SECRET_ONE": "secret-value",
                    "CI_SECRET_TWO": "line1\nline2",
                    "OTHER": "not-secret",
                },
            )
            self.assertNotIn(b"secret-value", data)
            self.assertNotIn(b"line1\\nline2", data)
            self.assertIn(b"[REDACTED]", data)

    def test_unchanged_snapshot_skips_drive_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            log = root / "log.txt"
            log.write_bytes(b"same bytes\n")
            digest = hashlib.sha256(log.read_bytes()).hexdigest()
            state = mod.CheckpointState(last_sha256=digest)
            updater = FakeUpdater()
            result = mod.checkpoint_once(
                log_path=log,
                file_id="trustedFileId_1234567890",
                state=state,
                status_path=root / "status.json",
                updater=updater,
                environ={},
                sleep_fn=lambda _seconds: None,
            )
            self.assertEqual(result, "unchanged")
            self.assertEqual(updater.calls, [])
            self.assertEqual(state.unchanged, 1)

    def test_changed_snapshot_replaces_only_fixed_file_id_and_updates_digest(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            log = root / "log.txt"
            log.write_bytes(b"new secret-value bytes\n")
            state = mod.CheckpointState(last_sha256=hashlib.sha256(b"").hexdigest())
            updater = FakeUpdater()
            result = mod.checkpoint_once(
                log_path=log,
                file_id="trustedFileId_1234567890",
                state=state,
                status_path=root / "status.json",
                updater=updater,
                environ={"CI_SECRET_TEST": "secret-value"},
                sleep_fn=lambda _seconds: None,
            )
            self.assertEqual(result, "uploaded")
            self.assertEqual(len(updater.calls), 1)
            file_id, payload = updater.calls[0]
            self.assertEqual(file_id, "trustedFileId_1234567890")
            self.assertNotIn(b"secret-value", payload)
            self.assertIn(b"[REDACTED]", payload)
            self.assertEqual(state.last_sha256, hashlib.sha256(payload).hexdigest())
            status = json.loads((root / "status.json").read_text())
            self.assertEqual(status["uploads"], 1)

    def test_transient_failure_does_not_advance_digest_and_later_retry_succeeds(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            log = root / "log.txt"
            log.write_bytes(b"changed\n")
            seed = hashlib.sha256(b"").hexdigest()
            state = mod.CheckpointState(last_sha256=seed)
            updater = FakeUpdater(
                [
                    mod.CheckpointError("drive_transport", transient=True),
                    mod.CheckpointError("drive_http_503", transient=True),
                    mod.CheckpointError("drive_transport", transient=True),
                ]
            )
            with self.assertRaises(mod.CheckpointError):
                mod.checkpoint_once(
                    log_path=log,
                    file_id="trustedFileId_1234567890",
                    state=state,
                    status_path=root / "status.json",
                    updater=updater,
                    environ={},
                    sleep_fn=lambda _seconds: None,
                )
            self.assertEqual(state.last_sha256, seed)
            self.assertEqual(state.transient_failures, 1)

            result = mod.checkpoint_once(
                log_path=log,
                file_id="trustedFileId_1234567890",
                state=state,
                status_path=root / "status.json",
                updater=updater,
                environ={},
                sleep_fn=lambda _seconds: None,
            )
            self.assertEqual(result, "uploaded")
            self.assertEqual(state.uploads, 1)

    def test_loop_performs_final_flush_when_stopped(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            log = root / "log.txt"
            log.write_bytes(b"final bytes\n")
            updater = FakeUpdater()
            stop = threading.Event()
            stop.set()
            state = mod.run_loop(
                log_path=log,
                file_id="trustedFileId_1234567890",
                seed_sha256=hashlib.sha256(b"").hexdigest(),
                status_path=root / "status.json",
                updater=updater,
                environ={},
                stop_event=stop,
                interval_seconds=0.01,
                sleep_fn=lambda _seconds: None,
            )
            self.assertEqual(state.uploads, 1)
            self.assertEqual(updater.calls[0][1], b"final bytes\n")

    def test_drive_updater_uses_patch_to_only_the_supplied_file_id(self) -> None:
        class Response:
            def __init__(self, raw: bytes) -> None:
                self.raw = raw
            def __enter__(self):
                return self
            def __exit__(self, *_args):
                return False
            def read(self) -> bytes:
                return self.raw

        class Opener:
            def __init__(self) -> None:
                self.requests = []
            def urlopen(self, request, timeout):
                self.requests.append((request, timeout))
                if request.full_url == "https://oauth2.googleapis.com/token":
                    return Response(b'{"access_token":"token"}')
                return Response(b'{"id":"trustedFileId_1234567890","size":"4"}')

        opener = Opener()
        updater = mod.GoogleDriveUpdater("client", "secret", "refresh", opener=opener)
        updater.replace("trustedFileId_1234567890", b"data")
        request, timeout = opener.requests[-1]
        self.assertEqual(request.get_method(), "PATCH")
        self.assertIn("/files/trustedFileId_1234567890?uploadType=media", request.full_url)
        self.assertEqual(timeout, mod.HTTP_TIMEOUT_SECONDS)
        self.assertEqual(request.data, b"data")

    def test_drive_updater_refreshes_expired_access_token_once(self) -> None:
        class Response:
            def __init__(self, raw: bytes) -> None:
                self.raw = raw
            def __enter__(self):
                return self
            def __exit__(self, *_args):
                return False
            def read(self) -> bytes:
                return self.raw

        class Opener:
            def __init__(self) -> None:
                self.token_calls = 0
                self.patch_calls = 0
            def urlopen(self, request, timeout):
                if request.full_url == "https://oauth2.googleapis.com/token":
                    self.token_calls += 1
                    return Response(json.dumps({"access_token": f"token-{self.token_calls}"}).encode())
                self.patch_calls += 1
                if self.patch_calls == 1:
                    raise urllib.error.HTTPError(request.full_url, 401, "expired", {}, None)
                self.asserted_authorization = request.headers.get("Authorization")
                return Response(b'{"id":"trustedFileId_1234567890","size":"4"}')

        opener = Opener()
        updater = mod.GoogleDriveUpdater("client", "secret", "refresh", opener=opener)
        updater.replace("trustedFileId_1234567890", b"data")
        self.assertEqual(opener.token_calls, 2)
        self.assertEqual(opener.patch_calls, 2)
        self.assertEqual(opener.asserted_authorization, "Bearer token-2")

    def test_http_classification_keeps_transient_drive_failures_retryable(self) -> None:
        class Opener:
            def urlopen(self, request, timeout):
                raise urllib.error.HTTPError(request.full_url, 503, "unavailable", {}, None)
        updater = mod.GoogleDriveUpdater("client", "secret", "refresh", opener=Opener())
        with self.assertRaises(mod.CheckpointError) as raised:
            updater.replace("trustedFileId_1234567890", b"data")
        self.assertTrue(raised.exception.transient)
        self.assertEqual(raised.exception.category, "drive_http_503")


if __name__ == "__main__":
    unittest.main()
