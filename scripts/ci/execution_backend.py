#!/usr/bin/env python3
"""Resolve one bounded execution backend."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ci_workflows import runners
from ci_workflows.ciw_types import write_command_file
from ci_workflows.execution_backends import ExecutionBackendError, resolve_execution_backend


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--root", type=Path, default=ROOT)
    result.add_argument("--workflow-api", required=True)
    result.add_argument("--source-trust", required=True)
    result.add_argument("--execution-backend", required=True)
    result.add_argument("--profile")
    result.add_argument("--github-output", type=Path)
    return result


def main() -> int:
    args = parser().parse_args()
    root = args.root.resolve()
    try:
        organization = runners.resolve_runner_profile(
            runners.load_runner_contract(root),
            workflow_api=args.workflow_api,
            source_trust=args.source_trust,
            requested_profile=args.profile,
        )
        resolved = resolve_execution_backend(
            execution_backend=args.execution_backend,
            execution_profile=organization.execution_profile,
            organization_runs_on=organization.runs_on,
        )
    except (runners.RunnerContractError, ExecutionBackendError) as error:
        print(getattr(error, "code", str(error)), file=sys.stderr)
        return 2

    payload = resolved.as_dict()
    if args.github_output is not None:
        write_command_file(
            args.github_output,
            {
                "execution_backend": str(payload["execution_backend"]),
                "execution_profile": str(payload["execution_profile"]),
                "runs_on_json": str(payload["runs_on_json"]),
            },
        )
    print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
