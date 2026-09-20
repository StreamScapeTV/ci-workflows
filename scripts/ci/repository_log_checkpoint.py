#!/usr/bin/env python3
"""Periodically checkpoint one scrubbed repository CI log into an existing Drive file.

The workflow creates the deterministic Drive object through the reviewed google-drive
action before product execution. This helper receives only that trusted file id and
may replace its bytes; it cannot create folders/files or select another destination.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import signal
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Callable, Mapping, Protocol

import repository_log_timeline as timeline

MAX_LOG_BYTES = 16 * 1024 * 1024
INTERVAL_SECONDS = 180
HTTP_TIMEOUT_SECONDS = 30
MAX_UPLOAD_ATTEMPTS = 3
RETRY_DELAYS_SECONDS = (1, 2)
FILE_ID_PATTERN = re.compile(r"[A-Za-z0-9_-]{10,256}\Z")
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}\Z")


class CheckpointError(RuntimeError):
    def __init__(self, category: str, *, transient: bool = False) -> None:
        super().__init__(category)
        self.category = category
        self.transient = transient


class DriveUpdater(Protocol):
    def replace(self, file_id: str, data: bytes) -> None: ...


def _scrub_values(environ: Mapping[str, str]) -> tuple[bytes, ...]:
    values: list[bytes] = []
    seen: set[bytes] = set()
    for name, value in environ.items():
        if not name.startswith("CI_SECRET_") or not value:
            continue
        raw = value.encode()
        for candidate in (raw, raw.replace(b"\n", b"\\n")):
            if candidate and candidate not in seen:
                seen.add(candidate)
                values.append(candidate)
    return tuple(values)


def scrubbed_snapshot(
    log_path: Path,
    environ: Mapping[str, str],
    *,
    timeline_state_path: Path | None = None,
    progress_path: Path | None = None,
) -> bytes:
    if log_path.is_symlink() or not log_path.is_file():
        raise CheckpointError("invalid_log_path")
    size = log_path.stat().st_size
    if size > MAX_LOG_BYTES:
        raise CheckpointError("log_too_large")
    data = log_path.read_bytes()
    if len(data) > MAX_LOG_BYTES:
        raise CheckpointError("log_too_large")
    for raw in _scrub_values(environ):
        data = data.replace(raw, b"[REDACTED]")
    if timeline_state_path is not None and timeline_state_path.exists():
        try:
            marker = timeline.active_entrypoint_marker(
                log_path=log_path,
                state_path=timeline_state_path,
                progress_path=progress_path,
            )
        except timeline.TimelineError as exc:
            print(
                f"repository log checkpoint timeline warning: {exc}",
                file=__import__("sys").stderr,
            )
        else:
            data += marker
    return data


@dataclass
class CheckpointState:
    last_sha256: str
    attempts: int = 0
    uploads: int = 0
    unchanged: int = 0
    transient_failures: int = 0
    last_error: str = ""

    def payload(self) -> dict[str, object]:
        return {
            "attempts": self.attempts,
            "last_error": self.last_error,
            "last_sha256": self.last_sha256,
            "transient_failures": self.transient_failures,
            "unchanged": self.unchanged,
            "uploads": self.uploads,
        }


def _write_status(path: Path, state: CheckpointState) -> None:
    if path.is_symlink():
        raise CheckpointError("invalid_status_path")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(state.payload(), sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    temporary.chmod(0o600)
    temporary.replace(path)


def _upload_with_retries(
    updater: DriveUpdater,
    file_id: str,
    data: bytes,
    *,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> None:
    for attempt in range(1, MAX_UPLOAD_ATTEMPTS + 1):
        try:
            updater.replace(file_id, data)
            return
        except CheckpointError as exc:
            if not exc.transient or attempt >= MAX_UPLOAD_ATTEMPTS:
                raise
            sleep_fn(RETRY_DELAYS_SECONDS[attempt - 1])
    raise AssertionError("bounded upload retry loop exhausted unexpectedly")


def checkpoint_once(
    *,
    log_path: Path,
    file_id: str,
    state: CheckpointState,
    status_path: Path,
    updater: DriveUpdater,
    environ: Mapping[str, str],
    sleep_fn: Callable[[float], None] = time.sleep,
    timeline_state_path: Path | None = None,
    progress_path: Path | None = None,
) -> str:
    state.attempts += 1
    data = scrubbed_snapshot(
        log_path,
        environ,
        timeline_state_path=timeline_state_path,
        progress_path=progress_path,
    )
    digest = hashlib.sha256(data).hexdigest()
    if digest == state.last_sha256:
        state.unchanged += 1
        state.last_error = ""
        _write_status(status_path, state)
        return "unchanged"
    try:
        _upload_with_retries(updater, file_id, data, sleep_fn=sleep_fn)
    except CheckpointError as exc:
        state.last_error = exc.category
        if exc.transient:
            state.transient_failures += 1
        _write_status(status_path, state)
        raise
    state.last_sha256 = digest
    state.uploads += 1
    state.last_error = ""
    _write_status(status_path, state)
    return "uploaded"


@dataclass
class GoogleDriveUpdater:
    client_id: str
    client_secret: str
    refresh_token: str
    opener: object = urllib.request
    _access_token: str | None = None

    def _request(self, request: urllib.request.Request) -> bytes:
        try:
            with self.opener.urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:  # type: ignore[attr-defined]
                return response.read()
        except urllib.error.HTTPError as exc:
            transient = exc.code in (408, 429) or 500 <= exc.code <= 599
            raise CheckpointError(
                f"drive_http_{exc.code}", transient=transient
            ) from None
        except (OSError, urllib.error.URLError):
            raise CheckpointError("drive_transport", transient=True) from None

    def access_token(self) -> str:
        if self._access_token:
            return self._access_token
        encoded = urllib.parse.urlencode(
            {
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "refresh_token": self.refresh_token,
                "grant_type": "refresh_token",
            }
        ).encode()
        request = urllib.request.Request(
            "https://oauth2.googleapis.com/token",
            data=encoded,
            method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        raw = self._request(request)
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise CheckpointError("oauth_invalid_json") from None
        token = value.get("access_token") if isinstance(value, dict) else None
        if not isinstance(token, str) or not token:
            raise CheckpointError("oauth_missing_access_token")
        self._access_token = token
        return token

    def replace(self, file_id: str, data: bytes) -> None:
        if FILE_ID_PATTERN.fullmatch(file_id) is None:
            raise CheckpointError("invalid_drive_file_id")
        raw = b""
        for auth_attempt in range(2):
            request = urllib.request.Request(
                "https://www.googleapis.com/upload/drive/v3/files/"
                + urllib.parse.quote(file_id, safe="")
                + "?uploadType=media&fields=id,size",
                data=data,
                method="PATCH",
                headers={
                    "Authorization": f"Bearer {self.access_token()}",
                    "Content-Type": "text/plain",
                    "Content-Length": str(len(data)),
                },
            )
            try:
                raw = self._request(request)
                break
            except CheckpointError as exc:
                if exc.category == "drive_http_401" and auth_attempt == 0:
                    self._access_token = None
                    continue
                raise
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise CheckpointError("drive_invalid_json") from None
        if not isinstance(value, dict) or value.get("id") != file_id:
            raise CheckpointError("drive_identity_mismatch")
        size = value.get("size")
        try:
            observed_size = int(size)
        except (TypeError, ValueError):
            raise CheckpointError("drive_size_missing") from None
        if observed_size != len(data):
            raise CheckpointError("drive_size_mismatch")


def run_loop(
    *,
    log_path: Path,
    file_id: str,
    seed_sha256: str,
    status_path: Path,
    updater: DriveUpdater,
    environ: Mapping[str, str],
    stop_event: threading.Event,
    interval_seconds: float = INTERVAL_SECONDS,
    sleep_fn: Callable[[float], None] = time.sleep,
    timeline_state_path: Path | None = None,
    progress_path: Path | None = None,
) -> CheckpointState:
    if SHA256_PATTERN.fullmatch(seed_sha256) is None:
        raise CheckpointError("invalid_seed_sha256")
    if interval_seconds <= 0 or interval_seconds > 300:
        raise CheckpointError("invalid_interval")
    state = CheckpointState(last_sha256=seed_sha256)
    _write_status(status_path, state)

    permanent_failure = False
    while not stop_event.wait(interval_seconds):
        try:
            checkpoint_once(
                log_path=log_path,
                file_id=file_id,
                state=state,
                status_path=status_path,
                updater=updater,
                environ=environ,
                sleep_fn=sleep_fn,
                timeline_state_path=timeline_state_path,
                progress_path=progress_path,
            )
        except CheckpointError as exc:
            print(
                f"repository log checkpoint warning: {exc.category}",
                file=__import__("sys").stderr,
            )
            if not exc.transient:
                permanent_failure = True
                break

    if not permanent_failure:
        try:
            checkpoint_once(
                log_path=log_path,
                file_id=file_id,
                state=state,
                status_path=status_path,
                updater=updater,
                environ=environ,
                sleep_fn=sleep_fn,
                timeline_state_path=timeline_state_path,
                progress_path=progress_path,
            )
        except CheckpointError as exc:
            print(
                f"repository log final checkpoint warning: {exc.category}",
                file=__import__("sys").stderr,
            )
    return state


def _required_env(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        raise CheckpointError(f"missing_{name.lower()}")
    return value


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--log-path", required=True)
    parser.add_argument("--file-id", required=True)
    parser.add_argument("--seed-sha256", required=True)
    parser.add_argument("--status-path", required=True)
    parser.add_argument("--timeline-state-path")
    parser.add_argument("--progress-path")
    args = parser.parse_args()

    log_path = Path(args.log_path)
    status_path = Path(args.status_path)
    if FILE_ID_PATTERN.fullmatch(args.file_id) is None:
        raise SystemExit("repository log checkpoint received an invalid fixed Drive file id")
    if SHA256_PATTERN.fullmatch(args.seed_sha256) is None:
        raise SystemExit("repository log checkpoint received an invalid seed digest")

    try:
        updater = GoogleDriveUpdater(
            client_id=_required_env("GOOGLE_DRIVE_CLIENT_ID"),
            client_secret=_required_env("GOOGLE_DRIVE_CLIENT_SECRET"),
            refresh_token=_required_env("GOOGLE_DRIVE_REFRESH_TOKEN"),
        )
    except CheckpointError as exc:
        print(f"repository log checkpoint configuration failed: {exc.category}", file=__import__("sys").stderr)
        return 2

    stop_event = threading.Event()

    def stop(_signum: int, _frame: object) -> None:
        stop_event.set()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    try:
        run_loop(
            log_path=log_path,
            file_id=args.file_id,
            seed_sha256=args.seed_sha256,
            status_path=status_path,
            updater=updater,
            environ=os.environ,
            stop_event=stop_event,
            timeline_state_path=Path(args.timeline_state_path) if args.timeline_state_path else None,
            progress_path=Path(args.progress_path) if args.progress_path else None,
        )
    except CheckpointError as exc:
        print(f"repository log checkpoint failed: {exc.category}", file=__import__("sys").stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
