#!/usr/bin/env python3
"""Stable action adapter for #338 Android completion validators."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ci_workflows.ciw_android_completion import (  # noqa: E402
    execute_android_live_validate,
    execute_android_release_validate,
)
from ci_workflows.ciw_types import CIWError, default_context  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("domain", choices=("live", "release"))
    parser.add_argument("--phase", choices=("plan", "execute", "cleanup", "residue"), default="execute")
    parser.add_argument("--source-root", default="source")
    args = parser.parse_args()
    context = default_context(ROOT, stdout=sys.stdout, stderr=sys.stderr)
    try:
        result = (
            execute_android_live_validate(args, context)
            if args.domain == "live"
            else execute_android_release_validate(args, context)
        )
        result.emit(context)
        return 0
    except CIWError as error:
        print(f"{error.domain}:{error.code}", file=sys.stderr)
        return error.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
