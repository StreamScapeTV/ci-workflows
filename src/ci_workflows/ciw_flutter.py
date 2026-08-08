"""Bounded ``ciw flutter validate`` adapter and standalone compatibility CLI."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Mapping, Sequence

from . import flutter as flutter_validation

try:
    from . import runners
    from .ciw_types import CIWContext, CIWResult
    from .workspace import resolve_state_root
except ImportError:  # pragma: no cover - standalone fixture use
    CIWContext = object  # type: ignore[assignment,misc]
    CIWResult = object  # type: ignore[assignment,misc]


def configure_flutter_validate(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--phase",
        choices=("plan", "execute", "cleanup", "residue"),
        default="execute",
    )
    parser.add_argument("--source-root", default="source")


def _resolved_state_root(root: Path, environment: Mapping[str, str]) -> Path:
    runner_temp = Path(environment.get("RUNNER_TEMP", root / ".validation-state"))
    declared = environment.get(
        "CI_WORKFLOW_ROOT", str(runner_temp / "ciw-flutter")
    )
    state_id = environment.get("CI_WORKFLOW_STATE_ID", "flutter-validation")
    try:
        resolver = resolve_state_root  # type: ignore[name-defined]
    except NameError:
        path = Path(declared).resolve()
        path.mkdir(parents=True, exist_ok=True)
        (path / "tmp").mkdir(parents=True, exist_ok=True)
        return path / "tmp"
    path = resolver(
        runner_temp=runner_temp,
        state_id=state_id,
        declared_root=declared,
        contract_root=root,
    )
    temporary = path / "tmp"
    if not temporary.is_dir() or temporary.is_symlink():
        raise flutter_validation.FlutterValidationError("invalid_input")
    return temporary


def _standalone_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument(
        "command", choices=("plan", "execute", "cleanup", "residue")
    )
    parser.add_argument("--source-root", default="source")
    parser.add_argument("--output")
    return parser


def _write_outputs(values: Mapping[str, str], target: str | None) -> None:
    if target:
        with Path(target).open("a", encoding="utf-8") as handle:
            for key, value in sorted(values.items()):
                handle.write(f"{key}={value}\n")
    print(json.dumps(dict(values), sort_keys=True, separators=(",", ":")))


def _planning_outputs(
    root: Path,
    plan: flutter_validation.FlutterPlan,
    request: flutter_validation.FlutterRequest,
) -> dict[str, str]:
    try:
        resolved = runners.resolve_runner_profile(
            runners.load_runner_contract(root),
            workflow_api="validation.flutter",
            source_trust=request.source_trust,
            requested_profile=plan.runner_profile.value,
        )
    except runners.RunnerContractError as error:
        raise flutter_validation.FlutterValidationError(
            "platform_runner_mismatch"
        ) from error
    outputs = plan.planning_outputs()
    outputs["runs_on_json"] = resolved.as_dict()["runs_on_json"]
    return outputs


def standalone_main(argv: Sequence[str] | None = None) -> int:
    args = _standalone_parser().parse_args(argv)
    root = args.root.resolve()
    workspace = Path(os.environ.get("GITHUB_WORKSPACE", root)).resolve()
    source = flutter_validation.bounded_path(
        workspace, flutter_validation.safe_relative(args.source_root)
    )
    state = None if args.command == "plan" else _resolved_state_root(root, os.environ)
    try:
        contract = flutter_validation.load_flutter_contract(root)
        if args.command == "cleanup":
            assert state is not None
            flutter_validation.cleanup_flutter_state(source, state)
            values = {"cleanup_result": "success", "failure_code": ""}
        elif args.command == "residue":
            assert state is not None
            flutter_validation.assert_zero_flutter_residue(source, state)
            values = {"cleanup_result": "success", "failure_code": ""}
        else:
            request = flutter_validation.request_from_environment(
                os.environ, contract
            )
            result = flutter_validation.validate(
                contract_root=root,
                source_root=None if args.command == "plan" else source,
                state_root=state,
                request=request,
                phase=args.command,
                environment=os.environ,
            )
            values = (
                _planning_outputs(root, result, request)
                if isinstance(result, flutter_validation.FlutterPlan)
                else result.output_values()
            )
        _write_outputs(values, args.output or os.environ.get("GITHUB_OUTPUT"))
        return 0
    except flutter_validation.FlutterValidationError as error:
        _write_outputs(
            {"result": "failure", "failure_code": error.code},
            args.output or os.environ.get("GITHUB_OUTPUT"),
        )
        return 1


def execute_flutter_validate(
    args: argparse.Namespace, context: "CIWContext"
) -> "CIWResult":
    contract = flutter_validation.load_flutter_contract(context.root)
    request = flutter_validation.request_from_environment(
        context.environment, contract
    )
    state = (
        None
        if args.phase == "plan"
        else _resolved_state_root(context.root, context.environment)
    )
    workspace = Path(
        context.environment.get("GITHUB_WORKSPACE", ".")
    ).resolve()
    source = flutter_validation.bounded_path(
        workspace, flutter_validation.safe_relative(args.source_root)
    )
    if args.phase == "cleanup":
        assert state is not None
        flutter_validation.cleanup_flutter_state(source, state)
        return CIWResult(
            "flutter",
            "validate",
            outputs={"cleanup_result": "success", "failure_code": ""},
        )
    if args.phase == "residue":
        assert state is not None
        flutter_validation.assert_zero_flutter_residue(source, state)
        return CIWResult(
            "flutter",
            "validate",
            outputs={"cleanup_result": "success", "failure_code": ""},
        )
    result = flutter_validation.validate(
        contract_root=context.root,
        source_root=None if args.phase == "plan" else source,
        state_root=state,
        request=request,
        phase=args.phase,
        environment=context.environment,
    )
    if isinstance(result, flutter_validation.FlutterPlan):
        return CIWResult(
            "flutter",
            "validate",
            outputs=_planning_outputs(context.root, result, request),
        )
    return CIWResult(
        "flutter", "validate", outputs=result.output_values()
    )


def main(argv: Sequence[str] | None = None) -> int:
    return standalone_main(argv)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
