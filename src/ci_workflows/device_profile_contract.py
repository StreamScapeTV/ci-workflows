"""Validation and typed loading for the generic physical-device contract."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from .device_contract_common import (
    EVIDENCE_CONTRACT_PATH,
    PROFILE_CONTRACT_PATH,
    REQUEST_ID,
    RUN_ID,
    _read_json,
    require,
    strings,
    version_tuple,
)
from .device_types import DeviceCommandProfile, DeviceFamily, DeviceProfile, DeviceRequest

POLICY_KEYS = {
    "models",
    "version_policy",
    "selection_policy",
    "allowed_host_capacities",
    "workspace_profiles",
    "timeout_minutes",
    "artifact_exception_ids",
    "connection_states",
    "health_states",
}
REQUIRED_FORBIDDEN_INPUTS = {
    "arbitrary_command",
    "arguments",
    "callback",
    "command",
    "command_profile",
    "concurrency_group",
    "cancel_in_progress",
    "database_url",
    "deletion_path",
    "deployment",
    "device_alias",
    "device_identifier",
    "device_selector",
    "environment_dump",
    "keychain",
    "keystore",
    "kubernetes",
    "live_backend_url",
    "notarization",
    "physical_device",
    "production_endpoint",
    "provisioning_profile",
    "raw_identifier",
    "registry",
    "release",
    "runner",
    "runner_labels",
    "runs_on",
    "script_path",
    "secret_name",
    "serial",
    "shell",
    "signing_identity",
    "source_trust",
    "store",
    "testflight",
    "udid",
    "workspace_root",
}
SYNTHETIC_MODELS = {
    DeviceFamily.ANDROID: ("synthetic-phone", "synthetic-tablet"),
    DeviceFamily.IOS: ("synthetic-iphone",),
    DeviceFamily.TVOS: ("synthetic-apple-tv",),
}


def _validate_family_policy(
    family: DeviceFamily,
    raw: Mapping[str, Any],
    contract: Mapping[str, Any],
) -> None:
    require(set(raw) == POLICY_KEYS, "device_profile_rejected")
    strings(raw.get("models"), nonempty=True, code="device_profile_rejected")
    policy = raw.get("version_policy")
    require(isinstance(policy, Mapping) and policy, "device_profile_rejected")
    if family is DeviceFamily.ANDROID:
        require(set(policy) == {"api_min", "api_max"}, "device_profile_rejected")
        require(
            isinstance(policy["api_min"], int)
            and isinstance(policy["api_max"], int)
            and 1 <= policy["api_min"] <= policy["api_max"] <= 100,
            "device_profile_rejected",
        )
    else:
        require(set(policy) == {"os_min", "os_max"}, "device_profile_rejected")
        require(
            version_tuple(str(policy["os_min"]))
            <= version_tuple(str(policy["os_max"])),
            "device_profile_rejected",
        )

    require(
        raw.get("selection_policy") in {"unique", "identity-hash"},
        "device_profile_rejected",
    )
    capacities = strings(
        raw.get("allowed_host_capacities"),
        nonempty=True,
        code="device_profile_rejected",
    )
    require(set(capacities) <= {"mobile", "apple"}, "device_profile_rejected")
    if family is DeviceFamily.ANDROID:
        require(set(capacities) == {"mobile", "apple"}, "device_profile_rejected")
    else:
        require(capacities == ["apple"], "device_profile_rejected")

    workspaces = raw.get("workspace_profiles")
    require(
        isinstance(workspaces, Mapping)
        and set(workspaces) == set(capacities)
        and all(value in {"native", "apple"} for value in workspaces.values()),
        "device_profile_rejected",
    )
    require(
        workspaces.get("apple", "apple") == "apple"
        and (family is not DeviceFamily.ANDROID or workspaces.get("mobile") == "native"),
        "device_profile_rejected",
    )
    require(
        isinstance(raw.get("timeout_minutes"), int)
        and 1 <= int(raw["timeout_minutes"]) <= int(contract["hard_timeout_minutes"]),
        "device_profile_rejected",
    )
    require(
        set(strings(raw.get("artifact_exception_ids"), code="artifact_exception_rejected"))
        <= set(contract["artifact_exceptions"]),
        "artifact_exception_rejected",
    )
    strings(raw.get("connection_states"), nonempty=True, code="device_profile_rejected")
    strings(raw.get("health_states"), nonempty=True, code="device_profile_rejected")


def load_device_contract(root: Path) -> Mapping[str, Any]:
    contract = _read_json(root / PROFILE_CONTRACT_PATH)
    require(
        (
            contract.get("schema_version"),
            contract.get("contract_version"),
            contract.get("organization"),
            contract.get("workflow_api"),
            contract.get("stable_check_name"),
        )
        == (
            1,
            "2.0.0",
            "StreamScapeTV",
            "validation.device",
            "CI / Physical device validation",
        ),
        "invalid_input",
    )
    require(contract.get("planner_runner_profile") == "portable", "invalid_input")
    require(
        contract.get("execution_overlay_profile") == "physical-device",
        "invalid_input",
    )
    require(contract.get("hard_timeout_minutes") == 240, "invalid_input")
    require(contract.get("artifact_policy") == "zero-default", "invalid_input")
    require(contract.get("request_id_regex") == REQUEST_ID.pattern, "request_identity_rejected")
    require(contract.get("run_id_regex") == RUN_ID.pattern, "request_identity_rejected")
    require(
        set(strings(contract.get("public_inputs"), nonempty=True))
        == {
            "admitted_sha",
            "arguments_json",
            "cleanup_script_path",
            "device_capability",
            "device_family",
            "environment_json",
            "evidence_exception_id",
            "evidence_script_path",
            "host_capacity",
            "max_duration_minutes",
            "prepare_script_path",
            "request_id",
            "test_script_path",
        },
        "invalid_input",
    )
    require(
        strings(contract.get("public_outputs"), nonempty=True)
        == ["result", "device_evidence_id", "artifact_exception_used", "request_id"],
        "invalid_input",
    )
    require(
        strings(contract.get("public_secrets"), nonempty=True)
        == ["device_authorization_receipt"],
        "invalid_input",
    )
    require(
        set(strings(contract.get("families"), nonempty=True))
        == {item.value for item in DeviceFamily},
        "unsupported_family",
    )
    require(
        REQUIRED_FORBIDDEN_INPUTS <= set(strings(contract.get("forbidden_inputs"), nonempty=True)),
        "forbidden_input",
    )
    require(
        "profiles" not in contract
        and "command_profiles" not in contract
        and "live_backend_profiles" not in contract,
        "device_profile_rejected",
    )
    serialization = contract.get("serialization_contract")
    require(
        isinstance(serialization, Mapping)
        and serialization.get("backend") == "github-actions-job-concurrency"
        and serialization.get("cancel_in_progress") is False
        and serialization.get("caller_override") is False
        and serialization.get("fencing_token") is True
        and serialization.get("group_scope") == ["device_family", "device_capability", "host_capacity"],
        "group_injection_rejected",
    )
    authorization = contract.get("owner_authorization")
    require(
        isinstance(authorization, Mapping)
        and authorization.get("mode") == "exact-family-runtime-receipt"
        and authorization.get("authorized_families") == []
        and authorization.get("runner_or_secret_is_authorization") is False
        and authorization.get("failure_code") == "physical_authorization_required",
        "authorization_rejected",
    )
    lock = contract.get("lock_contract")
    require(
        isinstance(lock, Mapping)
        and lock.get("production_adapter") == "device-lock/1:posix-shared-root-v1"
        and lock.get("temporary_reference_adapter") == "in-memory-tests-only"
        and lock.get("cross_run_fencing_claimed") is True
        and lock.get("agent_state_transport_used") is False,
        "invalid_input",
    )
    require(isinstance(contract.get("artifact_exceptions"), Mapping), "invalid_input")
    policies = contract.get("family_policies")
    require(
        isinstance(policies, Mapping)
        and set(policies) == {item.value for item in DeviceFamily},
        "device_profile_rejected",
    )
    for family in DeviceFamily:
        raw = policies[family.value]
        require(isinstance(raw, Mapping), "device_profile_rejected")
        _validate_family_policy(family, raw, contract)
    require(
        isinstance(contract.get("cleanup"), Mapping)
        and all(value is True for value in contract["cleanup"].values()),
        "cleanup_failed",
    )
    return contract


def load_evidence_contract(root: Path) -> Mapping[str, Any]:
    contract = _read_json(root / EVIDENCE_CONTRACT_PATH, "evidence_policy_failed")
    require(
        (
            contract.get("schema_version"),
            contract.get("contract_version"),
            contract.get("packet_version"),
        ) == (1, "1.1.0", "device-evidence/1"),
        "evidence_policy_failed",
    )
    required = set(strings(contract.get("required_fields"), nonempty=True))
    allowed = set(strings(contract.get("allowed_fields"), nonempty=True))
    require(required == allowed, "evidence_policy_failed")
    require(
        set(contract.get("certification_scope_by_family", {}))
        == {item.value for item in DeviceFamily},
        "evidence_policy_failed",
    )
    strings(contract.get("required_limitations"), nonempty=True)
    assertions = strings(contract.get("allowed_assertions"), nonempty=True, code="evidence_policy_failed")
    require(
        assertions == sorted(assertions)
        and len(assertions) <= int(contract["maximum_assertions"]),
        "evidence_policy_failed",
    )
    return contract


def profile_for_request(contract: Mapping[str, Any], request: DeviceRequest) -> DeviceProfile:
    """Combine generic production family policy with caller-owned bounded commands.

    Synthetic source uses test-only model classes so permanent smoke can exercise
    selection without adding synthetic or product identities to the public contract.
    """

    raw = contract["family_policies"][request.family.value]
    capacities = tuple(raw["allowed_host_capacities"])
    require(request.host_capacity in capacities, "device_profile_rejected")
    workspace_profile = str(raw["workspace_profiles"][request.host_capacity])
    synthetic = request.source_trust == "trusted-pr"
    command_profile = DeviceCommandProfile(
        profile_id="caller-plan",
        prepare_script=request.prepare_script_path,
        test_script=request.test_script_path,
        evidence_script=request.evidence_script_path,
        cleanup_script=request.cleanup_script_path,
        fixed_arguments=request.arguments,
        environment=dict(request.environment),
        state_restoration=(
            "caller-cleanup-script",
            "central-device-state",
            "production-lock-release",
        ),
    )
    return DeviceProfile(
        profile_id=request.family.value,
        family=request.family,
        models=SYNTHETIC_MODELS[request.family] if synthetic else tuple(raw["models"]),
        version_policy=dict(raw["version_policy"]),
        selection_policy=(
            "identity-hash"
            if synthetic and request.family is DeviceFamily.ANDROID
            else str(raw["selection_policy"])
        ),
        base_runner_profile=request.host_capacity,
        workspace_profile=workspace_profile,
        command_profile=command_profile,
        timeout_minutes=int(raw["timeout_minutes"]),
        artifact_exception_ids=tuple(raw["artifact_exception_ids"]),
        synthetic_only=synthetic,
        connection_states=tuple(raw["connection_states"]),
        health_states=tuple(raw["health_states"]),
    )
