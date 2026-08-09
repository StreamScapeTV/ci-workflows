"""Checked-in physical-device contract, trust, and plan resolution."""
from __future__ import annotations

import json
import re
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence

from .device_types import (
    DeviceCommandProfile,
    DeviceFamily,
    DevicePlan,
    DeviceProfile,
    DeviceRequest,
    DeviceValidationError,
    SerialPolicy,
)

PROFILE_CONTRACT_PATH = Path("contracts/device-profiles.json")
EVIDENCE_CONTRACT_PATH = Path("contracts/device-evidence.json")
_FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
_REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_IDENTIFIER = re.compile(r"^[a-z][a-z0-9.-]{2,63}$")
_CAPABILITY = re.compile(r"^[a-z][a-z0-9-]{2,63}$")
_RAW_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{2,127}$")
_REQUEST_ID = re.compile(r"^issue-([1-9][0-9]*)-([a-z0-9][a-z0-9-]{7,63})$")
_RUN_ID = re.compile(r"^[1-9][0-9]{0,19}:[1-9][0-9]{0,3}$")
_SCRIPT_SUFFIXES = (".sh", ".py")
_ALLOWED_EVENTS = {"workflow_call", "workflow_dispatch"}
_ALLOWED_TRUST = {"trusted-exact"}
_SYNTHETIC_TRUST = "trusted-pr"
_PROFILE_KEYS = {
    "repositories",
    "products",
    "family",
    "capabilities",
    "models",
    "version_policy",
    "serial_policy",
    "selection_policy",
    "base_runner_profile",
    "workspace_profile",
    "command_profile",
    "timeout_minutes",
    "artifact_exception_ids",
    "live_backend_profiles",
    "execution_allowed",
    "connection_states",
    "health_states",
}
_COMMAND_KEYS = {
    "prepare_script",
    "test_script",
    "evidence_script",
    "cleanup_script",
    "fixed_arguments",
    "live_backend_profile",
    "state_restoration",
}
_PUBLIC_INPUTS = {
    "admitted_sha",
    "artifact_exception_id",
    "command_profile",
    "device_capability",
    "device_family",
    "device_identifier",
    "evidence_exception_id",
    "max_duration_minutes",
    "request_id",
    "script_path",
}
_REQUIRED_FORBIDDEN_INPUTS = {
    "arbitrary_command",
    "arguments",
    "callback",
    "command",
    "database_url",
    "deletion_path",
    "deployment",
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
    "registry",
    "release",
    "runner",
    "runner_labels",
    "runs_on",
    "secret_name",
    "shell",
    "signing_identity",
    "store",
    "testflight",
    "workspace_root",
}


def fail(code: str) -> None:
    raise DeviceValidationError(code)


def require(condition: bool, code: str) -> None:
    if not condition:
        fail(code)


def _read_json(path: Path, code: str = "invalid_input") -> Mapping[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise DeviceValidationError(code) from error
    require(isinstance(value, Mapping), code)
    return value


def strings(
    value: Any,
    *,
    nonempty: bool = False,
    unique: bool = True,
    code: str = "invalid_input",
) -> list[str]:
    require(isinstance(value, list), code)
    require(not nonempty or bool(value), code)
    require(all(isinstance(item, str) and item for item in value), code)
    require(not unique or len(value) == len(set(value)), code)
    return list(value)


def safe_relative(
    value: Any,
    code: str = "invalid_input",
    *,
    allow_dot: bool = False,
) -> str:
    require(isinstance(value, str), code)
    candidate = value.strip()
    if allow_dot and candidate == ".":
        return "."
    path = PurePosixPath(candidate)
    require(
        bool(candidate)
        and not path.is_absolute()
        and "\\" not in candidate
        and ".." not in path.parts
        and all(part not in {"", "."} for part in path.parts),
        code,
    )
    require(candidate.endswith(_SCRIPT_SUFFIXES), code)
    return path.as_posix()


def bounded_path(root: Path, relative: str) -> Path:
    boundary = root.resolve()
    require(boundary.is_dir() and not root.is_symlink(), "invalid_input")
    if relative == ".":
        return boundary
    lexical = boundary.joinpath(*PurePosixPath(relative).parts)
    current = boundary
    for part in PurePosixPath(relative).parts:
        current /= part
        require(not current.is_symlink(), "invalid_input")
        if not current.exists():
            break
    resolved = lexical.resolve(strict=False)
    require(boundary in resolved.parents, "invalid_input")
    return resolved


def version_tuple(value: str) -> tuple[int, ...]:
    require(
        isinstance(value, str)
        and re.fullmatch(r"(?:0|[1-9][0-9]*)(?:\.(?:0|[1-9][0-9]*)){1,2}", value)
        is not None,
        "device_inventory_malformed",
    )
    return tuple(int(part) for part in value.split("."))


def parse_request_id(value: str) -> int:
    match = _REQUEST_ID.fullmatch(value)
    require(match is not None, "request_identity_rejected")
    return int(match.group(1))


def source_trust_from_environment(environment: Mapping[str, str]) -> str:
    event_name = environment.get("GITHUB_EVENT_NAME", "")
    synthetic = environment.get("CIW_DEVICE_SYNTHETIC_MODE", "") == "true"
    if synthetic:
        require(
            event_name == "pull_request"
            and environment.get("GITHUB_REPOSITORY") == "StreamScapeTV/ci-workflows",
            "authorization_rejected",
        )
        return _SYNTHETIC_TRUST
    require(event_name in _ALLOWED_EVENTS, "authorization_rejected")
    if event_name == "workflow_dispatch":
        return "trusted-exact"
    source = environment.get("CIW_DEVICE_SOURCE_TRUST", "")
    require(source == "trusted-exact", "source_trust_rejected")
    return source


def optional_input(environment: Mapping[str, str], key: str) -> str | None:
    value = environment.get(key, "").strip()
    return value or None


def _positive_int(value: str, code: str) -> int:
    require(re.fullmatch(r"[1-9][0-9]{0,3}", value) is not None, code)
    result = int(value)
    require(1 <= result <= 240, code)
    return result


def request_from_environment(
    environment: Mapping[str, str],
    contract: Mapping[str, Any],
) -> DeviceRequest:
    forbidden = set(strings(contract.get("forbidden_inputs"), nonempty=True))
    for key, value in environment.items():
        if not key.startswith("INPUT_") or not value:
            continue
        logical = key.removeprefix("INPUT_").lower()
        if logical in forbidden:
            fail("forbidden_input")

    repository = environment.get("GITHUB_REPOSITORY", "").strip()
    admitted_sha = environment.get("INPUT_ADMITTED_SHA", "").strip()
    family_raw = environment.get("INPUT_DEVICE_FAMILY", "").strip()
    capability = environment.get("INPUT_DEVICE_CAPABILITY", "").strip()
    command_profile = environment.get("INPUT_COMMAND_PROFILE", "").strip()
    script_path = environment.get("INPUT_SCRIPT_PATH", "").strip()
    request_id = environment.get("INPUT_REQUEST_ID", "").strip()
    event_name = environment.get("GITHUB_EVENT_NAME", "").strip()
    run_id = (
        f"{environment.get('GITHUB_RUN_ID', '').strip()}:"
        f"{environment.get('GITHUB_RUN_ATTEMPT', '').strip()}"
    )

    require(_REPOSITORY.fullmatch(repository) is not None, "invalid_input")
    require(_FULL_SHA.fullmatch(admitted_sha) is not None, "source_mismatch")
    try:
        family = DeviceFamily(family_raw)
    except ValueError as error:
        raise DeviceValidationError("unsupported_family") from error
    require(_CAPABILITY.fullmatch(capability) is not None, "device_profile_rejected")
    require(_IDENTIFIER.fullmatch(command_profile) is not None, "command_profile_rejected")
    script_path = safe_relative(script_path, "command_profile_rejected")
    issue_number = parse_request_id(request_id)
    require(_RUN_ID.fullmatch(run_id) is not None, "request_identity_rejected")

    synthetic = environment.get("CIW_DEVICE_SYNTHETIC_MODE", "") == "true"
    source_trust = environment.get("INPUT_SOURCE_TRUST", "").strip()
    if not source_trust:
        source_trust = source_trust_from_environment(environment)
    if synthetic:
        require(
            source_trust == _SYNTHETIC_TRUST
            and event_name == "pull_request"
            and repository == "StreamScapeTV/ci-workflows",
            "source_trust_rejected",
        )
    else:
        require(source_trust in _ALLOWED_TRUST, "source_trust_rejected")
        require(event_name in _ALLOWED_EVENTS, "authorization_rejected")

    duration_raw = environment.get("INPUT_MAX_DURATION_MINUTES", "60").strip()
    duration = _positive_int(duration_raw, "invalid_input")
    device_identifier = optional_input(environment, "INPUT_DEVICE_IDENTIFIER")
    if device_identifier is not None:
        require(
            _RAW_IDENTIFIER.fullmatch(device_identifier) is not None,
            "device_identifier_rejected",
        )
        require(
            "simulator" not in device_identifier.casefold()
            and "emulator" not in device_identifier.casefold(),
            "device_identifier_rejected",
        )
    exception_id = optional_input(environment, "INPUT_EVIDENCE_EXCEPTION_ID")
    live_backend_secret_present = (
        environment.get("CIW_DEVICE_LIVE_BACKEND_PRESENT", "").strip() == "true"
    )
    return DeviceRequest(
        repository=repository,
        admitted_sha=admitted_sha,
        family=family,
        capability=capability,
        device_identifier=device_identifier,
        command_profile=command_profile,
        script_path=script_path,
        max_duration_minutes=duration,
        evidence_exception_id=exception_id,
        request_id=request_id,
        issue_number=issue_number,
        source_trust=source_trust,
        event_name=event_name,
        run_id=run_id,
        live_backend_secret_present=live_backend_secret_present,
    )


def _validate_command_profiles(contract: Mapping[str, Any]) -> None:
    profiles = contract.get("command_profiles")
    require(isinstance(profiles, Mapping) and profiles, "invalid_input")
    for profile_id, raw in profiles.items():
        require(
            isinstance(profile_id, str)
            and _IDENTIFIER.fullmatch(profile_id) is not None
            and isinstance(raw, Mapping)
            and set(raw) == _COMMAND_KEYS,
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
                and not re.search(r"[;&|`$<>]", item)
                for item in arguments
            ),
            "command_profile_rejected",
        )
        backend = raw.get("live_backend_profile")
        require(
            backend is None
            or (
                isinstance(backend, str)
                and _IDENTIFIER.fullmatch(backend) is not None
            ),
            "live_backend_rejected",
        )
        strings(
            raw.get("state_restoration"),
            nonempty=True,
            code="invalid_input",
        )


def _validate_profile(
    profile_id: str,
    raw: Mapping[str, Any],
    contract: Mapping[str, Any],
) -> None:
    require(
        _IDENTIFIER.fullmatch(profile_id) is not None
        and set(raw) == _PROFILE_KEYS,
        "device_profile_rejected",
    )
    repositories = strings(raw.get("repositories"), nonempty=True)
    require(
        all(_REPOSITORY.fullmatch(value) is not None for value in repositories),
        "device_profile_rejected",
    )
    strings(raw.get("products"), nonempty=True)
    require(raw.get("family") in {item.value for item in DeviceFamily}, "unsupported_family")
    capabilities = strings(raw.get("capabilities"), nonempty=True)
    require(
        all(_CAPABILITY.fullmatch(value) is not None for value in capabilities),
        "device_profile_rejected",
    )
    strings(raw.get("models"), nonempty=True)
    policy = raw.get("version_policy")
    require(isinstance(policy, Mapping) and policy, "device_profile_rejected")
    family = raw["family"]
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
    require(
        raw.get("serial_policy") in {item.value for item in SerialPolicy},
        "device_profile_rejected",
    )
    require(
        raw.get("selection_policy") in {"unique", "identity-hash"},
        "device_profile_rejected",
    )
    expected_base = "mobile" if family == "android" else "apple"
    require(raw.get("base_runner_profile") == expected_base, "device_profile_rejected")
    require(raw.get("workspace_profile") in {"native", "apple"}, "device_profile_rejected")
    command_id = raw.get("command_profile")
    require(command_id in contract["command_profiles"], "command_profile_rejected")
    require(
        isinstance(raw.get("timeout_minutes"), int)
        and 1 <= raw["timeout_minutes"] <= contract["hard_timeout_minutes"],
        "device_profile_rejected",
    )
    exception_ids = strings(raw.get("artifact_exception_ids"))
    require(
        set(exception_ids) <= set(contract["artifact_exceptions"]),
        "artifact_exception_rejected",
    )
    backend_ids = strings(raw.get("live_backend_profiles"))
    require(
        set(backend_ids) <= set(contract["live_backend_profiles"]),
        "live_backend_rejected",
    )
    require(isinstance(raw.get("execution_allowed"), bool), "device_profile_rejected")
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
        == (
            1,
            "1.0.0",
            "StreamScapeTV",
            "validation.device",
            "CI / Physical device validation",
        ),
        "invalid_input",
    )
    require(
        contract.get("planner_runner_profile") == "portable"
        and contract.get("execution_overlay_profile") == "physical-device",
        "invalid_input",
    )
    require(
        contract.get("hard_timeout_minutes") == 240
        and contract.get("artifact_policy") == "zero-default",
        "invalid_input",
    )
    require(
        set(strings(contract.get("allowed_events"), nonempty=True)) == _ALLOWED_EVENTS
        and set(strings(contract.get("allowed_source_trust"), nonempty=True))
        == _ALLOWED_TRUST
        and contract.get("synthetic_allowed_event") == "pull_request"
        and contract.get("synthetic_allowed_source_trust") == _SYNTHETIC_TRUST,
        "authorization_rejected",
    )
    require(
        contract.get("request_id_regex") == _REQUEST_ID.pattern
        and contract.get("run_id_regex") == _RUN_ID.pattern,
        "request_identity_rejected",
    )
    require(
        set(strings(contract.get("public_inputs"), nonempty=True))
        == {
            "admitted_sha",
            "command_profile",
            "device_capability",
            "device_family",
            "device_identifier",
            "evidence_exception_id",
            "max_duration_minutes",
            "request_id",
            "script_path",
        },
        "invalid_input",
    )
    require(
        strings(contract.get("public_outputs"), nonempty=True)
        == [
            "result",
            "device_evidence_id",
            "artifact_exception_used",
            "request_id",
        ]
        and strings(contract.get("public_secrets"), nonempty=True)
        == ["live_test_credentials"],
        "invalid_input",
    )
    require(
        set(strings(contract.get("families"), nonempty=True))
        == {item.value for item in DeviceFamily},
        "unsupported_family",
    )
    require(
        _REQUIRED_FORBIDDEN_INPUTS
        <= set(strings(contract.get("forbidden_inputs"), nonempty=True)),
        "forbidden_input",
    )
    _validate_command_profiles(contract)
    require(
        isinstance(contract.get("artifact_exceptions"), Mapping)
        and isinstance(contract.get("live_backend_profiles"), Mapping),
        "invalid_input",
    )
    profiles = contract.get("profiles")
    require(isinstance(profiles, Mapping) and profiles, "device_profile_rejected")
    for profile_id, raw in profiles.items():
        require(isinstance(raw, Mapping), "device_profile_rejected")
        _validate_profile(str(profile_id), raw, contract)
    require(
        isinstance(contract.get("failure_codes"), list)
        and len(contract["failure_codes"]) == len(set(contract["failure_codes"])),
        "invalid_input",
    )
    require(
        isinstance(contract.get("cleanup"), Mapping)
        and contract["cleanup"]
        and all(value is True for value in contract["cleanup"].values()),
        "cleanup_failed",
    )
    lock = contract.get("lock_contract")
    require(
        isinstance(lock, Mapping)
        and lock.get("backend") == "canonical-resource-rpc-required"
        and lock.get("temporary_reference_adapter") == "in-memory-tests-only"
        and lock.get("ordinary_provisional_rpc_forbidden") is True
        and lock.get("legacy_agent_state_forbidden") is True,
        "lock_backend_unavailable",
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
        == (1, "1.0.0", "device-evidence/1"),
        "evidence_policy_failed",
    )
    required = set(strings(contract.get("required_fields"), nonempty=True))
    allowed = set(strings(contract.get("allowed_fields"), nonempty=True))
    require(required <= allowed, "evidence_policy_failed")
    require(
        set(contract.get("certification_scope_by_family", {}))
        == {item.value for item in DeviceFamily},
        "evidence_policy_failed",
    )
    strings(contract.get("required_limitations"), nonempty=True)
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
        serial_policy=SerialPolicy(raw["serial_policy"]),
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
        execution_allowed=bool(raw["execution_allowed"]),
        connection_states=tuple(raw["connection_states"]),
        health_states=tuple(raw["health_states"]),
    )


def build_plan(
    contract: Mapping[str, Any],
    request: DeviceRequest,
) -> DevicePlan:
    synthetic_request = (
        request.repository == "StreamScapeTV/ci-workflows"
        and request.capability.startswith("synthetic-")
    )
    if synthetic_request:
        require(
            request.source_trust == _SYNTHETIC_TRUST
            and request.event_name == "pull_request",
            "source_trust_rejected",
        )
    else:
        require(request.source_trust == "trusted-exact", "source_trust_rejected")
        require(request.event_name in _ALLOWED_EVENTS, "authorization_rejected")
    matches: list[DeviceProfile] = []
    for profile_id, raw in contract["profiles"].items():
        profile = _profile(str(profile_id), raw, contract)
        if (
            request.repository in profile.repositories
            and request.family is profile.family
            and request.capability in profile.capabilities
            and request.command_profile == profile.command_profile.profile_id
            and request.script_path == profile.command_profile.test_script
        ):
            matches.append(profile)
    require(bool(matches), "device_profile_rejected")
    require(len(matches) == 1, "device_profile_rejected")
    profile = matches[0]
    require(
        request.max_duration_minutes <= profile.timeout_minutes,
        "authorization_rejected",
    )
    if profile.serial_policy is SerialPolicy.FORBIDDEN:
        require(request.device_identifier is None, "device_identifier_rejected")
    elif profile.serial_policy is SerialPolicy.EXACT_CALLER:
        require(request.device_identifier is not None, "device_identifier_rejected")
    else:
        require(request.device_identifier is None, "device_identifier_rejected")
    if request.evidence_exception_id is not None:
        require(
            request.evidence_exception_id in profile.artifact_exception_ids,
            "artifact_exception_rejected",
        )
    backend = profile.command_profile.live_backend_profile
    if backend is not None:
        require(
            backend in profile.live_backend_profiles,
            "live_backend_rejected",
        )
        require(
            request.live_backend_secret_present,
            "live_backend_rejected",
        )
        backend_contract = contract["live_backend_profiles"][backend]
        require(
            request.repository in backend_contract["allowed_repositories"]
            and backend_contract["production_forbidden"] is True,
            "live_backend_rejected",
        )
    else:
        require(
            not request.live_backend_secret_present,
            "live_backend_rejected",
        )

    # Initial #14 source intentionally cannot authorize real execution. The
    # canonical resource-fencing RPC or a separately owner-approved temporary
    # adapter must be integrated after #13 before this value can become true.
    execution_authorized = False
    return DevicePlan(
        request=request,
        profile=profile,
        execution_authorized=execution_authorized,
        lock_backend=str(contract["lock_contract"]["backend"]),
        planner_runner_profile=str(contract["planner_runner_profile"]),
        execution_overlay_profile=str(contract["execution_overlay_profile"]),
    )
