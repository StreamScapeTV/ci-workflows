#!/usr/bin/env python3
"""CLI adapter for bounded organization maintenance operations."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from ci_workflows.maintenance import (  # noqa: E402
    GitHubApi,
    artifacts,
    branches,
    conformance,
    render_result,
    runner_retry,
)
from ci_workflows.maintenance_contract import (  # noqa: E402
    MaintenanceError,
    load_contract,
)


def _bool(value: str) -> bool:
    normalized = value.casefold()
    if normalized == "true":
        return True
    if normalized == "false":
        return False
    raise argparse.ArgumentTypeError("expected true or false")


def _out(values: dict[str, str]) -> None:
    path = os.environ.get("GITHUB_OUTPUT", "")
    if not path:
        return
    with Path(path).open("a", encoding="utf-8") as output:
        for name, value in values.items():
            if "\n" in value or "\r" in value:
                raise MaintenanceError("output_invalid")
            output.write(f"{name}={value}\n")


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser()
    sub = root.add_subparsers(dest="operation", required=True)

    artifacts_parser = sub.add_parser("artifacts")
    artifacts_parser.add_argument("--repository-scope", default="")
    artifacts_parser.add_argument("--dry-run", type=_bool, required=True)
    artifacts_parser.add_argument("--request-id", required=True)

    branches_parser = sub.add_parser("branches")
    branches_parser.add_argument("--project-id", required=True)
    branches_parser.add_argument("--pr-number", type=int)
    branches_parser.add_argument("--expected-head-sha", required=True)
    branches_parser.add_argument("--dry-run", type=_bool, required=True)
    branches_parser.add_argument("--request-id", required=True)

    conformance_parser = sub.add_parser("conformance")
    conformance_parser.add_argument("--repository-scope", default="")
    conformance_parser.add_argument("--shared-reference-target-sha", default="")
    conformance_parser.add_argument("--dry-run", type=_bool, required=True)
    conformance_parser.add_argument("--request-id", required=True)

    retry_parser = sub.add_parser("runner-retry")
    retry_parser.add_argument("--project-id", required=True)
    retry_parser.add_argument("--run-id", type=int, required=True)
    retry_parser.add_argument("--expected-head-sha", required=True)
    retry_parser.add_argument("--dry-run", type=_bool, required=True)
    retry_parser.add_argument("--request-id", required=True)
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    token = os.environ.get("MAINTENANCE_GITHUB_TOKEN", "")
    if not token:
        print("maintenance credential is required", file=sys.stderr)
        return 2
    contract = load_contract(ROOT)
    api = GitHubApi(
        token,
        api_url=os.environ.get("GITHUB_API_URL", "https://api.github.com"),
    )
    try:
        if args.operation == "artifacts":
            result = artifacts(
                contract,
                api,
                root=ROOT,
                repository_scope=args.repository_scope,
                dry_run=args.dry_run,
                request_id=args.request_id,
            )
        elif args.operation == "branches":
            result = branches(
                contract,
                api,
                project_id=args.project_id,
                pr_number=args.pr_number,
                expected_head_sha=args.expected_head_sha,
                dry_run=args.dry_run,
                request_id=args.request_id,
            )
        elif args.operation == "conformance":
            result = conformance(
                contract,
                api,
                root=ROOT,
                repository_scope=args.repository_scope,
                shared_reference_target_sha=args.shared_reference_target_sha,
                dry_run=args.dry_run,
                request_id=args.request_id,
            )
        else:
            result = runner_retry(
                contract,
                api,
                root=ROOT,
                project_id=args.project_id,
                run_id=args.run_id,
                expected_head_sha=args.expected_head_sha,
                dry_run=args.dry_run,
                request_id=args.request_id,
            )
    except MaintenanceError as error:
        values = {
            "result": "failure",
            "mutation_count": "0",
            "retry_run_id": "",
            "report_issue_url": "",
            "request_id": getattr(args, "request_id", ""),
            "failure_code": error.code,
        }
        _out(values)
        print(json.dumps(values, sort_keys=True))
        return 1
    values = render_result(result)
    values["failure_code"] = ""
    _out(values)
    print(json.dumps(values, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
