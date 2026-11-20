#!/usr/bin/env python3
"""Periodically checkpoint one scrubbed repository CI log into one S3 object.

The workflow creates the deterministic S3 object through the reviewed s3-upload
action before product execution. This helper receives only that trusted object key
(which must stay under logs/) and may replace its bytes; it cannot select another
destination.
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
from dataclasses import dataclass
from typing import Callable, Mapping, Protocol

import repository_log_timeline as timeline
import s3_put

MAX_LOG_BYTES = 16 * 1024 * 1024
MAX_RUNTIME_PROBE_BYTES = 4 * 1024 * 1024
RUNTIME_PROBE_TAIL_BYTES = 64 * 1024
INTERVAL_SECONDS = 180
MAX_UPLOAD_ATTEMPTS = 3
RETRY_DELAYS_SECONDS = (1, 2)
OBJECT_KEY_PATTERN = re.compile(r"logs/[A-Za-z0-9][A-Za-z0-9._+-]{0,127}(?:/[A-Za-z0-9][A-Za-z0-9._+-]{0,127}){1,3}\Z")
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}\Z")


class CheckpointError(RuntimeError):
    def __init__(self, category: str, *, transient: bool = False) -> None:
        super().__init__(category)
        self.category = category
        self.transient = transient


class ObjectUpdater(Protocol):
    def replace(self, object_key: str, data: bytes) -> None: ...


def _scrub_values(environ: Mapping[str, str]) -> tuple[bytes, ...]:
    values: list[bytes] = []
    seen: set[bytes] = set()
    for name, value in environ.items():
        if not name.startswith("CI_SECRET_") or not value:
            continue
        raw = value.encode()
        candidates = [raw, raw.replace(b"\n", b"\\n")]
        if name.startswith("CI_SECRET_FIXTURE_"):
            candidates.append(json.dumps(value)[1:-1].encode())
        for candidate in candidates:
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
    runtime_probe_path: Path | None = None,
) -> bytes:
    if log_path.is_symlink() or not log_path.is_file():
        raise CheckpointError("invalid_log_path")
    size = log_path.stat().st_size
    if size > MAX_LOG_BYTES:
        raise CheckpointError("log_too_large")
    data = log_path.read_bytes()
    if len(data) > MAX_LOG_BYTES:
        raise CheckpointError("log_too_large")
    if runtime_probe_path is not None and runtime_probe_path.exists():
        if runtime_probe_path.is_symlink() or not runtime_probe_path.is_file():
            raise CheckpointError("invalid_runtime_probe_path")
        probe_size = runtime_probe_path.stat().st_size
        if probe_size > MAX_RUNTIME_PROBE_BYTES:
            raise CheckpointError("runtime_probe_too_large")
        with runtime_probe_path.open("rb") as handle:
            handle.seek(max(0, probe_size - RUNTIME_PROBE_TAIL_BYTES))
            probe_tail = handle.read(RUNTIME_PROBE_TAIL_BYTES)
        if probe_tail:
            data += b"\n--- central runtime probe tail ---\n" + probe_tail

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
    updater: ObjectUpdater,
    object_key: str,
    data: bytes,
    *,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> None:
    for attempt in range(1, MAX_UPLOAD_ATTEMPTS + 1):
        try:
            updater.replace(object_key, data)
            return
        except CheckpointError as exc:
            if not exc.transient or attempt >= MAX_UPLOAD_ATTEMPTS:
                raise
            sleep_fn(RETRY_DELAYS_SECONDS[attempt - 1])
    raise AssertionError("bounded upload retry loop exhausted unexpectedly")


def checkpoint_once(
    *,
    log_path: Path,
    object_key: str,
    state: CheckpointState,
    status_path: Path,
    updater: ObjectUpdater,
    environ: Mapping[str, str],
    sleep_fn: Callable[[float], None] = time.sleep,
    timeline_state_path: Path | None = None,
    progress_path: Path | None = None,
    runtime_probe_path: Path | None = None,
) -> str:
    state.attempts += 1
    data = scrubbed_snapshot(
        log_path,
        environ,
        timeline_state_path=timeline_state_path,
        progress_path=progress_path,
        runtime_probe_path=runtime_probe_path,
    )
    digest = hashlib.sha256(data).hexdigest()
    if digest == state.last_sha256:
        state.unchanged += 1
        state.last_error = ""
        _write_status(status_path, state)
        return "unchanged"
    try:
        _upload_with_retries(updater, object_key, data, sleep_fn=sleep_fn)
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


class S3Updater:
    """Replace the bytes of one existing object through the shared SigV4 client."""

    def __init__(self, client: s3_put.Client) -> None:
        self._client = client

    def replace(self, object_key: str, data: bytes) -> None:
        if OBJECT_KEY_PATTERN.fullmatch(object_key) is None:
            raise CheckpointError("invalid_object_key")
        try:
            self._client.put(object_key, data, "text/plain")
        except s3_put.S3Error as exc:
            raise CheckpointError(exc.category, transient=exc.transient) from None


def run_loop(
    *,
    log_path: Path,
    object_key: str,
    seed_sha256: str,
    status_path: Path,
    updater: ObjectUpdater,
    environ: Mapping[str, str],
    stop_event: threading.Event,
    interval_seconds: float = INTERVAL_SECONDS,
    sleep_fn: Callable[[float], None] = time.sleep,
    timeline_state_path: Path | None = None,
    progress_path: Path | None = None,
    runtime_probe_path: Path | None = None,
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
                object_key=object_key,
                state=state,
                status_path=status_path,
                updater=updater,
                environ=environ,
                sleep_fn=sleep_fn,
                timeline_state_path=timeline_state_path,
                progress_path=progress_path,
                runtime_probe_path=runtime_probe_path,
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
                object_key=object_key,
                state=state,
                status_path=status_path,
                updater=updater,
                environ=environ,
                sleep_fn=sleep_fn,
                timeline_state_path=timeline_state_path,
                progress_path=progress_path,
                runtime_probe_path=runtime_probe_path,
            )
        except CheckpointError as exc:
            print(
                f"repository log final checkpoint warning: {exc.category}",
                file=__import__("sys").stderr,
            )
    return state


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--log-path", required=True)
    parser.add_argument("--object-key", required=True)
    parser.add_argument("--seed-sha256", required=True)
    parser.add_argument("--status-path", required=True)
    parser.add_argument("--timeline-state-path")
    parser.add_argument("--progress-path")
    parser.add_argument("--runtime-probe-path")
    args = parser.parse_args()

    log_path = Path(args.log_path)
    status_path = Path(args.status_path)
    if OBJECT_KEY_PATTERN.fullmatch(args.object_key) is None:
        raise SystemExit("repository log checkpoint received an invalid fixed S3 object key")
    if SHA256_PATTERN.fullmatch(args.seed_sha256) is None:
        raise SystemExit("repository log checkpoint received an invalid seed digest")

    try:
        updater = S3Updater(s3_put.Client(os.environ))
    except s3_put.S3Error as exc:
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
            object_key=args.object_key,
            seed_sha256=args.seed_sha256,
            status_path=status_path,
            updater=updater,
            environ=os.environ,
            stop_event=stop_event,
            timeline_state_path=Path(args.timeline_state_path) if args.timeline_state_path else None,
            progress_path=Path(args.progress_path) if args.progress_path else None,
            runtime_probe_path=Path(args.runtime_probe_path) if args.runtime_probe_path else None,
        )
    except CheckpointError as exc:
        print(f"repository log checkpoint failed: {exc.category}", file=__import__("sys").stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
