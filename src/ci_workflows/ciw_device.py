"""Bounded ``ciw device validate`` adapter and standalone compatibility CLI."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path, PurePosixPath
from typing import Mapping, Sequence

from . import device as device_validation

try:
    from . import runners
    from .ciw_types import CIWContext, CIWResult
    from .workspace import resolve_state_root
except ImportError:  # pragma: no cover - isolated focused tests
    CIWContext = object  # type: ignore[assignment,misc]
    CIWResult = object  # type: ignore[assignment,misc]

_PHASES = ("plan", "synthetic", "discover", "execute", "restore", "cleanup", "residue")


def configure_device_validate(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--phase", choices=_PHASES, default="plan")
    parser.add_argument("--source-root", default="source")
    parser.add_argument("--inventory-fixture", default="")


def _standalone_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("command", choices=(*_PHASES, "cleanup-checkout"))
    parser.add_argument("--source-root", default="source")
    parser.add_argument("--inventory-fixture", default="")
    parser.add_argument("--output")
    return parser


def _write_outputs(values: Mapping[str, str], target: str | None) -> None:
    if target:
        with Path(target).open("a", encoding="utf-8") as handle:
            for key, value in sorted(values.items()):
                handle.write(f"{key}={value}\n")
    print(json.dumps(dict(values), sort_keys=True, separators=(",", ":")))


def _resolved_state_root(root: Path, environment: Mapping[str, str]) -> Path:
    runner_temp = Path(environment.get("RUNNER_TEMP", root / ".validation-state"))
    declared = environment.get("CI_WORKFLOW_ROOT", str(runner_temp / "ciw-device"))
    state_id = environment.get("CI_WORKFLOW_STATE_ID", "device-validation")
    try:
        resolver = resolve_state_root  # type: ignore[name-defined]
    except NameError:
        path = Path(declared).resolve()
        path.mkdir(parents=True, exist_ok=True)
        temporary = path / "tmp"
        temporary.mkdir(parents=True, exist_ok=True)
        return temporary
    path = resolver(
        runner_temp=runner_temp,
        state_id=state_id,
        declared_root=declared,
        contract_root=root,
    )
    temporary = path / "tmp"
    if not temporary.is_dir() or temporary.is_symlink():
        raise device_validation.DeviceValidationError("invalid_input")
    return temporary


def _bounded_relative_file(root: Path, value: str) -> Path:
    path = PurePosixPath(value)
    if (
        not value or path.is_absolute() or "\\" in value or ".." in path.parts
        or any(part in {"", "."} for part in path.parts)
    ):
        raise device_validation.DeviceValidationError("invalid_input")
    target = root.joinpath(*path.parts)
    current = root
    for part in path.parts:
        current /= part
        if current.is_symlink():
            raise device_validation.DeviceValidationError("invalid_input")
    if not target.is_file() or target.is_symlink():
        raise device_validation.DeviceValidationError("invalid_input")
    return target


def _source_path(root: Path, relative: str, environment: Mapping[str, str]) -> Path:
    workspace = Path(environment.get("GITHUB_WORKSPACE", root)).resolve()
    if relative != "source":
        raise device_validation.DeviceValidationError("invalid_input")
    source = workspace / "source"
    if source.is_symlink():
        raise device_validation.DeviceValidationError("invalid_input")
    return source


def _approved_base_runs_on_json(contract: Mapping[str, object], plan: device_validation.DevicePlan) -> str:
    profile_id = plan.request.host_capacity
    profile = runners.profile_index(contract).get(profile_id)  # type: ignore[name-defined]
    if not isinstance(profile, Mapping) or profile.get("kind") != "runner":
        raise ValueError("invalid device host capacity")
    runners.validate_source(profile, plan.request.source_trust)  # type: ignore[name-defined]
    raw_selector = profile.get("default_internal_selector")
    if not isinstance(raw_selector, list) or not raw_selector or not all(isinstance(label, str) and label for label in raw_selector):
        raise ValueError("invalid device host selector")
    resolved_profile = runners.validate_direct_selector(contract, raw_selector)  # type: ignore[name-defined]
    if resolved_profile != profile_id:
        raise ValueError("device host selector/profile mismatch")
    return json.dumps(raw_selector, separators=(",", ":"))


def _runs_on_json(root: Path, plan: device_validation.DevicePlan) -> str:
    try:
        contract = runners.load_runner_contract(root)  # type: ignore[name-defined]
        value = _approved_base_runs_on_json(contract, plan)
    except (NameError, AttributeError, OSError, ValueError) as error:
        if os.environ.get("CIW_DEVICE_FOCUSED_TEST") == "true":
            value = json.dumps([plan.request.host_capacity], separators=(",", ":"))
        else:
            raise device_validation.DeviceValidationError("device_profile_rejected") from error
    if not isinstance(value, str) or not value:
        raise device_validation.DeviceValidationError("device_profile_rejected")
    return value


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON object key")
        result[key] = value
    return result


def _authorization_receipt(environment: Mapping[str, str]) -> str:
    """Canonicalize the transported JSON secret before exact authorization checks.

    Reusable-workflow secret transport is allowed to change insignificant JSON
    whitespace. Authority remains in the exact typed fields validated by the
    device runtime; duplicate keys are rejected before canonicalization.
    """

    raw = environment.get("CIW_DEVICE_AUTHORIZATION_RECEIPT", "")
    if not raw:
        return ""
    try:
        payload = json.loads(raw, object_pairs_hook=_unique_json_object)
    except (json.JSONDecodeError, ValueError) as error:
        raise device_validation.DeviceValidationError("authorization_rejected") from error
    if not isinstance(payload, Mapping):
        raise device_validation.DeviceValidationError("authorization_rejected")
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _execute_command(
    *, root: Path, command: str, source_root: str, inventory_fixture: str,
    environment: Mapping[str, str],
) -> dict[str, str]:
    workspace = Path(environment.get("GITHUB_WORKSPACE", root)).resolve()
    if command == "cleanup-checkout":
        device_validation.cleanup_checkout_path(workspace, source_root)
        return {"result": "success", "cleanup_result": "success", "failure_code": ""}

    contract = device_validation.load_device_contract(root)
    typed_packet = None
    if command in {"discover", "execute", "restore", "cleanup", "residue"}:
        typed_packet = device_validation.validate_typed_plan(
            environment.get("INPUT_VALIDATED_PLAN", ""),
            environment.get("INPUT_VALIDATED_PLAN_SHA256", ""),
            contract=contract,
            environment=environment,
        )

    if command in {"discover", "execute", "restore"}:
        assert typed_packet is not None
        source = _source_path(root, source_root, environment)
        if command in {"discover", "execute"}:
            device_validation.validate_exact_checkout(source, str(typed_packet["admitted_sha"]))
        request = device_validation.request_from_environment(environment, contract)
        plan = device_validation.build_plan(contract, request)
        device_validation.validate_authorization_receipt(_authorization_receipt(environment), plan=plan)
        state = _resolved_state_root(root, environment)
        if command == "discover":
            selected = device_validation.discover_live_device(
                plan, source_root=source, state_root=state, environment=environment
            )
            return {
                "result": "discovered", "request_id": plan.request.request_id,
                "selected_device_hash": selected.identity_hash,
                "cleanup_result": "not-run", "failure_code": "",
            }
        selected_hash = environment.get("INPUT_SELECTED_DEVICE_HASH", "").strip()
        lock_receipt = environment.get("INPUT_RESOURCE_LOCK_RECEIPT", "").strip()
        if command == "restore":
            device_validation.cleanup_live_device(
                contract_root=root, plan=plan, source_root=source, state_root=state,
                selected_identity_hash=selected_hash,
                authorization_receipt=_authorization_receipt(environment),
                resource_lock_receipt=lock_receipt, environment=environment,
            )
            return {
                "result": "success", "request_id": plan.request.request_id,
                "selected_device_hash": selected_hash,
                "cleanup_result": "success", "failure_code": "",
            }
        result = device_validation.execute_live_device(
            contract_root=root, plan=plan, source_root=source, state_root=state,
            selected_identity_hash=selected_hash,
            authorization_receipt=_authorization_receipt(environment),
            resource_lock_receipt=lock_receipt, environment=environment,
        )
        return result.output_values()

    if command in {"cleanup", "residue"}:
        assert typed_packet is not None
        state = _resolved_state_root(root, environment)
        if command == "cleanup":
            device_validation.cleanup_device_state(state)
        else:
            device_validation.assert_zero_device_residue(state)
        return {
            "result": "success", "request_id": str(typed_packet["request_id"]),
            "cleanup_result": "success", "failure_code": "",
        }

    request = device_validation.request_from_environment(environment, contract)
    plan = device_validation.build_plan(contract, request)
    if command == "plan":
        if plan.execution_authorized:
            device_validation.validate_authorization_receipt(_authorization_receipt(environment), plan=plan)
        return plan.planning_outputs(runs_on_json=_runs_on_json(root, plan))
    if command == "synthetic":
        source = _source_path(root, source_root, environment)
        device_validation.validate_exact_checkout(source, request.admitted_sha)
        inventory = _bounded_relative_file(root, inventory_fixture).read_text(encoding="utf-8")
        result = device_validation.synthetic_validate(
            contract_root=root, environment=environment, inventory_text=inventory
        )
        return {
            **plan.planning_outputs(runs_on_json=_runs_on_json(root, plan)),
            **result.output_values(), "result": result.result,
            "test_summary": result.output_values()["test_summary"],
            "cleanup_result": result.cleanup_result,
        }
    raise device_validation.DeviceValidationError("invalid_input")


def standalone_main(argv: Sequence[str] | None = None) -> int:
    args = _standalone_parser().parse_args(argv)
    root = args.root.resolve()
    try:
        values = _execute_command(
            root=root, command=args.command, source_root=args.source_root,
            inventory_fixture=args.inventory_fixture, environment=os.environ,
        )
        _write_outputs(values, args.output or os.environ.get("GITHUB_OUTPUT"))
        return 0 if values.get("result") != "failure" else 1
    except device_validation.DeviceValidationError as error:
        _write_outputs(
            {
                "result": "failure", "request_id": os.environ.get("INPUT_REQUEST_ID", ""),
                "device_evidence_id": "", "artifact_exception_used": "false",
                "selected_device_hash": "", "cleanup_result": "failed", "failure_code": error.code,
            },
            args.output or os.environ.get("GITHUB_OUTPUT"),
        )
        return 1


def execute_device_validate(args: argparse.Namespace, context: "CIWContext") -> "CIWResult":
    values = _execute_command(
        root=context.root, command=args.phase, source_root=args.source_root,
        inventory_fixture=args.inventory_fixture, environment=context.environment,
    )
    return CIWResult("device", "validate", outputs=values)


def main(argv: Sequence[str] | None = None) -> int:
    return standalone_main(argv)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
