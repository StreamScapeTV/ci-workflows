"""Contract-owned device plan construction and typed-plan verification."""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Mapping

from .device_admission import source_trust_from_environment
from .device_contract_common import require
from .device_profile_contract import _profile
from .device_types import DevicePlan, DeviceProfile, DeviceRequest, DeviceValidationError, canonical_json

def build_plan(contract: Mapping[str, Any], request: DeviceRequest) -> DevicePlan:
    if request.source_trust == "trusted-pr":
        require(request.repository == "StreamScapeTV/ci-workflows", "source_admission_rejected")
        require(request.event_name == "pull_request", "source_admission_rejected")
    else:
        require(request.source_trust == "trusted-exact", "source_admission_rejected")
        require(request.event_name in {"workflow_call", "workflow_dispatch"}, "authorization_rejected")

    matches: list[DeviceProfile] = []
    for profile_id, raw in contract["profiles"].items():
        profile = _profile(str(profile_id), raw, contract)
        if (
            request.repository in profile.repositories
            and request.family is profile.family
            and request.capability in profile.capabilities
            and request.command_profile == profile.command_profile.profile_id
            and request.script_path == profile.command_profile.test_script
            and request.device_alias in profile.aliases
        ):
            matches.append(profile)
    require(len(matches) == 1, "device_profile_rejected")
    profile = matches[0]
    require(request.max_duration_minutes <= profile.timeout_minutes, "authorization_rejected")
    if request.source_trust == "trusted-pr":
        require(profile.synthetic_only, "authorization_rejected")
    if request.evidence_exception_id is not None:
        require(request.evidence_exception_id in profile.artifact_exception_ids, "artifact_exception_rejected")

    backend = profile.command_profile.live_backend_profile
    if backend is not None:
        require(backend in profile.live_backend_profiles, "live_backend_rejected")
        require(request.live_backend_secret_present, "live_backend_rejected")
        backend_contract = contract["live_backend_profiles"][backend]
        require(
            request.repository in backend_contract["allowed_repositories"]
            and backend_contract["production_forbidden"] is True,
            "live_backend_rejected",
        )
    else:
        require(not request.live_backend_secret_present, "live_backend_rejected")

    alias_class = str(profile.aliases[request.device_alias])
    group = f"device-validation-{profile.profile_id}-{request.family.value}-{alias_class}"
    require(re.fullmatch(r"[a-z0-9-]{16,180}", group) is not None, "group_injection_rejected")

    # No family has explicit owner authorization in this chat. Runner labels,
    # device presence, branch text, or a secret never changes this decision.
    execution_authorized = False
    return DevicePlan(
        request=request,
        profile=profile,
        alias_class=alias_class,
        execution_authorized=execution_authorized,
        authorization_failure="physical_authorization_required",
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
        "packet_version",
        "repository",
        "admitted_sha",
        "source_trust",
        "event_name",
        "run_id",
        "request_id",
        "issue_number",
        "device_family",
        "device_capability",
        "device_alias",
        "alias_class",
        "device_profile",
        "command_profile",
        "script_path",
        "max_duration_minutes",
        "evidence_exception_id",
        "planner_runner_profile",
        "execution_overlay_profile",
        "base_runner_profile",
        "runs_on_json",
        "workspace_profile",
        "serialization_backend",
        "concurrency_group",
        "cancel_in_progress",
        "execution_authorized",
        "authorization_failure",
    }
    require(set(packet) == required, "typed_plan_rejected")
    require(packet["packet_version"] == "device-plan/1", "typed_plan_rejected")
    require(packet["repository"] == environment.get("GITHUB_REPOSITORY"), "typed_plan_rejected")
    require(packet["admitted_sha"] == environment.get("INPUT_ADMITTED_SHA"), "source_mismatch")
    derived = source_trust_from_environment(environment, str(packet["admitted_sha"]))
    require(packet["source_trust"] == derived, "typed_plan_rejected")
    require(packet["serialization_backend"] == contract["serialization_contract"]["backend"], "typed_plan_rejected")
    require(packet["cancel_in_progress"] is False, "group_injection_rejected")
    require(packet["execution_authorized"] is False, "authorization_rejected")
    require(packet["authorization_failure"] == "physical_authorization_required", "authorization_rejected")
    expected_group = (
        f"device-validation-{packet['device_profile']}-"
        f"{packet['device_family']}-{packet['alias_class']}"
    )
    require(packet["concurrency_group"] == expected_group, "group_injection_rejected")
    return packet

