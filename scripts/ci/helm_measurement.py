#!/usr/bin/env python3
"""Bounded CLI for Helm publication runner measurement."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ci_workflows.ciw_types import write_command_file  # noqa: E402
from ci_workflows.helm_measurement import monitor, start, stop  # noqa: E402
from ci_workflows.helm_types import HelmValidationError  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("start", "monitor", "stop"))
    parser.add_argument("--state-dir")
    args = parser.parse_args()
    try:
        if args.phase == "monitor":
            if not args.state_dir:
                return 2
            return monitor(Path(args.state_dir))
        values = start(ROOT, os.environ) if args.phase == "start" else stop(ROOT, os.environ)
        output = os.environ.get("GITHUB_OUTPUT", "")
        if output:
            write_command_file(Path(output), values)
        else:
            sys.stdout.write(json.dumps(values, sort_keys=True) + "\n")
        return 0
    except HelmValidationError as error:
        sys.stderr.write(f"helm runner measurement failed: {error.code}\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
