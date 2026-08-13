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
    verify_residue_absent,
)
from ci_workflows.flux_assets_guards import (  # noqa: E402
    compose_guarded_release,
    validate_operation_context,
)
from ci_workflows.flux_assets_source import (  # noqa: E402
    validate_dependency_product_inventory,
    validate_oci_build_dependency_evidence,
    validate_runtime_repository,
    validate_source_contract_strict,
)

DEFAULT_CONTRACT = ROOT / "contracts/flux-infrastructure-products.json"
PRODUCTS_INVENTORY = ROOT / "contracts/products.json"
OCI_PRODUCTS_INVENTORY = ROOT / "contracts/oci-products.json"


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
    parser.add_argument("--source-root", default="")
    parser.add_argument("--dependency-evidence-json", default="{}")
    parser.add_argument("--state-root", type=Path)
    parser.add_argument("--github-output", type=Path)
    return parser


def _github_context() -> dict[str, str]:
    """Read immutable caller event/ref identities from the GitHub runtime."""

    event_name = os.environ.get("GITHUB_EVENT_NAME", "")
    ref_type = os.environ.get("GITHUB_REF_TYPE", "")
    ref_name = os.environ.get("GITHUB_REF_NAME", "")
    default_branch = ""
    event_path = os.environ.get("GITHUB_EVENT_PATH", "")
    if event_path:
        try:
            payload = json.loads(Path(event_path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise FluxAssetError(
                "invalid_github_context", "GitHub event payload is unavailable"
            ) from error
        if not isinstance(payload, Mapping):
            raise FluxAssetError(
                "invalid_github_context", "GitHub event payload must be an object"
            )
        repository = payload.get("repository")
        if isinstance(repository, Mapping):
            value = repository.get("default_branch")
            if isinstance(value, str):
                default_branch = value
    return {
        "event_name": event_name,
        "ref_type": ref_type,
        "ref_name": ref_name,
        "default_branch": default_branch,
    }


def _request(args: argparse.Namespace, context: Mapping[str, str]) -> dict[str, str]:
    return {
        "admitted_sha": args.admitted_sha,
        "product_id": args.product_id,
        "release_version": args.release_version,
        "operation": args.operation,
        "policy_path": args.policy_path,
        "request_id": args.request_id,
        "source_ref_type": context["ref_type"],
        "source_ref_name": context["ref_name"],
    }


def _validate_context(args: argparse.Namespace, context: Mapping[str, str]) -> None:
    validate_operation_context(
        operation=args.operation,
        event_name=context["event_name"],
        ref_type=context["ref_type"],
        ref_name=context["ref_name"],
        default_branch=context["default_branch"],
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


def _load_current_contract(path: Path) -> dict[str, Any]:
    validate_runtime_repository(os.environ.get("GITHUB_REPOSITORY", ""))
    contract = load_contract(path)
    validate_dependency_product_inventory(
        contract, products_path=PRODUCTS_INVENTORY
    )
    return contract


def _plan(args: argparse.Namespace) -> Mapping[str, Any]:
    context = _github_context()
    contract = _load_current_contract(args.contract)
    plan = build_release_plan(contract, **_request(args, context))
    _validate_context(args, context)
    return {
        "runs_on_json": canonical_json(list(plan.runs_on)),
        "workspace_profile": plan.workspace_profile,
        "plan_json": canonical_json(plan.as_dict()),
        "request_id": plan.request_id,
    }


def _release(args: argparse.Namespace) -> Mapping[str, Any]:
    context = _github_context()
    contract = _load_current_contract(args.contract)
    request = _request(args, context)
    build_release_plan(contract, **request)
    _validate_context(args, context)
    source_root = Path(args.source_root) if args.source_root else None
    if source_root is not None and args.operation != "verify-only":
        validate_source_contract_strict(
            contract,
            product_id=args.product_id,
            source_root=source_root,
        )
    dependency_outputs = _dependency_evidence(args.dependency_evidence_json)
    validate_oci_build_dependency_evidence(
        dependency_outputs, oci_products_path=OCI_PRODUCTS_INVENTORY
    )
    return compose_guarded_release(
        contract,
        request=request,
        dependency_outputs=dependency_outputs,
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
