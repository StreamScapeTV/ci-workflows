"""Public façade for reusable Apple validation."""
from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path
from typing import Mapping

from .apple_contract import (
    AppleValidationError,
    bounded_path,
    build_plan,
    load_apple_contract,
    safe_relative,
)
from .apple_execution import (
    CommandRunner,
    assert_zero_apple_residue,
    cleanup_apple_state,
    execute_apple_plan,
    parse_swift_identity,
    parse_xcode_identity,
    select_simulator,
    verify_toolchain,
)
from .apple_types import (
    AppleProfile,
    AppleValidationPlan,
    AppleValidationRequest,
    AppleValidationResult,
)

_ALLOWED_INPUT_KEYS = {
    "admitted_sha",
    "artifact_exception_id",
    "command_profile",
    "consumer_contract",
    "destination_profile",
    "platform",
    "scheme",
    "script_path",
    "source_trust",
    "validation_profile",
    "version_file",
    "working_directory",
}


__all__ = [
    "AppleProfile",
    "AppleValidationError",
    "AppleValidationPlan",
    "AppleValidationRequest",
    "AppleValidationResult",
    "assert_zero_apple_residue",
    "bounded_path",
    "cleanup_apple_state",
    "load_apple_contract",
    "parse_swift_identity",
    "parse_xcode_identity",
    "request_from_environment",
    "resolve_plan",
    "safe_relative",
    "select_simulator",
    "source_trust_from_environment",
    "validate",
    "verify_toolchain",
]


def source_trust_from_environment(environment: Mapping[str, str]) -> str:
    if environment.get("GITHUB_EVENT_NAME") != "pull_request":
        return "trusted-exact"
    try:
        payload = json.loads(
            Path(environment.get("GITHUB_EVENT_PATH", "")).read_text(
                encoding="utf-8"
            )
        )
        head_repository = payload["pull_request"]["head"]["repo"]["full_name"]
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as error:
        raise AppleValidationError("invalid_input") from error
    if not isinstance(head_repository, str) or not head_repository:
        raise AppleValidationError("invalid_input")
    return (
        "trusted-pr"
        if head_repository == environment.get("GITHUB_REPOSITORY")
        else "untrusted-fork"
    )


def _optional(environment: Mapping[str, str], key: str) -> str | None:
    value = environment.get(key, "").strip()
    return value or None


def request_from_environment(
    environment: Mapping[str, str],
    contract: Mapping[str, object],
) -> AppleValidationRequest:
    forbidden = set(contract["forbidden_inputs"])
    for key, value in environment.items():
        if key.startswith("INPUT_") and value:
            logical = key.removeprefix("INPUT_").lower()
            if logical in forbidden or logical not in _ALLOWED_INPUT_KEYS:
                raise AppleValidationError("forbidden_input")
    try:
        repository = environment["GITHUB_REPOSITORY"].strip()
        admitted_sha = environment["INPUT_ADMITTED_SHA"].strip()
        consumer_contract = (
            environment.get("INPUT_COMMAND_PROFILE")
            or environment["INPUT_CONSUMER_CONTRACT"]
        ).strip()
        profile = AppleProfile(environment["INPUT_VALIDATION_PROFILE"].strip())
        source_trust = environment.get("INPUT_SOURCE_TRUST", "").strip() or (
            source_trust_from_environment(environment)
            if environment.get("GITHUB_EVENT_NAME")
            else "trusted-pr"
        )
        return AppleValidationRequest(
            repository=repository,
            admitted_sha=admitted_sha,
            consumer_contract=consumer_contract,
            validation_profile=profile,
            source_trust=source_trust,
            version_file=_optional(environment, "INPUT_VERSION_FILE"),
            working_directory=_optional(environment, "INPUT_WORKING_DIRECTORY"),
            script_path=_optional(environment, "INPUT_SCRIPT_PATH"),
            platform=_optional(environment, "INPUT_PLATFORM"),
            scheme=_optional(environment, "INPUT_SCHEME"),
            destination_profile=_optional(
                environment,
                "INPUT_DESTINATION_PROFILE",
            ),
            artifact_exception_id=_optional(
                environment,
                "INPUT_ARTIFACT_EXCEPTION_ID",
            ),
        )
    except AppleValidationError:
        raise
    except (KeyError, TypeError, ValueError) as error:
        raise AppleValidationError("invalid_input") from error


def resolve_plan(
    contract: Mapping[str, object],
    request: AppleValidationRequest,
) -> AppleValidationPlan:
    """Resolve one Apple plan with a collision-safe simulator identity."""

    plan = build_plan(contract, request)
    simulator = plan.simulator
    if simulator is None:
        return plan
    identity_material = "\n".join(
        (request.repository, plan.task_profile, request.admitted_sha)
    )
    simulator_scope = hashlib.sha256(
        identity_material.encode("utf-8")
    ).hexdigest()[:12]
    scoped_prefix = f"{simulator.device_name_prefix} {simulator_scope}"
    if len(scoped_prefix) > 128:
        raise AppleValidationError("simulator_contract_invalid")
    scoped_simulator = replace(
        simulator,
        device_name_prefix=scoped_prefix,
    )
    return replace(plan, simulator=scoped_simulator)


def validate(
    *,
    contract_root: Path,
    source_root: Path | None,
    state_root: Path | None,
    request: AppleValidationRequest,
    phase: str,
    environment: Mapping[str, str] | None = None,
    runner: CommandRunner | None = None,
) -> AppleValidationPlan | AppleValidationResult:
    contract = load_apple_contract(contract_root)
    plan = resolve_plan(contract, request)
    if phase == "plan":
        return plan
    if phase != "execute" or source_root is None or state_root is None:
        raise AppleValidationError("invalid_input")
    return execute_apple_plan(
        plan=plan,
        source_root=source_root,
        state_root=state_root,
        runner=runner,
        environment=environment,
    )
