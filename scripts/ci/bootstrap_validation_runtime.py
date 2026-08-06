#!/usr/bin/env python3
"""Bootstrap the exact locked validation parser using only Python stdlib."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ci_workflows.validation_runtime import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
