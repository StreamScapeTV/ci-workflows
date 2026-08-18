"""Contract-owned generic device plan construction and typed-plan verification."""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Mapping

from .device_admission import request_from_environment, source_trust_from_environment
from .device_contract_common import CAPABILITY, FULL_SHA, REPOSITORY, RUN_ID, parse_request_id, require
from .device_profile_contract import profile_for_request
from .device_types import DeviceFamily, DevicePlan, DeviceRequest, DeviceValidationError, canonical_json


def build_plan(contract: Mapping[str, Any], request: DeviceRequest) -> DevicePlan:
    if request.source_trust == "trusted-pr":
        require(request.repository == "StreamScapeTV/ci-workflows", "source_admission_rejected")
        require(
            request.event_name in set(contract.get("synthetic_allowed_events", ())),
            "source_admission_rejected",
        )
    else:
        require(request.source_trust == "trusted-exact", "source_admission_rejected")
        require(request.event_name in {"workflow_call", "workflow_dispatch"}, "authorization_rejected")

    profile = profile_for_request(contract, request)
    require(request.max_duration_minutes <= profile.timeout_minutes, "authorization_rejected")
    if request.source_trust == "trusted-pr":
        require(profile.synthetic_only, "authorization_rejected")
    if request.evidence_exception_id is not None:
        require(request.evidence_exception_id in profile.artifact_exception_ids, "artifact_exception_rejected")

    group = f"device-validation-{request.family.value}-{request.capability}-{request.host_capacity}"
    require(re.fullmatch(r"[a-z0-9-]{16,180}", group) is not None, "group_injection_rejected")
    execution_authorized = bool(request.authorization_receipt_present and not profile.synthetic_only)
    authorization_failure = "" if execution_authorized else "physical_authorization_required"
    return DevicePlan(
        request=request,
        profile=profile,
        execution_authorized=execution_authorized,
        authorization_failure=authorization_failure,
        planner_runner_profile=str(contract["planner_runner_profile"]),
        execution_overlay_profile=str(contract["execution_overlay_profile"]),
        serialization_backend=str(contract["serialization_contract"]["backend"]),
        concurrency_group=group,
    )


def validate_typed_plan(
    raw_plan: str,
    expected_hash: str,
    *,
    contract: Mapping[str, Any],
    environment: Mapping[str, str],
) -> Mapping[str, Any]:
    require(re.fullmatch(r"[0-9a-f]{64}", expected_hash) is not None, "typed_plan_hash_mismatch")
    try:
        packet = json.loads(raw_plan)
    except json.JSONDecodeError as error:
        raise DeviceValidationError("typed_plan_rejected") from error
    require(isinstance(packet, Mapping), "typed_plan_rejected")
    require(canonical_json(packet) == raw_plan, "typed_plan_rejected")
    require(hashlib.sha256(raw_plan.encode("utf-8")).hexdigest() == expected_hash, "typed_plan_hash_mismatch")
    required = {
        "packet_version", "repository", "admitted_sha", "source_trust", "event_name", "run_id",
        "request_id", "issue_number", "device_family", "device_capability", "host_capacity",
        "command_plan", "max_duration_minutes", "evidence_exception_id", "planner_runner_profile",
        "execution_overlay_profile", "runs_on_json", "workspace_profile", "serialization_backend",
        "concurrency_group", "cancel_in_progress", "execution_authorized", "authorization_failure",
    }
    require(set(packet) == required, "typed_plan_rejected")
    require(packet["packet_version"] == "device-plan/2", "typed_plan_rejected")
    require(packet["repository"] == environment.get("GITHUB_REPOSITORY"), "typed_plan_rejected")
    require(packet["admitted_sha"] == environment.get("INPUT_ADMITTED_SHA"), "source_mismatch")
    derived = source_trust_from_environment(environment, str(packet["admitted_sha"]))
    require(packet["source_trust"] == derived, "typed_plan_rejected")
    require(packet["serialization_backend"] == contract["serialization_contract"]["backend"], "typed_plan_rejected")
    require(packet["cancel_in_progress"] is False, "group_injection_rejected")
    require(type(packet["execution_authorized"]) is bool, "authorization_rejected")
    expected_failure = "" if packet["execution_authorized"] else "physical_authorization_required"
    require(packet["authorization_failure"] == expected_failure, "authorization_rejected")
    require(
        all(isinstance(packet[field], str) for field in (
            "repository", "admitted_sha", "event_name", "run_id", "request_id", "device_family",
            "device_capability", "host_capacity", "evidence_exception_id", "planner_runner_profile",
            "execution_overlay_profile", "runs_on_json", "workspace_profile", "serialization_backend",
            "concurrency_group", "authorization_failure",
        )),
        "typed_plan_rejected",
    )
    require(type(packet["issue_number"]) is int and type(packet["max_duration_minutes"]) is int, "typed_plan_rejected")
    require(isinstance(packet["command_plan"], Mapping), "typed_plan_rejected")
    try:
        runs_on = json.loads(str(packet["runs_on_json"]))
    except json.JSONDecodeError as error:
        raise DeviceValidationError("typed_plan_rejected") from error
    require(
        isinstance(runs_on, list)
        and runs_on
        and all(isinstance(label, str) and label for label in runs_on)
        and canonical_json(runs_on) == packet["runs_on_json"],
        "typed_plan_rejected",
    )
    repository = str(packet["repository"])
    admitted_sha = str(packet["admitted_sha"])
    require(REPOSITORY.fullmatch(repository) is not None, "typed_plan_rejected")
    require(FULL_SHA.fullmatch(admitted_sha) is not None, "typed_plan_rejected")
    require(packet["event_name"] == environment.get("GITHUB_EVENT_NAME"), "typed_plan_rejected")
    expected_run_id = f"{environment.get('GITHUB_RUN_ID', '').strip()}:{environment.get('GITHUB_RUN_ATTEMPT', '').strip()}"
    require(packet["run_id"] == expected_run_id and RUN_ID.fullmatch(expected_run_id), "typed_plan_rejected")
    issue_number = parse_request_id(str(packet["request_id"]))
    require(packet["issue_number"] == issue_number, "typed_plan_rejected")
    require(CAPABILITY.fullmatch(str(packet["device_capability"])) is not None, "typed_plan_rejected")
    try:
        DeviceFamily(str(packet["device_family"]))
    except ValueError as error:
        raise DeviceValidationError("typed_plan_rejected") from error
    rebuilt_request = request_from_environment(environment, contract)
    expected_packet = build_plan(contract, rebuilt_request).packet(runs_on_json=str(packet["runs_on_json"]))
    require(packet == expected_packet, "typed_plan_rejected")
    return packet
