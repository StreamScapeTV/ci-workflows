#!/usr/bin/env python3
"""Compatibility adapter for bounded OCI validation and mapping checks."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ci_workflows.ciw import main as ciw_main  # noqa: E402
from ci_workflows.ciw_oci import main as legacy_main  # noqa: E402


if __name__ == "__main__":
    arguments = sys.argv[1:]
    if arguments[:1] == ["mapping"]:
        raise SystemExit(legacy_main(arguments))
    if arguments[:1] == ["validate"]:
        arguments = arguments[1:]
    raise SystemExit(
        ciw_main(["--root", str(ROOT), "oci", "validate", *arguments])
    )
