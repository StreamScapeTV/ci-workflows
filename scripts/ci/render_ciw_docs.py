#!/usr/bin/env python3
"""Render or verify deterministic ``ciw`` command documentation."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ci_workflows.ciw_docs import write_ciw_docs  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    write_ciw_docs(contract_root=ROOT, check=args.check)
    print("ciw documentation is current" if args.check else "rendered ciw documentation")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
