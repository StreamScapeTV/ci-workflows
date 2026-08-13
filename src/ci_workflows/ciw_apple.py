"""Bounded ``ciw apple validate`` adapter and standalone compatibility CLI."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Mapping, Sequence

from . import apple as apple_validation
from .apple_contract import build_plan
from .apple_contract_fragments import load_apple_contract

try:
    from . import runners
    from .ciw_types import CIWContext, CIWResult
    from .workspace import resolve_state_root
except ImportError:  # pragma: no cover - standalone fixture use
    CIWContext = object  # type: ignore[assignment,misc]
    CIWResult = object  # type: ignore[assignment,misc]


def configure_apple_validate(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--phase",
        choices=("plan", "execute", "cleanup", "residue"),
        default="execute",
    )
    parser.add_argument("--source-root", default="source")


def _resolved_state_root(root: Path, environment: Mapping[str, str]) -> Path:
    runner_temp = Path(environment.get("RUNNER_TEMP", root / ".validation-state"))
    declared = environment.get(
        "CI_WORKFLOW_ROOT",
        str(runner_temp / "ciw-apple"),
    )
    state_id = environment.get("CI_WORKFLOW_STATE_ID", "apple-validation")
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
        raise apple_validation.AppleValidationError("invalid_input")
    return temporary


def _standalone_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument(
        "command",
        choices=("plan", "execute", "cleanup", "residue"),
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
    plan: apple_validation.AppleValidationPlan,
    request: apple_validation.AppleValidationRequest,
) -> dict[str, str]:
    try:
        resolved = runners.resolve_runner_profile(
            runners.load_runner_contract(root),
            workflow_api="validation.apple",
            source_trust=request.source_trust,
            requested_profile=plan.runner_profile.value,
        )
    except runners.RunnerContractError as error:
        raise apple_validation.AppleValidationError("runner_rejected") from error
    outputs = plan.planning_outputs()
    outputs["runs_on_json"] = resolved.as_dict()["runs_on_json"]
    return outputs


def _source_path(
    root: Path,
    source_root: str,
    environment: Mapping[str, str],
) -> Path:
    workspace = Path(environment.get("GITHUB_WORKSPACE", root)).resolve()
    relative = apple_validation.safe_relative(source_root)
    return apple_validation.bounded_path(workspace, relative)


def _run_plan(
    *,
    plan: apple_validation.AppleValidationPlan,
    source: Path | None,
    state: Path | None,
    environment: Mapping[str, str],
) -> apple_validation.AppleValidationPlan | apple_validation.AppleValidationResult:
    if source is None or state is None:
        return plan
    return apple_validation.execute_apple_plan(
        plan=plan,
        source_root=source,
        state_root=state,
        environment=environment,
    )


def standalone_main(argv: Sequence[str] | None = None) -> int:
    args = _standalone_parser().parse_args(argv)
    root = args.root.resolve()
    output = args.output or os.environ.get("GITHUB_OUTPUT")
    try:
        contract = load_apple_contract(root)
        request = apple_validation.request_from_environment(os.environ, contract)
        plan = build_plan(contract, request)
        source = _source_path(root, args.source_root, os.environ)
        state = None if args.command == "plan" else _resolved_state_root(root, os.environ)
        if args.command == "cleanup":
            assert state is not None
            apple_validation.cleanup_apple_state(source, state, plan)
            values = {"cleanup_result": "success", "failure_code": ""}
        elif args.command == "residue":
            assert state is not None
            apple_validation.assert_zero_apple_residue(source, state, plan)
            values = {"cleanup_result": "success", "failure_code": ""}
        else:
            result = _run_plan(
                plan=plan,
                source=None if args.command == "plan" else source,
                state=state,
                environment=os.environ,
            )
            values = (
                _planning_outputs(root, result, request)
                if isinstance(result, apple_validation.AppleValidationPlan)
                else result.output_values()
            )
        _write_outputs(values, output)
        return 0
    except apple_validation.AppleValidationError as error:
        _write_outputs(
            {
                "result": "failure",
                "cleanup_result": (
                    "failure" if error.cleanup_failed else "not-run"
                ),
                "failure_code": error.code,
            },
            output,
        )
        return 1


def execute_apple_validate(
    args: argparse.Namespace,
    context: "CIWContext",
) -> "CIWResult":
    contract = load_apple_contract(context.root)
    request = apple_validation.request_from_environment(
        context.environment,
        contract,
    )
    plan = build_plan(contract, request)
    source = _source_path(context.root, args.source_root, context.environment)
    state = (
        None
        if args.phase == "plan"
        else _resolved_state_root(context.root, context.environment)
    )
    if args.phase == "cleanup":
        assert state is not None
        apple_validation.cleanup_apple_state(source, state, plan)
        return CIWResult(
            "apple",
            "validate",
            outputs={"cleanup_result": "success", "failure_code": ""},
        )
    if args.phase == "residue":
        assert state is not None
        apple_validation.assert_zero_apple_residue(source, state, plan)
        return CIWResult(
            "apple",
            "validate",
            outputs={"cleanup_result": "success", "failure_code": ""},
        )
    result = _run_plan(
        plan=plan,
        source=None if args.phase == "plan" else source,
        state=state,
        environment=context.environment,
    )
    if isinstance(result, apple_validation.AppleValidationPlan):
        return CIWResult(
            "apple",
            "validate",
            outputs=_planning_outputs(context.root, result, request),
        )
    return CIWResult("apple", "validate", outputs=result.output_values())


def main(argv: Sequence[str] | None = None) -> int:
    return standalone_main(argv)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
