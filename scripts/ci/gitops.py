#!/usr/bin/env python3
"""Compatibility adapter for bounded GitOps validation."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ci_workflows.ciw_gitops import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main(["--root", str(ROOT), *sys.argv[1:]]))
