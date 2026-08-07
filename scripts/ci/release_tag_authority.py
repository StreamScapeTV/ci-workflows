#!/usr/bin/env python3
"""Compatibility wrapper for exact immutable release-tag authority."""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Sequence

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def main(argv: Sequence[str] | None = None) -> int:
    args = list(argv or sys.argv[1:])
    if args:
        print("release tag authority accepts no positional arguments", file=sys.stderr)
        return 2
    phase = os.environ.get("INPUT_PHASE", "resolve").strip()
    if phase not in {"resolve", "revalidate"}:
        print(
            "release tag authority rejected: unknown_release_authority_phase",
            file=sys.stderr,
        )
        return 2
    from ci_workflows.ciw import main as ciw_main

    return ciw_main(
        [
            "--root",
            str(ROOT),
            "release-tag",
            phase,
        ]
    )


if __name__ == "__main__":
    raise SystemExit(main())
