"""Typed models and stable failures for bounded physical-device validation."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from enum import Enum
from typing import Mapping, Sequence

_SAFE_CODE = re.compile(r"^[a-z][a-z0-9_]{2,95}$")


class DeviceValidationError(RuntimeError):
    """Fail closed with one stable, non-sensitive public error code."""

    def __init__(self, code: str) -> None:
        if _SAFE_CODE.fullmatch(code) is None:
            raise ValueError("device validation error code must be safe")
        self.code = code
        super().__init__(code)


class DeviceFamily(str, Enum):
    ANDROID = "android"
    IOS = "ios"
    TVOS = "tvos"


@dataclass(frozen=True, slots=True)
class DeviceRequest:
    repository: str
    admitted_sha: str
    family: DeviceFamily
    capability: str
    device_alias: str
    command_profile: str
    script_path: str
    max_duration_minutes: int
    evidence_exception_id: str | None
    request_id: str
    issue_number: int
    source_trust: str
    event_name: str
    run_id: str
    live_backend_secret_present: bool = False


@dataclass(frozen=True, slots=True)
class DeviceCommandProfile:
    profile_id: str
    prepare_script: str
    test_script: str
    evidence_script: str
    cleanup_script: str
    fixed_arguments: tuple[str, ...]
    live_backend_profile: str | None
    state_restoration: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DeviceProfile:
    profile_id: str
    repositories: tuple[str, ...]
    products: tuple[str, ...]
    family: DeviceFamily
    capabilities: tuple[str, ...]
    models: tuple[str, ...]
    version_policy: Mapping[str, object]
    aliases: Mapping[str, str]
    selection_policy: str
    base_runner_profile: str
    workspace_profile: str
    command_profile: DeviceCommandProfile
    timeout_minutes: int
    artifact_exception_ids: tuple[str, ...]
    live_backend_profiles: tuple[str, ...]
    synthetic_only: bool
    connection_states: tuple[str, ...]
    health_states: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DeviceRecord:
    raw_identifier: str
    family: DeviceFamily
    state: str
    connection: str
    model: str
    capabilities: tuple[str, ...]
    api_level: int | None = None
    os_version: str | None = None
    personal: bool = False
    conflicting: bool = False


@dataclass(frozen=True, slots=True)
class SelectedDevice:
    identity_hash: str
    family: DeviceFamily
    model_class: str
    connection_class: str
    os_or_api: str
    capabilities: tuple[str, ...]
    _raw_identifier: str

    def public_projection(self) -> dict[str, object]:
        return {
            "device_identity_hash": self.identity_hash,
            "family": self.family.value,
            "model_class": self.model_class,
            "connection_class": self.connection_class,
            "os_or_api": self.os_or_api,
            "capabilities": list(self.capabilities),
        }


@dataclass(frozen=True, slots=True)
class DevicePlan:
    request: DeviceRequest
    profile: DeviceProfile
    alias_class: str
    execution_authorized: bool
    authorization_failure: str
    planner_runner_profile: str
    execution_overlay_profile: str
    serialization_backend: str
    concurrency_group: str

    def packet(self, *, runs_on_json: str) -> dict[str, object]:
        """Return the complete bounded plan passed from planner to executor."""

        return {
            "packet_version": "device-plan/1",
            "repository": self.request.repository,
            "admitted_sha": self.request.admitted_sha,
            "source_trust": self.request.source_trust,
            "event_name": self.request.event_name,
            "run_id": self.request.run_id,
            "request_id": self.request.request_id,
            "issue_number": self.request.issue_number,
            "device_family": self.request.family.value,
            "device_capability": self.request.capability,
            "device_alias": self.request.device_alias,
            "alias_class": self.alias_class,
            "device_profile": self.profile.profile_id,
            "command_profile": self.profile.command_profile.profile_id,
            "script_path": self.request.script_path,
            "max_duration_minutes": min(
                self.request.max_duration_minutes, self.profile.timeout_minutes
            ),
            "evidence_exception_id": self.request.evidence_exception_id or "",
            "planner_runner_profile": self.planner_runner_profile,
            "execution_overlay_profile": self.execution_overlay_profile,
            "base_runner_profile": self.profile.base_runner_profile,
            "runs_on_json": runs_on_json,
            "workspace_profile": self.profile.workspace_profile,
            "serialization_backend": self.serialization_backend,
            "concurrency_group": self.concurrency_group,
            "cancel_in_progress": False,
            "execution_authorized": self.execution_authorized,
            "authorization_failure": self.authorization_failure,
        }

    def planning_outputs(self, *, runs_on_json: str) -> dict[str, str]:
        packet = self.packet(runs_on_json=runs_on_json)
        canonical = canonical_json(packet)
        packet_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        summary = canonical_json(
            {
                "alias_class": self.alias_class,
                "device_family": self.request.family.value,
                "device_profile": self.profile.profile_id,
                "execution_authorized": self.execution_authorized,
                "status": "planned",
            }
        )
        return {
            "result": "planned",
            "admitted_sha": self.request.admitted_sha,
            "request_id": self.request.request_id,
            "test_summary": summary,
            "device_evidence_id": "",
            "artifact_exception_used": "false",
            "runner_profile": self.execution_overlay_profile,
            "base_runner_profile": self.profile.base_runner_profile,
            "runs_on_json": runs_on_json,
            "workspace_profile": self.profile.workspace_profile,
            "timeout_minutes": str(packet["max_duration_minutes"]),
            "derived_source_trust": self.request.source_trust,
            "execution_authorized": str(self.execution_authorized).lower(),
            "authorization_failure": self.authorization_failure,
            "concurrency_group": self.concurrency_group,
            "cancel_in_progress": "false",
            "validated_plan": canonical,
            "validated_plan_sha256": packet_hash,
            "failure_code": "",
            "cleanup_result": "not-run",
        }


@dataclass(frozen=True, slots=True)
class LockReceipt:
    accepted: bool
    resource_key: str
    request_id: str
    run_id: str
    device_family: DeviceFamily
    device_profile: str
    epoch: int
    token: str
    owner_hash: str
    expires_at: int
    next_actor: str
    next_action: str


@dataclass(frozen=True, slots=True)
class LockReleaseReceipt:
    released: bool
    resource_key: str
    request_id: str
    epoch: int
    release_receipt: str


@dataclass(frozen=True, slots=True)
class DeviceResult:
    request_id: str
    evidence_id: str
    result: str
    failure_code: str
    cleanup_result: str
    artifact_exception_used: bool
    selected_device_hash: str
    evidence_packet: Mapping[str, object]

    def output_values(self) -> dict[str, str]:
        return {
            "result": self.result,
            "request_id": self.request_id,
            "device_evidence_id": self.evidence_id,
            "artifact_exception_used": str(self.artifact_exception_used).lower(),
            "selected_device_hash": self.selected_device_hash,
            "cleanup_result": self.cleanup_result,
            "failure_code": self.failure_code,
            "test_summary": canonical_json(
                {
                    "cleanup": self.cleanup_result,
                    "result": self.result,
                    "selected_device_hash": self.selected_device_hash,
                }
            ),
        }


def canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def stable_failure(code: str) -> DeviceValidationError:
    return DeviceValidationError(code)


def sorted_unique(values: Sequence[str]) -> tuple[str, ...]:
    return tuple(sorted(set(values)))
