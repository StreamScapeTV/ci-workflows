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
from .android_policy import (
    SOURCE_POLICY_PATH,
    load_android_source_policy,
    verify_android_repository_policy,
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


def _project_policy_failure(
    source_policy: Mapping[str, object] | None,
    error: FoundationError,
) -> AndroidValidationError:
    instruction = error.instruction
    code = "policy_contract_failed"
    if source_policy is not None:
        projection = source_policy.get("failure_projection")
        if isinstance(projection, Mapping):
            exact = projection.get("exact")
            if isinstance(exact, Mapping):
                candidate = exact.get(instruction)
                if isinstance(candidate, str):
                    code = candidate
            if code == "policy_contract_failed":
                prefixes = projection.get("prefixes")
                if isinstance(prefixes, list):
                    for entry in prefixes:
                        if not isinstance(entry, Mapping):
                            continue
                        prefix = entry.get("prefix")
                        candidate = entry.get("code")
                        if (
                            isinstance(prefix, str)
                            and isinstance(candidate, str)
                            and instruction.startswith(prefix)
                        ):
                            code = candidate
                            break
                fallback = projection.get("fallback")
                if (
                    code == "policy_contract_failed"
                    and isinstance(fallback, str)
                ):
                    code = fallback

    subject = getattr(error, "subject", None)
    if not isinstance(subject, str):
        if instruction.startswith("artifact_") or instruction in {
            "undeclared_artifact",
            "unused_artifact_exception",
        }:
            subject = "contracts/artifact-exceptions.json"
        elif source_policy is None or instruction.startswith(
            "android_source_policy"
        ):
            subject = SOURCE_POLICY_PATH
        else:
            subject = "contracts/repository-policy.json"
    return AndroidValidationError(
        code,
        rule_id=instruction,
        subject=subject,
    )


def _verify_policy(
    source_root: Path,
    request: AndroidValidationRequest,
    contract_root: Path,
    phase: str,
    environment: Mapping[str, str],
) -> None:
    source_policy: Mapping[str, object] | None = None
    try:
        source_policy = load_android_source_policy(contract_root)
        verify_android_repository_policy(
            source_root,
            request=request,
            phase=phase,
            contract_root=contract_root,
            environment=environment,
            source_policy=source_policy,
        )
    except FoundationError as error:
        raise _project_policy_failure(source_policy, error) from error


def validate(
    *,
    contract_root: Path,
    source_root: Path | None,
    state_root: Path | None,
    request: AndroidValidationRequest,
    phase: str,
    environment: Mapping[str, str],
) -> AndroidValidationPlan | AndroidValidationResult:
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
    _verify_policy(
        exact_source,
        request,
        contract_root,
        "before",
        environment,
    )
    result = execute_android_plan(
        exact_source,
        registered_state,
        plan,
        contract,
        environment,
    )
    _verify_policy(
        exact_source,
        request,
        contract_root,
        "after",
        environment,
    )
    return result


__all__ = (
    "CONTRACT_PATH",
    "SOURCE_POLICY_PATH",
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
    "load_android_source_policy",
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
