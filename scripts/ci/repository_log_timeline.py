#!/usr/bin/env python3
"""Write bounded Central-owned repository CI timeline markers.

The phase vocabulary is fixed here. Callers may only start/finish these reviewed
Central phases; product code cannot define phase names, metadata keys, or paths.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import time
from typing import Callable

PHASES = {
    "source-admission": 1,
    "private-capabilities": 2,
    "repository-entrypoint": 3,
}
TERMINAL_STATUSES = {"complete", "failed", "cancelled"}
STATE_SCHEMA_VERSION = 1
MAX_STATE_BYTES = 16 * 1024
MAX_MARKER_BYTES = 512


class TimelineError(RuntimeError):
    pass


def _utc_timestamp(unix_ns: int) -> str:
    value = datetime.fromtimestamp(unix_ns / 1_000_000_000, tz=timezone.utc)
    return value.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _validate_regular_file(path: Path, label: str) -> None:
    if path.is_symlink() or not path.is_file():
        raise TimelineError(f"invalid_{label}_path")


def _append_marker(log_path: Path, marker: str) -> None:
    _validate_regular_file(log_path, "log")
    raw = marker.encode("utf-8")
    if len(raw) > MAX_MARKER_BYTES or b"\n" in raw.rstrip(b"\n"):
        raise TimelineError("invalid_marker")
    with log_path.open("ab") as handle:
        handle.write(raw)
        if not raw.endswith(b"\n"):
            handle.write(b"\n")


def _load_state(state_path: Path) -> dict[str, object]:
    if state_path.is_symlink() or not state_path.is_file():
        raise TimelineError("invalid_state_path")
    if state_path.stat().st_size > MAX_STATE_BYTES:
        raise TimelineError("state_too_large")
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise TimelineError("invalid_state_json") from None
    if not isinstance(state, dict) or state.get("schema_version") != STATE_SCHEMA_VERSION:
        raise TimelineError("invalid_state_schema")
    last_completed = state.get("last_completed_ordinal")
    if not isinstance(last_completed, int) or isinstance(last_completed, bool) or not 0 <= last_completed <= len(PHASES):
        raise TimelineError("invalid_state_ordinal")
    active = state.get("active")
    if active is not None:
        if not isinstance(active, dict):
            raise TimelineError("invalid_active_phase")
        name = active.get("name")
        ordinal = active.get("ordinal")
        started_unix_ns = active.get("started_unix_ns")
        started_monotonic_ns = active.get("started_monotonic_ns")
        if (
            not isinstance(name, str)
            or PHASES.get(name) != ordinal
            or not isinstance(started_unix_ns, int)
            or isinstance(started_unix_ns, bool)
            or not isinstance(started_monotonic_ns, int)
            or isinstance(started_monotonic_ns, bool)
        ):
            raise TimelineError("invalid_active_phase")
    return state


def _write_state(state_path: Path, state: dict[str, object]) -> None:
    if state_path.is_symlink():
        raise TimelineError("invalid_state_path")
    state_path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(state, sort_keys=True, separators=(",", ":")) + "\n"
    if len(payload.encode("utf-8")) > MAX_STATE_BYTES:
        raise TimelineError("state_too_large")
    temporary = state_path.with_name(state_path.name + ".tmp")
    temporary.write_text(payload, encoding="utf-8")
    temporary.chmod(0o600)
    temporary.replace(state_path)


def start_phase(
    *,
    log_path: Path,
    state_path: Path,
    phase: str,
    wall_time_ns: Callable[[], int] = time.time_ns,
    monotonic_ns: Callable[[], int] = time.monotonic_ns,
) -> None:
    ordinal = PHASES.get(phase)
    if ordinal is None:
        raise TimelineError("unsupported_phase")
    if state_path.exists():
        state = _load_state(state_path)
    else:
        state = {
            "schema_version": STATE_SCHEMA_VERSION,
            "last_completed_ordinal": 0,
            "active": None,
        }
    if state.get("active") is not None:
        raise TimelineError("phase_already_active")
    if state.get("last_completed_ordinal") != ordinal - 1:
        raise TimelineError("phase_out_of_order")
    started_unix_ns = wall_time_ns()
    started_monotonic_ns = monotonic_ns()
    active = {
        "name": phase,
        "ordinal": ordinal,
        "started_unix_ns": started_unix_ns,
        "started_monotonic_ns": started_monotonic_ns,
    }
    state["active"] = active
    _write_state(state_path, state)
    _append_marker(
        log_path,
        f"CENTRAL phase={ordinal:02d} name={phase} status=running "
        f"started={_utc_timestamp(started_unix_ns)}",
    )


def _activity(log_path: Path, progress_path: Path | None) -> tuple[int, int]:
    _validate_regular_file(log_path, "log")
    log_stat = log_path.stat()
    latest_mtime_ns = log_stat.st_mtime_ns
    if progress_path is not None and progress_path.exists():
        if not progress_path.is_symlink() and progress_path.is_file():
            latest_mtime_ns = max(latest_mtime_ns, progress_path.stat().st_mtime_ns)
    return latest_mtime_ns, log_stat.st_size


def finish_phase(
    *,
    log_path: Path,
    state_path: Path,
    phase: str,
    status: str,
    progress_path: Path | None = None,
    wall_time_ns: Callable[[], int] = time.time_ns,
    monotonic_ns: Callable[[], int] = time.monotonic_ns,
) -> None:
    ordinal = PHASES.get(phase)
    if ordinal is None:
        raise TimelineError("unsupported_phase")
    if status not in TERMINAL_STATUSES:
        raise TimelineError("unsupported_status")
    state = _load_state(state_path)
    active = state.get("active")
    if not isinstance(active, dict) or active.get("name") != phase or active.get("ordinal") != ordinal:
        raise TimelineError("phase_not_active")
    completed_unix_ns = wall_time_ns()
    elapsed_ns = max(0, monotonic_ns() - int(active["started_monotonic_ns"]))
    marker = (
        f"CENTRAL phase={ordinal:02d} name={phase} status={status} "
        f"started={_utc_timestamp(int(active['started_unix_ns']))} "
        f"completed={_utc_timestamp(completed_unix_ns)} elapsed_ms={elapsed_ns // 1_000_000}"
    )
    if phase == "repository-entrypoint":
        last_activity_ns, log_bytes = _activity(log_path, progress_path)
        marker += f" last_activity={_utc_timestamp(last_activity_ns)} log_bytes={log_bytes}"
    _append_marker(log_path, marker)
    state["active"] = None
    state["last_completed_ordinal"] = ordinal
    _write_state(state_path, state)


def finish_active_phase(
    *,
    log_path: Path,
    state_path: Path,
    status: str,
    progress_path: Path | None = None,
    wall_time_ns: Callable[[], int] = time.time_ns,
    monotonic_ns: Callable[[], int] = time.monotonic_ns,
) -> bool:
    if status not in {"failed", "cancelled"}:
        raise TimelineError("unsupported_reconciliation_status")
    if not state_path.exists():
        return False
    state = _load_state(state_path)
    active = state.get("active")
    if active is None:
        return False
    if not isinstance(active, dict) or not isinstance(active.get("name"), str):
        raise TimelineError("invalid_active_phase")
    finish_phase(
        log_path=log_path,
        state_path=state_path,
        phase=active["name"],
        status=status,
        progress_path=progress_path,
        wall_time_ns=wall_time_ns,
        monotonic_ns=monotonic_ns,
    )
    return True


def active_entrypoint_marker(
    *,
    log_path: Path,
    state_path: Path,
    progress_path: Path | None = None,
    wall_time_ns: Callable[[], int] = time.time_ns,
    monotonic_ns: Callable[[], int] = time.monotonic_ns,
) -> bytes:
    state = _load_state(state_path)
    active = state.get("active")
    if not isinstance(active, dict) or active.get("name") != "repository-entrypoint":
        return b""
    last_activity_ns, log_bytes = _activity(log_path, progress_path)
    elapsed_ns = max(0, monotonic_ns() - int(active["started_monotonic_ns"]))
    marker = (
        "CENTRAL phase=03 name=repository-entrypoint status=running "
        f"started={_utc_timestamp(int(active['started_unix_ns']))} "
        f"observed={_utc_timestamp(wall_time_ns())} "
        f"elapsed_ms={elapsed_ns // 1_000_000} "
        f"last_activity={_utc_timestamp(last_activity_ns)} log_bytes={log_bytes}\n"
    ).encode("utf-8")
    if len(marker) > MAX_MARKER_BYTES:
        raise TimelineError("invalid_marker")
    return marker


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    start = subparsers.add_parser("start")
    start.add_argument("--phase", choices=tuple(PHASES), required=True)
    start.add_argument("--log-path", required=True)
    start.add_argument("--state-path", required=True)

    finish = subparsers.add_parser("finish")
    finish.add_argument("--phase", choices=tuple(PHASES), required=True)
    finish.add_argument("--status", choices=tuple(sorted(TERMINAL_STATUSES)), required=True)
    finish.add_argument("--log-path", required=True)
    finish.add_argument("--state-path", required=True)
    finish.add_argument("--progress-path")

    reconcile = subparsers.add_parser("finish-active")
    reconcile.add_argument("--status", choices=("cancelled", "failed"), required=True)
    reconcile.add_argument("--log-path", required=True)
    reconcile.add_argument("--state-path", required=True)
    reconcile.add_argument("--progress-path")

    args = parser.parse_args()
    try:
        if args.command == "start":
            start_phase(
                log_path=Path(args.log_path),
                state_path=Path(args.state_path),
                phase=args.phase,
            )
        elif args.command == "finish":
            finish_phase(
                log_path=Path(args.log_path),
                state_path=Path(args.state_path),
                phase=args.phase,
                status=args.status,
                progress_path=Path(args.progress_path) if args.progress_path else None,
            )
        else:
            finish_active_phase(
                log_path=Path(args.log_path),
                state_path=Path(args.state_path),
                status=args.status,
                progress_path=Path(args.progress_path) if args.progress_path else None,
            )
    except TimelineError as exc:
        print(f"repository CI timeline failed: {exc}", file=__import__("sys").stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
