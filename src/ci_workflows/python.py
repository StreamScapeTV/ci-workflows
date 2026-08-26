"""Public facade and bounded orchestration for reusable Python validation."""
from __future__ import annotations

from pathlib import Path
from typing import Mapping

from .foundation_types import FoundationError
from .policy import verify_repository_policy
from .python_contract import CONTRACT_PATH, bounded_path, load_python_contract, request_from_environment, resolve_python_version, resolve_validation_plan, runtime_reference, safe_relative, source_trust_from_environment, validate_dependency_lock, validate_script_entrypoint
from .python_docker_execution import execute_docker_plan
from .python_execution import cleanup_podman, container_script, execute_podman_plan, result_from_plan, run_command, verify_exact_source
from .python_host_execution import execute_host_plan, result_from_host_plan
from .python_types import PythonValidationError, PythonValidationPlan, PythonValidationRequest, PythonValidationResult

_container_script = container_script
_cleanup_podman = cleanup_podman
_run = run_command


def _verify_policy(source_root: Path, request: PythonValidationRequest, contract_root: Path, phase: str) -> None:
    try:
        verify_repository_policy(source_root, repository=request.repository, phase=phase, artifact_manifest_json="[]", artifact_exception_id=request.artifact_exception_id, trust_mode=request.source_trust, contract_root=contract_root)
    except FoundationError as error:
        raise PythonValidationError("policy_failed") from error


def validate(*, contract_root: Path, source_root: Path | None, state_root: Path | None, request: PythonValidationRequest, phase: str, environment: Mapping[str, str]) -> PythonValidationPlan | PythonValidationResult:
    if phase not in {"plan", "execute"}:
        raise PythonValidationError("invalid_input")
    contract = load_python_contract(contract_root)
    plan = resolve_validation_plan(contract, request)
    if phase == "plan":
        return plan
    if source_root is None or state_root is None:
        raise PythonValidationError("invalid_input")
    exact_source = source_root.resolve()
    registered_state = state_root.resolve()
    verify_exact_source(exact_source, plan.admitted_sha)
    validate_script_entrypoint(exact_source, plan)
    resolve_python_version(exact_source, plan)
    validate_dependency_lock(exact_source, plan)
    _verify_policy(exact_source, request, contract_root, "before")
    original_error: BaseException | None = None
    stage_count = 0
    host_python_version: str | None = None
    try:
        if plan.isolation == "copied-host-source":
            stage_count, host_python_version = execute_host_plan(exact_source, registered_state, plan, environment)
        elif plan.isolation in {"podman-vfs", "podman-vfs-postgres"}:
            if environment.get("INPUT_EXECUTION_BACKEND") == "github-hosted":
                stage_count = execute_docker_plan(exact_source, registered_state, plan, contract, environment)
            else:
                stage_count = execute_podman_plan(exact_source, registered_state, plan, contract, environment)
        else:
            raise PythonValidationError("isolation_unavailable")
    except BaseException as error:
        original_error = error
    verify_exact_source(exact_source, plan.admitted_sha)
    _verify_policy(exact_source, request, contract_root, "after")
    if original_error is not None:
        raise original_error
    if host_python_version is not None:
        return result_from_host_plan(plan, stage_count, host_python_version)
    return result_from_plan(plan, stage_count)


__all__ = ("CONTRACT_PATH", "PythonValidationError", "PythonValidationPlan", "PythonValidationRequest", "PythonValidationResult", "bounded_path", "load_python_contract", "request_from_environment", "resolve_python_version", "resolve_validation_plan", "runtime_reference", "safe_relative", "source_trust_from_environment", "validate", "validate_dependency_lock", "validate_script_entrypoint")
