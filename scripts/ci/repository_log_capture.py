#!/usr/bin/env python3
"""Keep repository CI stdout/stderr within the reviewed text-evidence bound.

The product process writes into this helper through stdin. The helper continuously
drains that stream while keeping the Central-owned private log as a bounded startup
prefix plus rolling terminal tail. The final workflow scrub remains authoritative
for configured-secret redaction.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys
import tempfile
from typing import BinaryIO

MAX_LOG_BYTES = 12 * 1024 * 1024
TAIL_BYTES = 10 * 1024 * 1024
MAX_PREFIX_BYTES = 128 * 1024
MAX_OVERLAP_BYTES = 1024 * 1024
READ_BYTES = 64 * 1024


class CaptureError(RuntimeError):
    pass


def _regular_file(path: Path) -> None:
    if path.is_symlink() or not path.is_file():
        raise CaptureError("repository CI log must remain one regular non-symlink file")


def _initial_prefix(path: Path) -> bytes:
    _regular_file(path)
    size = path.stat().st_size
    if size > MAX_PREFIX_BYTES:
        raise CaptureError("repository CI pre-entrypoint log exceeds the bounded prefix allowance")
    return path.read_bytes()


def _compact(
    path: Path,
    *,
    prefix: bytes,
    overlap_bytes: int,
    rollovers: int,
    input_bytes: int,
) -> int:
    _regular_file(path)
    size = path.stat().st_size
    retain = TAIL_BYTES + overlap_bytes
    start = max(len(prefix), size - retain)
    with path.open("rb") as source:
        source.seek(start)
        tail = source.read()

    marker = (
        f"\n[central repository log rollover #{rollovers + 1}: "
        f"bounded capture retained startup context and terminal tail after "
        f"{input_bytes} product bytes]\n"
    ).encode("utf-8")
    payload = prefix + marker + tail
    if len(payload) > MAX_LOG_BYTES:
        raise CaptureError("repository CI bounded log compaction exceeded its retained size")

    fd, temporary_name = tempfile.mkstemp(
        prefix=path.name + ".",
        suffix=".tmp",
        dir=path.parent,
    )
    try:
        with os.fdopen(fd, "wb", buffering=0) as output:
            output.write(payload)
        os.chmod(temporary_name, 0o600)
        os.replace(temporary_name, path)
    finally:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
    return rollovers + 1


def capture_stream(
    path: Path,
    source: BinaryIO,
    *,
    overlap_bytes: int,
) -> tuple[int, int]:
    if not 0 <= overlap_bytes <= MAX_OVERLAP_BYTES:
        raise CaptureError("repository CI capture overlap is outside the reviewed bound")

    input_bytes = 0
    rollovers = 0
    failure: CaptureError | None = None
    prefix = b""
    output = None
    try:
        prefix = _initial_prefix(path)
        output = path.open("ab", buffering=0)
    except (CaptureError, OSError):
        failure = CaptureError("repository CI bounded log capture could not initialize")

    try:
        while True:
            block = source.read(READ_BYTES)
            if not block:
                break
            if not isinstance(block, bytes):
                raise CaptureError("repository CI capture source must provide bytes")
            input_bytes += len(block)
            if failure is not None:
                continue
            try:
                assert output is not None
                output.write(block)
                if path.stat().st_size > MAX_LOG_BYTES:
                    output.close()
                    rollovers = _compact(
                        path,
                        prefix=prefix,
                        overlap_bytes=overlap_bytes,
                        rollovers=rollovers,
                        input_bytes=input_bytes,
                    )
                    output = path.open("ab", buffering=0)
            except (OSError, CaptureError):
                failure = CaptureError("repository CI bounded log capture failed")
        if failure is not None:
            raise failure
    finally:
        if output is not None:
            output.close()

    _regular_file(path)
    if path.stat().st_size > MAX_LOG_BYTES:
        raise CaptureError("repository CI bounded log exceeded its retained size")
    return input_bytes, rollovers


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--log-path", required=True)
    parser.add_argument("--overlap-bytes", type=int, required=True)
    args = parser.parse_args()

    try:
        input_bytes, rollovers = capture_stream(
            Path(args.log_path),
            sys.stdin.buffer,
            overlap_bytes=args.overlap_bytes,
        )
    except (CaptureError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 2

    print(
        f"repository CI bounded log capture consumed {input_bytes} product byte(s) "
        f"with {rollovers} rollover(s)",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
