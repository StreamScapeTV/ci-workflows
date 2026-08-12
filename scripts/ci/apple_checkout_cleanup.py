#!/usr/bin/env python3
"""Remove one fixed Apple workflow checkout without following links."""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Sequence

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ci_workflows.apple_execution import _remove_no_follow

_TARGETS = {"central": ".ciw", "source": "source"}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target", choices=sorted(_TARGETS))
    args = parser.parse_args(argv)
    path = Path.cwd() / _TARGETS[args.target]
    _remove_no_follow(path)
    if os.path.lexists(path):
        raise SystemExit(f"fixed Apple checkout remains: {args.target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
