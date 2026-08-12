#!/usr/bin/env python3
"""Thin CLI for the issue-33 Flux infrastructure asset runtime."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ci_workflows.flux_assets import (  # noqa: E402
    FluxAssetError,
    build_release_plan,
    canonical_json,
    cleanup_state,
    load_contract,
    validate_source_contract,
    verify_residue_absent,
)
from ci_workflows.flux_assets_guards import (  # noqa: E402
    compose_guarded_release,
    validate_operation_context,
)

DEFAULT_CONTRACT = ROOT / "contracts/flux-infrastructure-products.json"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("plan", "release", "cleanup", "residue"))
    parser.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT)
    parser.add_argument("--admitted-sha", default="")
    parser.add_argument("--product-id", default="")
    parser.add_argument("--release-version", default="")
    parser.add_argument("--operation", default="")
    parser.add_argument("--policy-path", default="")
    parser.add_argument("--request-id", default="")
    parser.add_argument("--source-event-name", default="")
    parser.add_argument("--source-ref-type", default="")
    parser.add_argument("--source-ref-name", default="")
    parser.add_argument("--source-default-branch", default="")
    parser.add_argument("--source-root", default="")
    parser.add_argument("--dependency-evidence-json", default="{}")
    parser.add_argument("--state-root", type=Path)
    parser.add_argument("--github-output", type=Path)
    return parser


def _request(args: argparse.Namespace) -> dict[str, str]:
    return {
        "admitted_sha": args.admitted_sha,
        "product_id": args.product_id,
        "release_version": args.release_version,
        "operation": args.operation,
        "policy_path": args.policy_path,
        "request_id": args.request_id,
        "source_ref_type": args.source_ref_type,
        "source_ref_name": args.source_ref_name,
    }


def _validate_context(args: argparse.Namespace) -> None:
    validate_operation_context(
        operation=args.operation,
        event_name=args.source_event_name,
        ref_type=args.source_ref_type,
        ref_name=args.source_ref_name,
        default_branch=args.source_default_branch,
        release_version=args.release_version,
    )


def _dependency_evidence(raw: str) -> Mapping[str, Any]:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as error:
        raise FluxAssetError(
            "invalid_dependency_evidence", "dependency evidence is not valid JSON"
        ) from error
    if not isinstance(value, Mapping):
        raise FluxAssetError(
            "invalid_dependency_evidence", "dependency evidence must be an object"
        )
    return value


def _state_root(path: Path | None) -> Path:
    if path is None:
        raise FluxAssetError("invalid_state_root", "state root is required")
    resolved_parent = path.parent.resolve()
    runner_temp = os.environ.get("RUNNER_TEMP")
    if runner_temp:
        expected_parent = Path(runner_temp).resolve()
        if resolved_parent != expected_parent:
            raise FluxAssetError(
                "invalid_state_root", "state root must be directly below RUNNER_TEMP"
            )
    if not path.name.startswith("flux-assets-"):
        raise FluxAssetError(
            "invalid_state_root", "state root must use the issue-owned prefix"
        )
    return path


def _emit(path: Path | None, payload: Mapping[str, Any]) -> None:
    print(json.dumps(payload, sort_keys=True, indent=2))
    if path is None:
        return
    allowed = (
        "runs_on_json",
        "workspace_profile",
        "plan_json",
        "result",
        "immutable_references_json",
        "release_manifest_sha256",
        "request_id",
    )
    with path.open("a", encoding="utf-8") as handle:
        for key in allowed:
            if key not in payload:
                continue
            value = payload[key]
            if not isinstance(value, str):
                value = canonical_json(value)
            if "\n" in value or "\r" in value:
                raise FluxAssetError(
                    "invalid_output", f"GitHub output {key} contains a newline"
                )
            handle.write(f"{key}={value}\n")


def _plan(args: argparse.Namespace) -> Mapping[str, Any]:
    contract = load_contract(args.contract)
    plan = build_release_plan(contract, **_request(args))
    _validate_context(args)
    return {
        "runs_on_json": canonical_json(list(plan.runs_on)),
        "workspace_profile": plan.workspace_profile,
        "plan_json": canonical_json(plan.as_dict()),
        "request_id": plan.request_id,
    }


def _release(args: argparse.Namespace) -> Mapping[str, Any]:
    contract = load_contract(args.contract)
    build_release_plan(contract, **_request(args))
    _validate_context(args)
    source_root = Path(args.source_root) if args.source_root else None
    if source_root is not None and args.operation != "verify-only":
        validate_source_contract(
            contract,
            product_id=args.product_id,
            source_root=source_root,
        )
    return compose_guarded_release(
        contract,
        request=_request(args),
        dependency_outputs=_dependency_evidence(args.dependency_evidence_json),
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.phase == "plan":
            payload = _plan(args)
        elif args.phase == "release":
            payload = _release(args)
        elif args.phase == "cleanup":
            cleanup_state(_state_root(args.state_root))
            payload = {"result": "cleanup-success"}
        else:
            verify_residue_absent(_state_root(args.state_root))
            payload = {"result": "residue-absent"}
        _emit(args.github_output, payload)
        return 0
    except FluxAssetError as error:
        print(f"{error.code}: {error.message}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
