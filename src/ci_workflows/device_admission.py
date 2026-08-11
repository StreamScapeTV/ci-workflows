"""GitHub-derived device source admission and request parsing."""
from __future__ import annotations

import re
from typing import Any, Mapping

from .device_contract_common import (
    ALIAS, CAPABILITY, FULL_SHA, IDENTIFIER, REPOSITORY, RUN_ID,
    fail, parse_request_id, require, safe_relative, strings,
)
from .device_types import DeviceFamily, DeviceRequest, DeviceValidationError

def _boolean_text(value: str, code: str) -> bool:
    require(value in {"true", "false"}, code)
    return value == "true"

def source_trust_from_environment(
    environment: Mapping[str, str], admitted_sha: str
) -> str:
    """Derive trust only from fixed GitHub event metadata, never from inputs."""

    repository = environment.get("GITHUB_REPOSITORY", "").strip()
    event_repository = environment.get(
        "CIW_DEVICE_EVENT_REPOSITORY", repository
    ).strip()
    event_name = environment.get("GITHUB_EVENT_NAME", "").strip()
    event_sha = environment.get("CIW_DEVICE_EVENT_SHA", "").strip()
    head_repository = environment.get(
        "CIW_DEVICE_HEAD_REPOSITORY", event_repository
    ).strip()
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
            event_name == "pull_request"
            and repository == "StreamScapeTV/ci-workflows",
            "source_admission_rejected",
        )
        return "trusted-pr"

    require(event_name in {"workflow_call", "workflow_dispatch"}, "authorization_rejected")
    return "trusted-exact"

def optional_input(environment: Mapping[str, str], key: str) -> str | None:
    value = environment.get(key, "").strip()
    return value or None

def _positive_int(value: str, code: str) -> int:
    require(re.fullmatch(r"[1-9][0-9]{0,3}", value) is not None, code)
    result = int(value)
    require(1 <= result <= 240, code)
    return result

def request_from_environment(
    environment: Mapping[str, str], contract: Mapping[str, Any]
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
    device_alias = environment.get("INPUT_DEVICE_ALIAS", "").strip()
    command_profile = environment.get("INPUT_COMMAND_PROFILE", "").strip()
    script_path = environment.get("INPUT_SCRIPT_PATH", "").strip()
    request_id = environment.get("INPUT_REQUEST_ID", "").strip()
    event_name = environment.get("GITHUB_EVENT_NAME", "").strip()
    run_id = (
        f"{environment.get('GITHUB_RUN_ID', '').strip()}:"
        f"{environment.get('GITHUB_RUN_ATTEMPT', '').strip()}"
    )

    require(REPOSITORY.fullmatch(repository) is not None, "invalid_input")
    require(FULL_SHA.fullmatch(admitted_sha) is not None, "source_mismatch")
    try:
        family = DeviceFamily(family_raw)
    except ValueError as error:
        raise DeviceValidationError("unsupported_family") from error
    require(CAPABILITY.fullmatch(capability) is not None, "device_profile_rejected")
    require(ALIAS.fullmatch(device_alias) is not None, "device_profile_rejected")
    require(IDENTIFIER.fullmatch(command_profile) is not None, "command_profile_rejected")
    script_path = safe_relative(script_path, "command_profile_rejected")
    issue_number = parse_request_id(request_id)
    require(RUN_ID.fullmatch(run_id) is not None, "request_identity_rejected")
    source_trust = source_trust_from_environment(environment, admitted_sha)

    duration = _positive_int(
        environment.get("INPUT_MAX_DURATION_MINUTES", "60").strip(),
        "invalid_input",
    )
    exception_id = optional_input(environment, "INPUT_EVIDENCE_EXCEPTION_ID")
    secret_present = (
        environment.get("CIW_DEVICE_LIVE_BACKEND_PRESENT", "").strip() == "true"
    )
    return DeviceRequest(
        repository=repository,
        admitted_sha=admitted_sha,
        family=family,
        capability=capability,
        device_alias=device_alias,
        command_profile=command_profile,
        script_path=script_path,
        max_duration_minutes=duration,
        evidence_exception_id=exception_id,
        request_id=request_id,
        issue_number=issue_number,
        source_trust=source_trust,
        event_name=event_name,
        run_id=run_id,
        live_backend_secret_present=secret_present,
    )
