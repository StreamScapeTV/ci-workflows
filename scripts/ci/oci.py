#!/usr/bin/env python3
"""Repository entry point for OCI build planning and execution."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from ci_workflows.ciw_oci import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
