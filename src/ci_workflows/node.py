"""Public facade and bounded orchestration for reusable Node validation."""
from __future__ import annotations

from pathlib import Path
from typing import Mapping

from .foundation_types import FoundationError
from .node_contract import (
    CONTRACT_PATH,
    bounded_path,
    file_sha256,
    load_lockfile,
    load_node_contract,
    load_package_manifest,
    request_from_environment,
    resolve_exact_node_version,
    resolve_validation_plan,
    safe_relative,
    source_trust_from_environment,
    verify_manifest_engines,
    version_satisfies,
)
from .node_execution import (
    cleanup_generated,
    execute_node_plan,
    verify_exact_source,
    verify_static_output,
)
from .node_types import (
    NodeCommand,
    NodeValidationError,
    NodeValidationPlan,
    NodeValidationRequest,
    NodeValidationResult,
)
from .policy import verify_repository_policy


def _verify_policy(
    source_root: Path,
    request: NodeValidationRequest,
    contract_root: Path,
    phase: str,
) -> None:
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
        code = (
            "artifact_policy_failed"
            if "artifact" in instruction
            else "dirty_tree"
        )
        raise NodeValidationError(code) from error


def validate(
    *,
    contract_root: Path,
    source_root: Path | None,
    state_root: Path | None,
    request: NodeValidationRequest,
    phase: str,
    environment: Mapping[str, str],
) -> NodeValidationPlan | NodeValidationResult:
    """Plan or execute one contract-bounded Node validation request."""

    if phase not in {"plan", "execute"}:
        raise NodeValidationError("invalid_input")
    contract = load_node_contract(contract_root)
    plan = resolve_validation_plan(contract, request)
    if phase == "plan":
        return plan
    if source_root is None or state_root is None:
        raise NodeValidationError("invalid_input")
    exact_source = source_root.resolve()
    registered_state = state_root.resolve()
    _verify_policy(exact_source, request, contract_root, "before")
    result = execute_node_plan(
        exact_source,
        registered_state,
        plan,
        contract,
        environment,
    )
    _verify_policy(exact_source, request, contract_root, "after")
    return result


__all__ = (
    "CONTRACT_PATH",
    "NodeCommand",
    "NodeValidationError",
    "NodeValidationPlan",
    "NodeValidationRequest",
    "NodeValidationResult",
    "bounded_path",
    "cleanup_generated",
    "file_sha256",
    "load_lockfile",
    "load_node_contract",
    "load_package_manifest",
    "request_from_environment",
    "resolve_exact_node_version",
    "resolve_validation_plan",
    "safe_relative",
    "source_trust_from_environment",
    "validate",
    "verify_exact_source",
    "verify_manifest_engines",
    "verify_static_output",
    "version_satisfies",
)
