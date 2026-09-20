#!/usr/bin/env python3
"""Run one Central-owned repository command with bounded signal/timeout supervision."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

TIMEOUT_EXIT = 124
TERM_GRACE_SECONDS = 5.0


def _shell_status(status: int) -> int:
    return status if status >= 0 else 128 + (-status)


def _terminate_group(process: subprocess.Popen[bytes], sig: int) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, sig)
    except ProcessLookupError:
        return


def run_command(*, cwd: Path, log_path: Path, command: list[str], timeout_seconds: float | None = None) -> int:
    if not command:
        raise SystemExit("repository command helper requires one command")
    cwd = cwd.resolve(strict=True)
    if log_path.is_symlink():
        raise SystemExit("repository command log must not be a symlink")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    if not log_path.exists():
        log_path.touch(mode=0o600)
    if not log_path.is_file():
        raise SystemExit("repository command log must be one regular file")

    previous_handlers: dict[int, object] = {}
    process: subprocess.Popen[bytes] | None = None

    def forward(signum: int, _frame: object) -> None:
        if process is not None:
            _terminate_group(process, signum)

    for signum in (signal.SIGTERM, signal.SIGINT):
        previous_handlers[signum] = signal.getsignal(signum)
        signal.signal(signum, forward)

    try:
        with log_path.open("ab", buffering=0) as output:
            process = subprocess.Popen(
                command,
                cwd=cwd,
                stdout=output,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                env=os.environ.copy(),
            )
            try:
                if timeout_seconds is None:
                    return _shell_status(process.wait())
                if timeout_seconds <= 0 or timeout_seconds > 300:
                    raise SystemExit("repository command timeout is outside the internal reviewed bound")
                return _shell_status(process.wait(timeout=timeout_seconds))
            except subprocess.TimeoutExpired:
                _terminate_group(process, signal.SIGTERM)
                deadline = time.monotonic() + TERM_GRACE_SECONDS
                while process.poll() is None and time.monotonic() < deadline:
                    time.sleep(0.05)
                if process.poll() is None:
                    _terminate_group(process, signal.SIGKILL)
                process.wait()
                return TIMEOUT_EXIT
    finally:
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cwd", type=Path, required=True)
    parser.add_argument("--log-path", type=Path, required=True)
    parser.add_argument("--timeout-seconds", type=float)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command
    if command and command[0] == "--":
        command = command[1:]
    return run_command(
        cwd=args.cwd,
        log_path=args.log_path,
        command=command,
        timeout_seconds=args.timeout_seconds,
    )


if __name__ == "__main__":
    raise SystemExit(main())
