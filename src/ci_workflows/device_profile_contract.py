"""Validation and typed loading for device profile/evidence contracts."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Mapping

from .device_contract_common import (
    ALIAS,
    CAPABILITY,
    EVIDENCE_CONTRACT_PATH,
    IDENTIFIER,
    PROFILE_CONTRACT_PATH,
    REPOSITORY,
    REQUEST_ID,
    RUN_ID,
    _read_json,
    require,
    safe_relative,
    strings,
    version_tuple,
)
from .device_types import DeviceCommandProfile, DeviceFamily, DeviceProfile

PROFILE_REQUIRED_KEYS = {
    "repositories",
    "products",
    "family",
    "capabilities",
    "models",
    "version_policy",
    "aliases",
    "selection_policy",
    "base_runner_profile",
    "workspace_profile",
    "command_profile",
    "timeout_minutes",
    "artifact_exception_ids",
    "live_backend_profiles",
    "synthetic_only",
    "connection_states",
    "health_states",
}
PROFILE_OPTIONAL_KEYS = {"request_issue_numbers"}
COMMAND_REQUIRED_KEYS = {
    "prepare_script",
    "test_script",
    "evidence_script",
    "cleanup_script",
    "fixed_arguments",
    "live_backend_profile",
    "state_restoration",
}
COMMAND_OPTIONAL_KEYS = {"retained_evidence_path", "retained_evidence_media_type"}
FRAGMENT_KEYS = {
    "schema_version",
    "contract_version",
    "retire_command_profiles",
    "retire_profiles",
    "command_profiles",
    "profiles",
}
PROFILE_FRAGMENT_ROOT = Path("contracts/device-profiles")
RETAINED_EVIDENCE_PREFIX = ".tmp/ci-retained/"
RETAINED_EVIDENCE_MEDIA_TYPES = {"application/json", "text/plain"}
REQUIRED_FORBIDDEN_INPUTS = {
    "arbitrary_command",
    "arguments",
    "callback",
    "command",
    "concurrency_group",
    "cancel_in_progress",
    "database_url",
    "deletion_path",
    "deployment",
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


def _profile_identifiers(value: object, code: str) -> tuple[str, ...]:
    values = strings(value, code=code)
    require(
        all(IDENTIFIER.fullmatch(item) is not None for item in values),
        code,
    )
    return tuple(values)


def _merge_profile_fragments(root: Path, contract: Mapping[str, Any]) -> Mapping[str, Any]:
    fragment_root = root / PROFILE_FRAGMENT_ROOT
    if not fragment_root.exists():
        return contract
    require(
        fragment_root.is_dir() and not fragment_root.is_symlink(),
        "device_profile_rejected",
    )

    merged = dict(contract)
    command_profiles = dict(contract.get("command_profiles", {}))
    profiles = dict(contract.get("profiles", {}))
    for path in sorted(fragment_root.glob("*.json")):
        require(path.is_file() and not path.is_symlink(), "device_profile_rejected")
        fragment = _read_json(path)
        require(
            set(fragment) == FRAGMENT_KEYS
            and fragment.get("schema_version") == 1
            and fragment.get("contract_version") == "1.0.0",
            "device_profile_rejected",
        )
        retire_commands = _profile_identifiers(
            fragment.get("retire_command_profiles"), "command_profile_rejected"
        )
        retire_profiles = _profile_identifiers(
            fragment.get("retire_profiles"), "device_profile_rejected"
        )
        for profile_id in retire_commands:
            require(profile_id in command_profiles, "command_profile_rejected")
            command_profiles.pop(profile_id)
        for profile_id in retire_profiles:
            require(profile_id in profiles, "device_profile_rejected")
            profiles.pop(profile_id)

        commands = fragment.get("command_profiles")
        additions = fragment.get("profiles")
        require(isinstance(commands, Mapping), "command_profile_rejected")
        require(isinstance(additions, Mapping), "device_profile_rejected")
        for profile_id, raw in commands.items():
            require(
                isinstance(profile_id, str)
                and profile_id not in command_profiles
                and isinstance(raw, Mapping),
                "command_profile_rejected",
            )
            command_profiles[profile_id] = dict(raw)
        for profile_id, raw in additions.items():
            require(
                isinstance(profile_id, str)
                and profile_id not in profiles
                and isinstance(raw, Mapping),
                "device_profile_rejected",
            )
            profiles[profile_id] = dict(raw)

    merged["command_profiles"] = command_profiles
    merged["profiles"] = profiles
    return merged


def _validate_command_profiles(contract: Mapping[str, Any]) -> None:
    profiles = contract.get("command_profiles")
    require(isinstance(profiles, Mapping) and profiles, "invalid_input")
    for profile_id, raw in profiles.items():
        keys = set(raw) if isinstance(raw, Mapping) else set()
        require(
            isinstance(profile_id, str)
            and IDENTIFIER.fullmatch(profile_id) is not None
            and isinstance(raw, Mapping)
            and COMMAND_REQUIRED_KEYS <= keys <= COMMAND_REQUIRED_KEYS | COMMAND_OPTIONAL_KEYS,
            "command_profile_rejected",
        )
        for field in (
            "prepare_script",
            "test_script",
            "evidence_script",
            "cleanup_script",
        ):
            safe_relative(raw.get(field), "command_profile_rejected")
        arguments = strings(
            raw.get("fixed_arguments"),
            unique=False,
            code="command_profile_rejected",
        )
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
            or (
                isinstance(backend, str)
                and IDENTIFIER.fullmatch(backend) is not None
            ),
            "live_backend_rejected",
        )
        strings(raw.get("state_restoration"), nonempty=True)

        retained_path = raw.get("retained_evidence_path")
        retained_media = raw.get("retained_evidence_media_type")
        if retained_path is None and retained_media is None:
            continue
        require(
            isinstance(retained_path, str)
            and isinstance(retained_media, str)
            and retained_media in RETAINED_EVIDENCE_MEDIA_TYPES,
            "evidence_policy_failed",
        )
        normalized = safe_relative(retained_path, "evidence_policy_failed")
        require(
            normalized == retained_path
            and retained_path.startswith(RETAINED_EVIDENCE_PREFIX)
            and len(retained_path) <= 240,
            "evidence_policy_failed",
        )


def _request_issue_numbers(raw: Mapping[str, Any]) -> tuple[int, ...]:
    value = raw.get("request_issue_numbers")
    if value is None:
        return ()
    require(
        isinstance(value, list)
        and value
        and all(type(item) is int and 1 <= item <= 2**31 - 1 for item in value)
        and value == sorted(set(value)),
        "request_identity_rejected",
    )
    return tuple(value)


def _validate_profile(
    profile_id: str,
    raw: Mapping[str, Any],
    contract: Mapping[str, Any],
) -> None:
    keys = set(raw)
    require(
        IDENTIFIER.fullmatch(profile_id) is not None
        and PROFILE_REQUIRED_KEYS <= keys <= PROFILE_REQUIRED_KEYS | PROFILE_OPTIONAL_KEYS,
        "device_profile_rejected",
    )
    repositories = strings(raw.get("repositories"), nonempty=True)
    require(
        all(REPOSITORY.fullmatch(value) is not None for value in repositories),
        "device_profile_rejected",
    )
    strings(raw.get("products"), nonempty=True)
    require(
        raw.get("family") in {item.value for item in DeviceFamily},
        "unsupported_family",
    )
    capabilities = strings(raw.get("capabilities"), nonempty=True)
    require(
        all(CAPABILITY.fullmatch(value) is not None for value in capabilities),
        "device_profile_rejected",
    )
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
        require(
            version_tuple(str(policy["os_min"]))
            <= version_tuple(str(policy["os_max"])),
            "device_profile_rejected",
        )
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
    require(
        raw.get("selection_policy") in {"unique", "identity-hash"},
        "device_profile_rejected",
    )
    expected_base = "mobile" if family == "android" else "apple"
    require(
        raw.get("base_runner_profile") == expected_base,
        "device_profile_rejected",
    )
    require(
        raw.get("workspace_profile") in {"native", "apple"},
        "device_profile_rejected",
    )
    require(
        raw.get("command_profile") in contract["command_profiles"],
        "command_profile_rejected",
    )
    require(
        isinstance(raw.get("timeout_minutes"), int)
        and 1 <= raw["timeout_minutes"] <= contract["hard_timeout_minutes"],
        "device_profile_rejected",
    )
    require(
        set(strings(raw.get("artifact_exception_ids")))
        <= set(contract["artifact_exceptions"]),
        "artifact_exception_rejected",
    )
    require(
        set(strings(raw.get("live_backend_profiles")))
        <= set(contract["live_backend_profiles"]),
        "live_backend_rejected",
    )
    require(
        isinstance(raw.get("synthetic_only"), bool),
        "device_profile_rejected",
    )
    strings(raw.get("connection_states"), nonempty=True)
    strings(raw.get("health_states"), nonempty=True)
    _request_issue_numbers(raw)


def load_device_contract(root: Path) -> Mapping[str, Any]:
    contract = _merge_profile_fragments(root, _read_json(root / PROFILE_CONTRACT_PATH))
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
            "1.1.0",
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
    require(
        contract.get("request_id_regex") == REQUEST_ID.pattern,
        "request_identity_rejected",
    )
    require(
        contract.get("run_id_regex") == RUN_ID.pattern,
        "request_identity_rejected",
    )
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
    require(
        strings(contract.get("public_secrets"), nonempty=True)
        == ["device_authorization_receipt", "live_test_credentials"],
        "invalid_input",
    )
    require(
        set(strings(contract.get("families"), nonempty=True))
        == {item.value for item in DeviceFamily},
        "unsupported_family",
    )
    require(
        REQUIRED_FORBIDDEN_INPUTS
        <= set(strings(contract.get("forbidden_inputs"), nonempty=True)),
        "forbidden_input",
    )
    serialization = contract.get("serialization_contract")
    require(
        isinstance(serialization, Mapping)
        and serialization.get("backend") == "github-actions-job-concurrency"
        and serialization.get("cancel_in_progress") is False
        and serialization.get("caller_override") is False
        and serialization.get("fencing_token") is True
        and serialization.get("group_scope")
        == ["device_profile", "device_family", "alias_class"],
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
        and lock.get("production_adapter")
        == "device-lock/1:posix-shared-root-v1"
        and lock.get("temporary_reference_adapter") == "in-memory-tests-only"
        and lock.get("cross_run_fencing_claimed") is True
        and lock.get("agent_state_transport_used") is False,
        "invalid_input",
    )
    _validate_command_profiles(contract)
    require(
        isinstance(contract.get("artifact_exceptions"), Mapping),
        "invalid_input",
    )
    require(
        isinstance(contract.get("live_backend_profiles"), Mapping),
        "invalid_input",
    )
    profiles = contract.get("profiles")
    require(
        isinstance(profiles, Mapping) and profiles,
        "device_profile_rejected",
    )
    for profile_id, raw in profiles.items():
        require(isinstance(raw, Mapping), "device_profile_rejected")
        _validate_profile(str(profile_id), raw, contract)
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
        )
        == (1, "1.1.0", "device-evidence/1"),
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
    assertions = strings(
        contract.get("allowed_assertions"),
        nonempty=True,
        code="evidence_policy_failed",
    )
    require(
        assertions == sorted(assertions)
        and len(assertions) <= int(contract["maximum_assertions"]),
        "evidence_policy_failed",
    )
    return contract


def _command_profile(
    profile_id: str,
    raw: Mapping[str, Any],
) -> DeviceCommandProfile:
    return DeviceCommandProfile(
        profile_id=profile_id,
        prepare_script=str(raw["prepare_script"]),
        test_script=str(raw["test_script"]),
        evidence_script=str(raw["evidence_script"]),
        cleanup_script=str(raw["cleanup_script"]),
        fixed_arguments=tuple(raw["fixed_arguments"]),
        live_backend_profile=raw["live_backend_profile"],
        state_restoration=tuple(raw["state_restoration"]),
        retained_evidence_path=(
            str(raw["retained_evidence_path"])
            if raw.get("retained_evidence_path") is not None
            else None
        ),
        retained_evidence_media_type=(
            str(raw["retained_evidence_media_type"])
            if raw.get("retained_evidence_media_type") is not None
            else None
        ),
    )


def _profile(
    profile_id: str,
    raw: Mapping[str, Any],
    contract: Mapping[str, Any],
) -> DeviceProfile:
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
        command_profile=_command_profile(
            command_id,
            contract["command_profiles"][command_id],
        ),
        timeout_minutes=int(raw["timeout_minutes"]),
        artifact_exception_ids=tuple(raw["artifact_exception_ids"]),
        live_backend_profiles=tuple(raw["live_backend_profiles"]),
        synthetic_only=bool(raw["synthetic_only"]),
        connection_states=tuple(raw["connection_states"]),
        health_states=tuple(raw["health_states"]),
        request_issue_numbers=_request_issue_numbers(raw),
    )