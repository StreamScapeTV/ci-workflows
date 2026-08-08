"""Public façade for reusable Flutter validation."""
from __future__ import annotations

import json
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
    "FlutterPlan",
    "FlutterProfile",
    "FlutterRequest",
    "FlutterResult",
    "FlutterValidationError",
    "assert_zero_flutter_residue",
    "bounded_path",
    "cleanup_flutter_state",
    "discover_flutter_pin",
    "load_flutter_contract",
    "request_from_environment",
    "safe_relative",
    "source_trust_from_environment",
    "validate",
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
        raise FlutterValidationError("invalid_input") from error
    if not isinstance(head_repository, str) or not head_repository:
        raise FlutterValidationError("invalid_input")
    return (
        "trusted-pr"
        if head_repository == environment.get("GITHUB_REPOSITORY")
        else "untrusted-fork"
    )


def _match_optional(environment: Mapping[str, str], key: str, expected: str) -> None:
    value = environment.get(key, "")
    if value and value != expected:
        raise FlutterValidationError("forbidden_input")


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
        repository = environment["GITHUB_REPOSITORY"]
        if not repository:
            raise FlutterValidationError("invalid_input")
        profile = FlutterProfile(environment["INPUT_VALIDATION_PROFILE"])
        consumer_id = environment.get("INPUT_COMMAND_PROFILE") or environment[
            "INPUT_CONSUMER_CONTRACT"
        ]
        consumer = contract["consumer_contracts"][consumer_id]
        profile_contract = consumer["profiles"][profile.value]
        pin_sources = [
            value for value in consumer["pin_sources"] if value != "contract"
        ]
        expected_version_file = pin_sources[0] if len(pin_sources) == 1 else ""
        _match_optional(environment, "INPUT_VERSION_FILE", expected_version_file)
        _match_optional(environment, "INPUT_WORKING_DIRECTORY", ".")
        _match_optional(
            environment,
            "INPUT_SCRIPT_PATH",
            profile_contract.get("gate_path") or "",
        )
        expected_platform = {
            FlutterProfile.ANDROID_DEBUG: "android",
            FlutterProfile.IOS_SIMULATOR: "ios-simulator",
            FlutterProfile.DEVICE_HANDOFF: "device-handoff",
        }.get(profile, "flutter")
        _match_optional(environment, "INPUT_PLATFORM", expected_platform)
        if environment.get("INPUT_ARTIFACT_EXCEPTION_ID", ""):
            raise FlutterValidationError("forbidden_input")
        source_trust = environment.get("INPUT_SOURCE_TRUST") or (
            source_trust_from_environment(environment)
            if environment.get("GITHUB_EVENT_NAME")
            else "trusted-pr"
        )
        return FlutterRequest(
            repository=repository,
            admitted_sha=environment["INPUT_ADMITTED_SHA"],
            consumer_contract=consumer_id,
            validation_profile=profile,
            source_trust=source_trust,
        )
    except FlutterValidationError:
        raise
    except (KeyError, TypeError, ValueError) as error:
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
