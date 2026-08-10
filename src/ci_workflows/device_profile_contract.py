"""Validation and typed loading for device profile/evidence contracts."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Mapping

from .device_contract_common import (
    ALIAS, CAPABILITY, EVIDENCE_CONTRACT_PATH, IDENTIFIER, PROFILE_CONTRACT_PATH,
    REPOSITORY, REQUEST_ID, RUN_ID, _read_json, require, safe_relative, strings,
    version_tuple,
)
from .device_types import DeviceCommandProfile, DeviceFamily, DeviceProfile

PROFILE_KEYS = {
    "repositories", "products", "family", "capabilities", "models",
    "version_policy", "aliases", "selection_policy", "base_runner_profile",
    "workspace_profile", "command_profile", "timeout_minutes",
    "artifact_exception_ids", "live_backend_profiles", "synthetic_only",
    "connection_states", "health_states",
}
COMMAND_KEYS = {
    "prepare_script", "test_script", "evidence_script", "cleanup_script",
    "fixed_arguments", "live_backend_profile", "state_restoration",
}
REQUIRED_FORBIDDEN_INPUTS = {
    "arbitrary_command", "arguments", "callback", "command",
    "concurrency_group", "cancel_in_progress", "database_url",
    "deletion_path", "deployment", "device_identifier", "device_selector",
    "environment_dump", "keychain", "keystore", "kubernetes",
    "live_backend_url", "notarization", "physical_device",
    "production_endpoint", "provisioning_profile", "raw_identifier",
    "registry", "release", "runner", "runner_labels", "runs_on",
    "secret_name", "serial", "shell", "signing_identity", "source_trust",
    "store", "testflight", "udid", "workspace_root",
}

def _validate_command_profiles(contract: Mapping[str, Any]) -> None:
    profiles = contract.get("command_profiles")
    require(isinstance(profiles, Mapping) and profiles, "invalid_input")
    for profile_id, raw in profiles.items():
        require(
            isinstance(profile_id, str)
            and IDENTIFIER.fullmatch(profile_id) is not None
            and isinstance(raw, Mapping)
            and set(raw) == COMMAND_KEYS,
            "command_profile_rejected",
        )
        for field in ("prepare_script", "test_script", "evidence_script", "cleanup_script"):
            safe_relative(raw.get(field), "command_profile_rejected")
        arguments = strings(raw.get("fixed_arguments"), unique=False, code="command_profile_rejected")
        require(
            len(arguments) <= 16
            and all(
                len(item) <= 128
                and "\n" not in item
                and "\r" not in item
                and re.search(r"[;&|`$<>]", item) is None
                for item in arguments
            ),
            "command_profile_rejected",
        )
        backend = raw.get("live_backend_profile")
        require(
            backend is None
            or (isinstance(backend, str) and IDENTIFIER.fullmatch(backend) is not None),
            "live_backend_rejected",
        )
        strings(raw.get("state_restoration"), nonempty=True)

def _validate_profile(
    profile_id: str, raw: Mapping[str, Any], contract: Mapping[str, Any]
) -> None:
    require(IDENTIFIER.fullmatch(profile_id) is not None and set(raw) == PROFILE_KEYS, "device_profile_rejected")
    repositories = strings(raw.get("repositories"), nonempty=True)
    require(all(REPOSITORY.fullmatch(value) is not None for value in repositories), "device_profile_rejected")
    strings(raw.get("products"), nonempty=True)
    require(raw.get("family") in {item.value for item in DeviceFamily}, "unsupported_family")
    capabilities = strings(raw.get("capabilities"), nonempty=True)
    require(all(CAPABILITY.fullmatch(value) is not None for value in capabilities), "device_profile_rejected")
    strings(raw.get("models"), nonempty=True)
    policy = raw.get("version_policy")
    require(isinstance(policy, Mapping) and policy, "device_profile_rejected")
    family = str(raw["family"])
    if family == "android":
        require(set(policy) == {"api_min", "api_max"}, "device_profile_rejected")
        require(
            isinstance(policy["api_min"], int)
            and isinstance(policy["api_max"], int)
            and 1 <= policy["api_min"] <= policy["api_max"] <= 100,
            "device_profile_rejected",
        )
    else:
        require(set(policy) == {"os_min", "os_max"}, "device_profile_rejected")
        require(version_tuple(str(policy["os_min"])) <= version_tuple(str(policy["os_max"])), "device_profile_rejected")
    aliases = raw.get("aliases")
    require(isinstance(aliases, Mapping) and aliases, "device_profile_rejected")
    require(
        all(
            isinstance(alias, str)
            and ALIAS.fullmatch(alias) is not None
            and isinstance(alias_class, str)
            and ALIAS.fullmatch(alias_class) is not None
            for alias, alias_class in aliases.items()
        ),
        "device_profile_rejected",
    )
    require(raw.get("selection_policy") in {"unique", "identity-hash"}, "device_profile_rejected")
    expected_base = "mobile" if family == "android" else "apple"
    require(raw.get("base_runner_profile") == expected_base, "device_profile_rejected")
    require(raw.get("workspace_profile") in {"native", "apple"}, "device_profile_rejected")
    require(raw.get("command_profile") in contract["command_profiles"], "command_profile_rejected")
    require(
        isinstance(raw.get("timeout_minutes"), int)
        and 1 <= raw["timeout_minutes"] <= contract["hard_timeout_minutes"],
        "device_profile_rejected",
    )
    require(set(strings(raw.get("artifact_exception_ids"))) <= set(contract["artifact_exceptions"]), "artifact_exception_rejected")
    require(set(strings(raw.get("live_backend_profiles"))) <= set(contract["live_backend_profiles"]), "live_backend_rejected")
    require(isinstance(raw.get("synthetic_only"), bool), "device_profile_rejected")
    strings(raw.get("connection_states"), nonempty=True)
    strings(raw.get("health_states"), nonempty=True)

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
        == (1, "1.1.0", "StreamScapeTV", "validation.device", "CI / Physical device validation"),
        "invalid_input",
    )
    require(contract.get("planner_runner_profile") == "portable", "invalid_input")
    require(contract.get("execution_overlay_profile") == "physical-device", "invalid_input")
    require(contract.get("hard_timeout_minutes") == 240, "invalid_input")
    require(contract.get("artifact_policy") == "zero-default", "invalid_input")
    require(contract.get("request_id_regex") == REQUEST_ID.pattern, "request_identity_rejected")
    require(contract.get("run_id_regex") == RUN_ID.pattern, "request_identity_rejected")
    require(
        set(strings(contract.get("public_inputs"), nonempty=True))
        == {
            "admitted_sha",
            "command_profile",
            "device_alias",
            "device_capability",
            "device_family",
            "evidence_exception_id",
            "max_duration_minutes",
            "request_id",
            "script_path",
        },
        "invalid_input",
    )
    require(
        strings(contract.get("public_outputs"), nonempty=True)
        == ["result", "device_evidence_id", "artifact_exception_used", "request_id"],
        "invalid_input",
    )
    require(strings(contract.get("public_secrets"), nonempty=True) == ["live_test_credentials"], "invalid_input")
    require(set(strings(contract.get("families"), nonempty=True)) == {item.value for item in DeviceFamily}, "unsupported_family")
    require(REQUIRED_FORBIDDEN_INPUTS <= set(strings(contract.get("forbidden_inputs"), nonempty=True)), "forbidden_input")
    serialization = contract.get("serialization_contract")
    require(
        isinstance(serialization, Mapping)
        and serialization.get("backend") == "github-actions-job-concurrency"
        and serialization.get("cancel_in_progress") is False
        and serialization.get("caller_override") is False
        and serialization.get("fencing_token") is False
        and serialization.get("group_scope") == ["device_profile", "device_family", "alias_class"],
        "group_injection_rejected",
    )
    authorization = contract.get("owner_authorization")
    require(
        isinstance(authorization, Mapping)
        and authorization.get("mode") == "exact-family-current-chat"
        and authorization.get("authorized_families") == []
        and authorization.get("runner_or_secret_is_authorization") is False
        and authorization.get("failure_code") == "physical_authorization_required",
        "authorization_rejected",
    )
    lock = contract.get("lock_contract")
    require(
        isinstance(lock, Mapping)
        and lock.get("production_adapter") == "none-in-source-package"
        and lock.get("temporary_reference_adapter") == "in-memory-tests-only"
        and lock.get("cross_run_fencing_claimed") is False
        and lock.get("agent_state_transport_used") is False,
        "invalid_input",
    )
    _validate_command_profiles(contract)
    require(isinstance(contract.get("artifact_exceptions"), Mapping), "invalid_input")
    require(isinstance(contract.get("live_backend_profiles"), Mapping), "invalid_input")
    profiles = contract.get("profiles")
    require(isinstance(profiles, Mapping) and profiles, "device_profile_rejected")
    for profile_id, raw in profiles.items():
        require(isinstance(raw, Mapping), "device_profile_rejected")
        _validate_profile(str(profile_id), raw, contract)
    require(isinstance(contract.get("cleanup"), Mapping) and all(value is True for value in contract["cleanup"].values()), "cleanup_failed")
    return contract

def load_evidence_contract(root: Path) -> Mapping[str, Any]:
    contract = _read_json(root / EVIDENCE_CONTRACT_PATH, "evidence_policy_failed")
    require(
        (contract.get("schema_version"), contract.get("contract_version"), contract.get("packet_version"))
        == (1, "1.1.0", "device-evidence/1"),
        "evidence_policy_failed",
    )
    required = set(strings(contract.get("required_fields"), nonempty=True))
    allowed = set(strings(contract.get("allowed_fields"), nonempty=True))
    require(required == allowed, "evidence_policy_failed")
    require(set(contract.get("certification_scope_by_family", {})) == {item.value for item in DeviceFamily}, "evidence_policy_failed")
    strings(contract.get("required_limitations"), nonempty=True)
    return contract

def _command_profile(profile_id: str, raw: Mapping[str, Any]) -> DeviceCommandProfile:
    return DeviceCommandProfile(
        profile_id=profile_id,
        prepare_script=str(raw["prepare_script"]),
        test_script=str(raw["test_script"]),
        evidence_script=str(raw["evidence_script"]),
        cleanup_script=str(raw["cleanup_script"]),
        fixed_arguments=tuple(raw["fixed_arguments"]),
        live_backend_profile=raw["live_backend_profile"],
        state_restoration=tuple(raw["state_restoration"]),
    )

def _profile(profile_id: str, raw: Mapping[str, Any], contract: Mapping[str, Any]) -> DeviceProfile:
    command_id = str(raw["command_profile"])
    return DeviceProfile(
        profile_id=profile_id,
        repositories=tuple(raw["repositories"]),
        products=tuple(raw["products"]),
        family=DeviceFamily(raw["family"]),
        capabilities=tuple(raw["capabilities"]),
        models=tuple(raw["models"]),
        version_policy=dict(raw["version_policy"]),
        aliases=dict(raw["aliases"]),
        selection_policy=str(raw["selection_policy"]),
        base_runner_profile=str(raw["base_runner_profile"]),
        workspace_profile=str(raw["workspace_profile"]),
        command_profile=_command_profile(command_id, contract["command_profiles"][command_id]),
        timeout_minutes=int(raw["timeout_minutes"]),
        artifact_exception_ids=tuple(raw["artifact_exception_ids"]),
        live_backend_profiles=tuple(raw["live_backend_profiles"]),
        synthetic_only=bool(raw["synthetic_only"]),
        connection_states=tuple(raw["connection_states"]),
        health_states=tuple(raw["health_states"]),
    )

