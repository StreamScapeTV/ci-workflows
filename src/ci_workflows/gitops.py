"""Public facade for the bounded GitOps validation implementation."""
from __future__ import annotations

from pathlib import Path
from typing import Mapping

from .gitops_contract import (
    bounded_path,
    build_plan,
    load_gitops_contract,
    request_from_environment,
    safe_relative,
    source_trust_from_environment,
)
from .gitops_execution import (
    assert_zero_gitops_residue,
    cleanup_gitops_state,
    execute_gitops_plan,
)
from .gitops_types import (
    GitOpsPlan,
    GitOpsRequest,
    GitOpsResult,
    GitOpsValidationError,
)


def validate(
    *,
    contract_root: Path,
    source_root: Path | None,
    state_root: Path | None,
    request: GitOpsRequest,
    phase: str,
    environment: Mapping[str, str],
) -> GitOpsPlan | GitOpsResult | None:
    """Plan, execute, clean, or inspect one checked-in validation request."""

    del environment
    contract = load_gitops_contract(contract_root)
    if phase == "plan":
        return build_plan(contract, request, None)
    if state_root is None:
        raise GitOpsValidationError("invalid_input")
    if phase == "cleanup":
        cleanup_gitops_state(state_root)
        return None
    if phase == "residue":
        assert_zero_gitops_residue(state_root)
        return None
    if phase != "execute" or source_root is None:
        raise GitOpsValidationError("invalid_input")
    plan = build_plan(contract, request, source_root)
    return execute_gitops_plan(plan, source_root, state_root)


__all__ = [
    "GitOpsPlan",
    "GitOpsRequest",
    "GitOpsResult",
    "GitOpsValidationError",
    "assert_zero_gitops_residue",
    "bounded_path",
    "build_plan",
    "cleanup_gitops_state",
    "execute_gitops_plan",
    "load_gitops_contract",
    "request_from_environment",
    "safe_relative",
    "source_trust_from_environment",
    "validate",
]
