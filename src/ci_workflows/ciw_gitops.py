"""Bounded ``ciw gitops validate`` adapter and standalone compatibility CLI."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Mapping, Sequence

from . import gitops as gitops_validation

try:
    from . import runners
    from .ciw_types import CIWContext, CIWResult, write_command_file
    from .workspace import resolve_state_root
except ImportError:  # pragma: no cover - standalone fixture use
    CIWContext = object  # type: ignore[assignment,misc]
    CIWResult = object  # type: ignore[assignment,misc]


def configure_gitops_validate(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--phase",
        choices=("plan", "execute", "cleanup", "residue"),
        default="execute",
    )
    parser.add_argument("--source-root", default="source")


def _resolved_state_root(root: Path, environment: Mapping[str, str]) -> Path:
    try:
        runner_temp = Path(environment["RUNNER_TEMP"])
        state_id = environment["CI_WORKFLOW_STATE_ID"]
        declared_root = environment["CI_WORKFLOW_ROOT"]
    except KeyError as error:
        raise gitops_validation.GitOpsValidationError("invalid_input") from error
    try:
        resolver = resolve_state_root  # type: ignore[name-defined]
    except NameError:
        declared = Path(declared_root).resolve()
        declared.mkdir(parents=True, exist_ok=True)
        temporary = declared / "tmp"
        temporary.mkdir(parents=True, exist_ok=True)
    else:
        state = resolver(
            runner_temp=runner_temp,
            state_id=state_id,
            declared_root=declared_root,
            contract_root=root,
        )
        temporary = state / "tmp"
    if not temporary.is_dir() or temporary.is_symlink():
        raise gitops_validation.GitOpsValidationError("cleanup_failed")
    return temporary / "gitops-validation"


def _write_outputs(values: Mapping[str, str], target: str | None) -> None:
    if target:
        path = Path(target)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            for key, value in sorted(values.items()):
                if "\n" in value or "\r" in value:
                    raise gitops_validation.GitOpsValidationError("invalid_input")
                handle.write(f"{key}={value}\n")
    print(json.dumps(dict(values), sort_keys=True, separators=(",", ":")))


def _failure_outputs(environment: Mapping[str, str], code: str) -> dict[str, str]:
    return {
        "result": "failure",
        "source_sha": environment.get("INPUT_ADMITTED_SHA", ""),
        "consumer_contract": environment.get("INPUT_CONSUMER_CONTRACT", ""),
        "validation_profile": environment.get("INPUT_VALIDATION_PROFILE", ""),
        "runner_profile": "portable",
        "runs_on_json": "",
        "workspace_profile": "minimal",
        "timeout_minutes": "",
        "source_trust": "",
        "test_summary": "{}",
        "target_ids_json": "[]",
        "selected_targets_json": "[]",
        "validated_files": "0",
        "rendered_objects": "0",
        "render_digest": "",
        "policy_result": "failure",
        "tool_versions_json": "{}",
        "clean_tree": "false",
        "cleanup_result": "not-run",
        "artifact_exception_used": "false",
        "evidence_id": "",
        "failure_code": code,
    }


def _plan_outputs(
    root: Path,
    plan: gitops_validation.GitOpsPlan,
    request: gitops_validation.GitOpsRequest,
) -> dict[str, str]:
    outputs = plan.planning_outputs()
    try:
        resolved = runners.resolve_runner_profile(  # type: ignore[name-defined]
            runners.load_runner_contract(root),  # type: ignore[name-defined]
            workflow_api="validation.gitops",
            source_trust=request.source_trust,
            requested_profile=plan.runner_profile,
        )
    except NameError:
        outputs["runs_on_json"] = '["portable"]'
    except Exception as error:
        raise gitops_validation.GitOpsValidationError(
            "source_trust_rejected"
        ) from error
    else:
        outputs["runs_on_json"] = resolved.as_dict()["runs_on_json"]
    return outputs


def _run(
    *,
    root: Path,
    command: str,
    source_relative: str,
    environment: Mapping[str, str],
) -> dict[str, str]:
    contract = gitops_validation.load_gitops_contract(root)
    request = gitops_validation.request_from_environment(environment, contract)
    state_root = None if command == "plan" else _resolved_state_root(root, environment)
    if command == "cleanup":
        assert state_root is not None
        gitops_validation.cleanup_gitops_state(state_root)
        return {"cleanup_result": "success", "failure_code": ""}
    if command == "residue":
        assert state_root is not None
        gitops_validation.assert_zero_gitops_residue(state_root)
        return {"cleanup_result": "success", "failure_code": ""}
    workspace = Path(environment.get("GITHUB_WORKSPACE", ".")).resolve()
    source = gitops_validation.bounded_path(
        workspace,
        gitops_validation.safe_relative(source_relative),
    )
    result = gitops_validation.validate(
        contract_root=root,
        source_root=None if command == "plan" else source,
        state_root=state_root,
        request=request,
        phase=command,
        environment=environment,
    )
    if isinstance(result, gitops_validation.GitOpsPlan):
        return _plan_outputs(root, result, request)
    if isinstance(result, gitops_validation.GitOpsResult):
        return result.output_values()
    raise gitops_validation.GitOpsValidationError("invalid_input")


def standalone_main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument(
        "command",
        choices=("plan", "execute", "cleanup", "residue"),
    )
    parser.add_argument("--source-root", default="source")
    parser.add_argument("--output")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    target = args.output or os.environ.get("GITHUB_OUTPUT")
    try:
        values = _run(
            root=root,
            command=args.command,
            source_relative=args.source_root,
            environment=os.environ,
        )
        _write_outputs(values, target)
        return 0
    except gitops_validation.GitOpsValidationError as error:
        _write_outputs(_failure_outputs(os.environ, error.code), target)
        return 1


def execute_gitops_validate(
    args: argparse.Namespace,
    context: "CIWContext",
) -> "CIWResult":
    try:
        values = _run(
            root=context.root,
            command=args.phase,
            source_relative=args.source_root,
            environment=context.environment,
        )
        return CIWResult("gitops", "validate", outputs=values)
    except gitops_validation.GitOpsValidationError as error:
        path = context.environment.get("GITHUB_OUTPUT", "")
        if path:
            try:
                writer = write_command_file  # type: ignore[name-defined]
            except NameError:
                _write_outputs(_failure_outputs(context.environment, error.code), path)
            else:
                writer(Path(path), _failure_outputs(context.environment, error.code))
        raise


def main(argv: Sequence[str] | None = None) -> int:
    return standalone_main(argv)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
