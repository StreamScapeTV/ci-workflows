"""Public facade for bounded reusable Android validation."""
from __future__ import annotations

from pathlib import Path
from typing import Mapping

from .android_contract import (
    CONTRACT_PATH,
    bounded_path,
    file_sha256,
    git_blob_sha1,
    load_android_contract,
    request_from_environment,
    resolve_validation_plan,
    safe_relative,
    source_trust_from_environment,
)
from .android_execution import (
    cleanup_android_state,
    execute_android_plan,
    protected_hashes,
    verify_debug_outputs,
    verify_exact_source,
    verify_private_dependency,
    verify_toolchain,
    verify_wrapper,
)
from .android_types import (
    AndroidCommand,
    AndroidValidationError,
    AndroidValidationPlan,
    AndroidValidationRequest,
    AndroidValidationResult,
    AndroidWrapperContract,
)
from .foundation_types import FoundationError
from .policy import verify_repository_policy


def _verify_policy(source_root: Path, request: AndroidValidationRequest, contract_root: Path, phase: str) -> None:
    try:
        verify_repository_policy(
            source_root,
            repository=request.repository,
            phase=phase,
            artifact_manifest_json="[]",
            artifact_exception_id=request.artifact_exception_id,
            trust_mode=request.source_trust,
            contract_root=contract_root,
        )
    except FoundationError as error:
        instruction = getattr(error, "instruction", "")
        code = "artifact_policy_failed" if "artifact" in instruction else "dirty_tree"
        raise AndroidValidationError(code) from error


def validate(*, contract_root: Path, source_root: Path | None, state_root: Path | None,
             request: AndroidValidationRequest, phase: str,
             environment: Mapping[str, str]) -> AndroidValidationPlan | AndroidValidationResult:
    """Plan or execute one contract-bounded Android validation request."""

    if phase not in {"plan", "execute"}:
        raise AndroidValidationError("invalid_input")
    contract = load_android_contract(contract_root)
    plan = resolve_validation_plan(contract, request)
    if phase == "plan":
        return plan
    if source_root is None or state_root is None:
        raise AndroidValidationError("invalid_input")
    exact_source = source_root.resolve()
    registered_state = state_root.resolve()
    _verify_policy(exact_source, request, contract_root, "before")
    result = execute_android_plan(exact_source, registered_state, plan, contract, environment)
    _verify_policy(exact_source, request, contract_root, "after")
    return result


__all__ = (
    "CONTRACT_PATH",
    "AndroidCommand",
    "AndroidValidationError",
    "AndroidValidationPlan",
    "AndroidValidationRequest",
    "AndroidValidationResult",
    "AndroidWrapperContract",
    "bounded_path",
    "cleanup_android_state",
    "file_sha256",
    "git_blob_sha1",
    "load_android_contract",
    "protected_hashes",
    "request_from_environment",
    "resolve_validation_plan",
    "safe_relative",
    "source_trust_from_environment",
    "validate",
    "verify_debug_outputs",
    "verify_exact_source",
    "verify_private_dependency",
    "verify_toolchain",
    "verify_wrapper",
)
