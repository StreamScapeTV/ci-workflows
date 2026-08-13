#!/usr/bin/env python3
"""Compatibility wrapper for the stable ``ciw oci publish`` command."""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Mapping, Sequence

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from ci_workflows.ciw import main as ciw_main  # noqa: E402


def main(
    argv: Sequence[str] | None = None,
    environment: Mapping[str, str] | None = None,
) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--phase",
        choices=(
            "plan",
            "authenticate",
            "publish",
            "readback",
            "verify",
            "cleanup",
            "residue",
        ),
        required=True,
    )
    args = parser.parse_args(argv)
    return ciw_main(
        ["--root", str(ROOT), "oci", "publish", "--phase", args.phase],
        environment=dict(os.environ if environment is None else environment),
    )


if __name__ == "__main__":
    raise SystemExit(main())
