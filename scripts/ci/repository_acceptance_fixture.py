#!/usr/bin/env python3
"""Fixed internal repository-CI acceptance fixture. Not a product/caller surface."""
from __future__ import annotations

import os
from pathlib import Path
import sys
import time

MODES = {"success", "failure", "stall"}


def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1] not in MODES:
        raise SystemExit("repository acceptance fixture mode is invalid")
    mode = sys.argv[1]
    log_dir = Path(os.environ["CI_LOG_DIR"])
    artifact_dir = Path(os.environ["CI_ARTIFACT_DIR"])
    progress = Path(os.environ["CI_PROGRESS_FILE"])
    secret = os.environ["REPOSITORY_ACCEPTANCE_SECRET"]

    log_dir.mkdir(parents=True, exist_ok=True)
    artifact_dir.mkdir(parents=True, exist_ok=True)
    (log_dir / "fixture.log").write_text(
        f"fixture={mode} configured-secret={secret}\n",
        encoding="utf-8",
    )
    progress.write_text(f"mode={mode}\nstate=started\n", encoding="utf-8")

    framework = artifact_dir / "Acceptance.framework"
    headers = framework / "Versions" / "A" / "Headers"
    headers.mkdir(parents=True, exist_ok=True)
    (headers / "Acceptance.h").write_bytes(b"bounded-acceptance-header\n")
    (framework / "Versions" / "Current").symlink_to("A", target_is_directory=True)
    (framework / "Headers").symlink_to("Versions/Current/Headers", target_is_directory=True)

    oversized = artifact_dir / "oversized-artifact.bin"
    oversized.write_bytes(b"must-not-enter-evidence")
    with oversized.open("r+b") as handle:
        handle.truncate(16 * 1024 * 1024 + 1)

    print(f"repository acceptance {mode}: configured-secret={secret}", flush=True)
    if mode == "success":
        progress.write_text("mode=success\nstate=complete\n", encoding="utf-8")
        return 0
    if mode == "failure":
        progress.write_text("mode=failure\nstate=failed\n", encoding="utf-8")
        return 23

    progress.write_text("mode=stall\nstate=stalling\n", encoding="utf-8")
    print("repository acceptance stall is now intentionally silent", flush=True)
    time.sleep(600)
    return 99


if __name__ == "__main__":
    raise SystemExit(main())
