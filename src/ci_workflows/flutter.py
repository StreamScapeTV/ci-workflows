"""Public façade for reusable Flutter validation."""
from __future__ import annotations

from pathlib import Path
from typing import Mapping

from .flutter_contract import (
    FlutterValidationError,
    build_plan,
    bounded_path,
    discover_flutter_pin,
    load_flutter_contract,
    safe_relative,
)
from .flutter_execution import (
    CommandRunner,
    assert_zero_flutter_residue,
    cleanup_flutter_state,
    execute_flutter_plan,
)
from .flutter_types import FlutterPlan, FlutterProfile, FlutterRequest, FlutterResult

__all__ = [
    "FlutterPlan", "FlutterProfile", "FlutterRequest", "FlutterResult",
    "FlutterValidationError", "assert_zero_flutter_residue", "bounded_path",
    "cleanup_flutter_state", "discover_flutter_pin", "load_flutter_contract",
    "request_from_environment", "safe_relative", "validate",
]


def request_from_environment(
    environment: Mapping[str, str],
    contract: Mapping[str, object],
) -> FlutterRequest:
    forbidden = set(contract["forbidden_inputs"])
    for key, value in environment.items():
        if key.startswith("INPUT_") and value:
            logical = key.removeprefix("INPUT_").lower()
            if logical in forbidden:
                raise FlutterValidationError("forbidden_input")
    try:
        profile = FlutterProfile(environment["INPUT_VALIDATION_PROFILE"])
        return FlutterRequest(
            admitted_sha=environment["INPUT_ADMITTED_SHA"],
            consumer_contract=environment["INPUT_CONSUMER_CONTRACT"],
            validation_profile=profile,
            source_trust=environment.get("INPUT_SOURCE_TRUST", "trusted-pr"),
        )
    except (KeyError, ValueError) as error:
        raise FlutterValidationError("invalid_input") from error


def validate(
    *,
    contract_root: Path,
    source_root: Path | None,
    state_root: Path | None,
    request: FlutterRequest,
    phase: str,
    environment: Mapping[str, str] | None = None,
    runner: CommandRunner | None = None,
) -> FlutterPlan | FlutterResult:
    contract = load_flutter_contract(contract_root)
    plan = build_plan(contract, request, source_root)
    if phase == "plan":
        return plan
    if phase != "execute" or source_root is None or state_root is None:
        raise FlutterValidationError("invalid_input")
    return execute_flutter_plan(
        plan=plan,
        contract_root=contract_root,
        source_root=source_root,
        state_root=state_root,
        runner=runner,
        environment=environment,
    )
