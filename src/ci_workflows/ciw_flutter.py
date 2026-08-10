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

PHASES = (
    "plan",
    "persistent-cache-snapshot",
    "pub-cache-bind",
    "verify-toolchain",
    "execute",
    "persistent-cache-verify",
    "cleanup",
    "residue",
)


def configure_flutter_validate(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--phase", choices=PHASES, default="execute")
    parser.add_argument("--source-root", default="source")


def _resolved_state_root(root: Path, environment: Mapping[str, str]) -> Path:
    runner_temp = Path(environment.get("RUNNER_TEMP", root / ".validation-state"))
    declared = environment.get("CI_WORKFLOW_ROOT", str(runner_temp / "ciw-flutter"))
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
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("command", choices=(*PHASES, "generate"))
    parser.add_argument("--source-root", default="source")
    parser.add_argument("--output")
    parser.add_argument("--check", action="store_true")
    return parser


def _write_outputs(values: Mapping[str, str], target: str | None) -> None:
    if target:
        with Path(target).open("a", encoding="utf-8") as handle:
            for key, value in sorted(values.items()):
                if "\n" in key or "\n" in value:
                    raise flutter_validation.FlutterValidationError("invalid_input")
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
    except NameError:
        outputs = plan.planning_outputs()
        outputs["runs_on_json"] = json.dumps([plan.runner_profile.value])
        return outputs
    except runners.RunnerContractError as error:
        raise flutter_validation.FlutterValidationError("platform_runner_mismatch") from error
    outputs = plan.planning_outputs()
    outputs["runs_on_json"] = resolved.as_dict()["runs_on_json"]
    return outputs


def _source_path(root: Path, source_root: str, environment: Mapping[str, str]) -> Path:
    workspace = Path(environment.get("GITHUB_WORKSPACE", root)).resolve()
    return flutter_validation.bounded_path(
        workspace,
        flutter_validation.safe_relative(source_root),
    )


def _request_plan(
    root: Path,
    source: Path,
    environment: Mapping[str, str],
    *,
    source_required: bool,
) -> tuple[flutter_validation.FlutterRequest, flutter_validation.FlutterPlan]:
    contract = flutter_validation.load_flutter_contract(root)
    request = flutter_validation.request_from_environment(environment, contract)
    plan = flutter_validation.validate(
        contract_root=root,
        source_root=source if source_required or source.exists() else None,
        state_root=None,
        request=request,
        phase="plan",
        environment=environment,
    )
    assert isinstance(plan, flutter_validation.FlutterPlan)
    return request, plan


def _phase_outputs(
    *,
    root: Path,
    command: str,
    source: Path,
    state: Path | None,
    environment: Mapping[str, str],
    check: bool,
) -> dict[str, str]:
    if command == "generate":
        changed = flutter_validation.generate_flutter_contract_files(root, check=check)
        return {
            "result": "success",
            "generated_file_count": str(3),
            "generated_changed_json": json.dumps(list(changed), separators=(",", ":")),
            "failure_code": "",
            "primary_failure_code": "",
            "cleanup_failure_code": "",
        }
    if command == "plan":
        request, plan = _request_plan(root, source, environment, source_required=False)
        return _planning_outputs(root, plan, request)
    assert state is not None
    if command == "persistent-cache-snapshot":
        return flutter_validation.snapshot_persistent_pub_cache(state, environment)
    if command == "pub-cache-bind":
        return flutter_validation.bind_pub_cache(state, environment)
    if command == "persistent-cache-verify":
        return flutter_validation.verify_persistent_pub_cache(state)
    if command == "cleanup":
        return flutter_validation.terminal_cleanup_flutter_state(
            source,
            state,
            primary_failure_code=environment.get("INPUT_PRIMARY_FAILURE_CODE", ""),
        )
    if command == "residue":
        flutter_validation.assert_zero_flutter_residue(source, state)
        return {
            "result": "success",
            "cleanup_result": "success",
            "failure_code": "",
            "primary_failure_code": "",
            "cleanup_failure_code": "",
        }
    request, plan = _request_plan(
        root,
        source,
        environment,
        source_required=command == "execute",
    )
    if command == "verify-toolchain":
        identity = flutter_validation.verify_toolchain_identity(
            plan=plan,
            source_root=source if source.exists() else root,
            state_root=state,
            environment=environment,
        )
        return {
            "result": "success",
            **identity,
            "gradle_version": plan.toolchain.gradle_version,
            "pub_cache_path": str(flutter_validation.expected_pub_cache_path(state)),
            "failure_code": "",
            "primary_failure_code": "",
            "cleanup_failure_code": "",
        }
    result = flutter_validation.validate(
        contract_root=root,
        source_root=source,
        state_root=state,
        request=request,
        phase="execute",
        environment=environment,
    )
    assert isinstance(result, flutter_validation.FlutterResult)
    return result.output_values()


def standalone_main(argv: Sequence[str] | None = None) -> int:
    args = _standalone_parser().parse_args(argv)
    root = args.root.resolve()
    source = _source_path(root, args.source_root, os.environ)
    state = None if args.command in {"plan", "generate"} else _resolved_state_root(root, os.environ)
    target = args.output or os.environ.get("GITHUB_OUTPUT")
    try:
        values = _phase_outputs(
            root=root,
            command=args.command,
            source=source,
            state=state,
            environment=os.environ,
            check=args.check,
        )
        _write_outputs(values, target)
        return 0
    except flutter_validation.FlutterValidationError as error:
        _write_outputs(error.output_values(), target)
        return 1


def execute_flutter_validate(
    args: argparse.Namespace,
    context: "CIWContext",
) -> "CIWResult":
    state = None if args.phase == "plan" else _resolved_state_root(context.root, context.environment)
    source = _source_path(context.root, args.source_root, context.environment)
    values = _phase_outputs(
        root=context.root,
        command=args.phase,
        source=source,
        state=state,
        environment=context.environment,
        check=False,
    )
    return CIWResult("flutter", "validate", outputs=values)


def main(argv: Sequence[str] | None = None) -> int:
    return standalone_main(argv)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
