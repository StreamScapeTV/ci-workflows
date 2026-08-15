#!/usr/bin/env python3
"""Compatibility adapter for bounded physical-device validation."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ci_workflows.ciw_device import main as device_main  # noqa: E402


if __name__ == "__main__":
    arguments = sys.argv[1:]
    if arguments[:1] == ["validate"]:
        arguments = arguments[1:]
    if len(arguments) >= 2 and arguments[0] == "--phase":
        arguments = [arguments[1], *arguments[2:]]
    raise SystemExit(device_main(["--root", str(ROOT), *arguments]))
