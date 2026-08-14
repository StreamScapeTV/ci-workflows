#!/usr/bin/env python3
"""CLI adapter for trusted Flux reconciliation."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from ci_workflows.flux_reconcile import plan_summary, reconcile, resolve_request
from ci_workflows.flux_reconcile_fs import remove_state
from ci_workflows.maintenance_contract import MaintenanceError, load_contract


def _bool(value: str) -> bool:
    normalized = value.casefold()
    if normalized == "true":
        return True
    if normalized == "false":
        return False
    raise argparse.ArgumentTypeError("expected true or false")


def _write(values: dict[str, str]) -> None:
    path = os.environ.get("GITHUB_OUTPUT", "")
    if path:
        with Path(path).open("a", encoding="utf-8") as output:
            for name, value in values.items():
                if "\n" in value or "\r" in value:
                    raise MaintenanceError("output_invalid")
                output.write(f"{name}={value}\n")
    print(json.dumps(values, sort_keys=True))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--source-repository", required=True)
    parser.add_argument("--admitted-sha", required=True)
    parser.add_argument("--target-id", required=True)
    parser.add_argument("--product-id", required=True)
    parser.add_argument("--operation", required=True)
    parser.add_argument("--policy-path", required=True)
    parser.add_argument("--allowlist-path", required=True)
    parser.add_argument("--request-id", required=True)
    parser.add_argument("--dry-run", type=_bool, required=True)
    args = parser.parse_args(argv)

    contract = load_contract(ROOT)
    state: Path | None = None
    failure: MaintenanceError | None = None
    values: dict[str, str] | None = None

    try:
        # Validate request identity before any state operation. Filesystem state
        # itself is keyed only by GitHub-owned run identity, never caller input.
        contract.validate_request_id(args.request_id)
        state = Path(
            os.environ.get("RUNNER_TEMP", str(ROOT / ".maintenance-state"))
        ) / (
            f"flux-reconcile-{os.environ.get('GITHUB_RUN_ID', 'local')}-"
            f"{os.environ.get('GITHUB_RUN_ATTEMPT', '1')}"
        )
        remove_state(state, fail_on_unsafe=True)
        plan = resolve_request(
            contract,
            source_root=args.source_root,
            source_repository=args.source_repository,
            admitted_sha=args.admitted_sha,
            target_id=args.target_id,
            product_id=args.product_id,
            operation=args.operation,
            policy_path=args.policy_path,
            allowlist_path=args.allowlist_path,
            request_id=args.request_id,
            state_root=state,
        )
        if not args.dry_run:
            reconcile(
                contract,
                plan,
                source_root=args.source_root,
                state_root=state,
                flux_kubeconfig=os.environ.get("FLUX_KUBECONFIG", ""),
                flux_sops_age_key=os.environ.get("FLUX_SOPS_AGE_KEY", ""),
            )
        values = plan_summary(plan, dry_run=args.dry_run)
        values["reconciliation_state"] = (
            "dry-run" if args.dry_run else "applied-and-verified"
        )
        values["failure_code"] = ""
    except MaintenanceError as error:
        failure = error
    finally:
        if state is not None:
            try:
                remove_state(state, fail_on_unsafe=False)
            except MaintenanceError as cleanup_error:
                if failure is None:
                    failure = cleanup_error

    if failure is not None:
        _write(
            {
                "result": "failure",
                "reconciliation_state": "rejected",
                "request_id": args.request_id,
                "failure_code": failure.code,
            }
        )
        return 1
    assert values is not None
    _write(values)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
