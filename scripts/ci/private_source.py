#!/usr/bin/env python3
"""Resolve one configured private StreamScapeTV branch to an exact commit."""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ci_workflows.ciw_types import write_command_file
from ci_workflows.private_source import PrivateSourceError, resolve_private_branch


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--repository-name", required=True)
    result.add_argument("--branch", required=True)
    result.add_argument("--github-output", type=Path, required=True)
    return result


def main() -> int:
    args = parser().parse_args()
    try:
        sha = resolve_private_branch(
            repository_name=args.repository_name,
            branch=args.branch,
            token=os.environ.get("CHECKOUT_TOKEN", ""),
        )
    except PrivateSourceError as error:
        print(error.code, file=sys.stderr)
        return 2
    write_command_file(args.github_output, {"sha": sha})
    print("Configured private branch resolved to one exact commit.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
