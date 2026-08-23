"""GitHub-derived device source admission and generic request parsing."""
from __future__ import annotations

import json
import re
from typing import Any, Mapping

from .device_contract_common import (
    CAPABILITY,
    FULL_SHA,
    REPOSITORY,
    RUN_ID,
    fail,
    parse_request_id,
    require,
    safe_relative,
    strings,
)
from .device_types import DeviceFamily, DeviceRequest, DeviceValidationError

_ENV_NAME = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
_HOST_CAPACITY = re.compile(r"^[a-z][a-z0-9-]{2,31}$")
_RESERVED_ENVIRONMENT = {
    "ANDROID_HOME", "ANDROID_SDK_ROOT", "ANDROID_SERIAL", "CI", "GITHUB_ACTIONS",
    "HOME", "JAVA_HOME", "PATH", "RUNNER_TEMP", "RUNNER_TOOL_CACHE", "TMP", "TMPDIR",
}
_RESERVED_PREFIXES = ("CIW_", "GITHUB_", "RUNNER_")
_SECRET_MARKERS = ("CREDENTIAL", "PASSWORD", "PRIVATE_KEY", "SECRET", "SESSION", "TOKEN")
_SYNTHETIC_EVENTS = {"pull_request", "push", "workflow_dispatch"}
_TRUSTED_EXACT_EVENTS = {"pull_request", "workflow_call", "workflow_dispatch"}


def _boolean_text(value: str, code: str) -> bool:
    require(value in {"true", "false"}, code)
    return value == "true"


def source_trust_from_environment(environment: Mapping[str, str], admitted_sha: str) -> str:
    """Derive trust only from fixed GitHub event metadata, never from inputs.

    GitHub preserves the original caller event inside a reusable workflow. A
    same-repository non-fork PR therefore arrives at ``reusable-device.yml`` as
    ``pull_request`` rather than ``workflow_call``. Exact head/repository/fork
    checks remain the trust boundary; the event name is not rewritten.
    """

    repository = environment.get("GITHUB_REPOSITORY", "").strip()
    event_repository = environment.get("CIW_DEVICE_EVENT_REPOSITORY", repository).strip()
    event_name = environment.get("GITHUB_EVENT_NAME", "").strip()
    event_sha = environment.get("CIW_DEVICE_EVENT_SHA", "").strip()
    head_repository = environment.get("CIW_DEVICE_HEAD_REPOSITORY", event_repository).strip()
    head_fork_raw = environment.get("CIW_DEVICE_HEAD_FORK", "false").strip()
    head_fork = _boolean_text(head_fork_raw, "source_admission_rejected")
    synthetic = environment.get("CIW_DEVICE_SYNTHETIC_MODE", "") == "true"

    require(REPOSITORY.fullmatch(repository) is not None, "source_admission_rejected")
    require(event_repository == repository, "source_admission_rejected")
    require(head_repository == repository and not head_fork, "source_admission_rejected")
    require(FULL_SHA.fullmatch(event_sha) is not None, "source_admission_rejected")
    require(event_sha == admitted_sha, "source_mismatch")
    if synthetic:
        require(
            repository == "StreamScapeTV/ci-workflows" and event_name in _SYNTHETIC_EVENTS,
            "source_admission_rejected",
        )
        return "trusted-pr"
    require(event_name in _TRUSTED_EXACT_EVENTS, "source_admission_rejected")
    return "trusted-exact"


def optional_input(environment: Mapping[str, str], key: str) -> str | None:
    value = environment.get(key, "").strip()
    return value or None


def _positive_int(value: str, code: str) -> int:
    require(re.fullmatch(r"[1-9][0-9]{0,3}", value) is not None, code)
    result = int(value)
    require(1 <= result <= 240, code)
    return result


def _arguments(raw: str) -> tuple[str, ...]:
    try:
        value = json.loads(raw or "[]")
    except json.JSONDecodeError as error:
        raise DeviceValidationError("command_profile_rejected") from error
    require(
        isinstance(value, list)
        and len(value) <= 16
        and all(
            isinstance(item, str)
            and 0 < len(item.encode("utf-8")) <= 128
            and "\x00" not in item
            and "\n" not in item
            and "\r" not in item
            for item in value
        ),
        "command_profile_rejected",
    )
    return tuple(value)


def _caller_environment(raw: str) -> Mapping[str, str]:
    try:
        value = json.loads(raw or "{}")
    except json.JSONDecodeError as error:
        raise DeviceValidationError("command_profile_rejected") from error
    require(isinstance(value, Mapping) and len(value) <= 16, "command_profile_rejected")
    result: dict[str, str] = {}
    for key, item in value.items():
        require(
            isinstance(key, str)
            and _ENV_NAME.fullmatch(key) is not None
            and key not in _RESERVED_ENVIRONMENT
            and not key.startswith(_RESERVED_PREFIXES)
            and not any(marker in key for marker in _SECRET_MARKERS)
            and isinstance(item, str)
            and len(item.encode("utf-8")) <= 512
            and "\x00" not in item
            and "\n" not in item
            and "\r" not in item,
            "command_profile_rejected",
        )
        result[key] = item
    return dict(sorted(result.items()))


def request_from_environment(environment: Mapping[str, str], contract: Mapping[str, Any]) -> DeviceRequest:
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
    host_capacity = environment.get("INPUT_HOST_CAPACITY", "").strip()
    request_id = environment.get("INPUT_REQUEST_ID", "").strip()
    event_name = environment.get("GITHUB_EVENT_NAME", "").strip()
    run_id = f"{environment.get('GITHUB_RUN_ID', '').strip()}:{environment.get('GITHUB_RUN_ATTEMPT', '').strip()}"

    require(REPOSITORY.fullmatch(repository) is not None, "invalid_input")
    require(FULL_SHA.fullmatch(admitted_sha) is not None, "source_mismatch")
    try:
        family = DeviceFamily(family_raw)
    except ValueError as error:
        raise DeviceValidationError("unsupported_family") from error
    require(CAPABILITY.fullmatch(capability) is not None, "device_profile_rejected")
    require(_HOST_CAPACITY.fullmatch(host_capacity) is not None, "device_profile_rejected")
    prepare_script = safe_relative(environment.get("INPUT_PREPARE_SCRIPT_PATH", "").strip(), "command_profile_rejected")
    test_script = safe_relative(environment.get("INPUT_TEST_SCRIPT_PATH", "").strip(), "command_profile_rejected")
    evidence_script = safe_relative(environment.get("INPUT_EVIDENCE_SCRIPT_PATH", "").strip(), "command_profile_rejected")
    cleanup_script = safe_relative(environment.get("INPUT_CLEANUP_SCRIPT_PATH", "").strip(), "command_profile_rejected")
    arguments = _arguments(environment.get("INPUT_ARGUMENTS_JSON", "[]").strip())
    caller_environment = _caller_environment(environment.get("INPUT_ENVIRONMENT_JSON", "{}").strip())
    issue_number = parse_request_id(request_id)
    require(RUN_ID.fullmatch(run_id) is not None, "request_identity_rejected")
    source_trust = source_trust_from_environment(environment, admitted_sha)
    duration = _positive_int(environment.get("INPUT_MAX_DURATION_MINUTES", "60").strip(), "invalid_input")
    exception_id = optional_input(environment, "INPUT_EVIDENCE_EXCEPTION_ID")
    authorization_raw = environment.get("CIW_DEVICE_AUTHORIZATION_PRESENT", "false").strip()
    authorization_present = _boolean_text(authorization_raw, "authorization_rejected")
    return DeviceRequest(
        repository=repository,
        admitted_sha=admitted_sha,
        family=family,
        capability=capability,
        host_capacity=host_capacity,
        prepare_script_path=prepare_script,
        test_script_path=test_script,
        evidence_script_path=evidence_script,
        cleanup_script_path=cleanup_script,
        arguments=arguments,
        environment=caller_environment,
        max_duration_minutes=duration,
        evidence_exception_id=exception_id,
        request_id=request_id,
        issue_number=issue_number,
        source_trust=source_trust,
        event_name=event_name,
        run_id=run_id,
        authorization_receipt_present=authorization_present,
    )
