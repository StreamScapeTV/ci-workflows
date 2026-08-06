#!/usr/bin/env python3
"""Render or check shared foundation primitive documentation."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ci_workflows.foundation_docs import write_foundation_docs  # noqa: E402
from ci_workflows.foundation_types import FoundationError  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    try:
        path = write_foundation_docs(contract_root=args.root, check=args.check)
    except FoundationError as error:
        print(error.instruction, file=sys.stderr)
        return 2
    print(path.relative_to(args.root))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
