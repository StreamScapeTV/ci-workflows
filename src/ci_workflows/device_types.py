"""Typed models and stable failures for physical-device validation."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import Enum
from typing import Mapping, Sequence

_SAFE_CODE = re.compile(r"^[a-z][a-z0-9_]{2,95}$")


class DeviceValidationError(RuntimeError):
    """Fail-closed validation error carrying one stable public code."""

    def __init__(self, code: str) -> None:
        if _SAFE_CODE.fullmatch(code) is None:
            raise ValueError("device validation error code must be safe")
        self.code = code
        super().__init__(code)


class DeviceFamily(str, Enum):
    ANDROID = "android"
    IOS = "ios"
    TVOS = "tvos"


class SerialPolicy(str, Enum):
    FORBIDDEN = "forbidden"
    CONTRACT_OWNED = "contract-owned"
    EXACT_CALLER = "exact-caller"


@dataclass(frozen=True, slots=True)
class DeviceRequest:
    repository: str
    admitted_sha: str
    family: DeviceFamily
    capability: str
    device_identifier: str | None
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
    serial_policy: SerialPolicy
    selection_policy: str
    base_runner_profile: str
    workspace_profile: str
    command_profile: DeviceCommandProfile
    timeout_minutes: int
    artifact_exception_ids: tuple[str, ...]
    live_backend_profiles: tuple[str, ...]
    execution_allowed: bool
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
    execution_authorized: bool
    lock_backend: str
    planner_runner_profile: str
    execution_overlay_profile: str

    def planning_outputs(
        self,
        *,
        runs_on_json: str,
    ) -> dict[str, str]:
        summary = json.dumps(
            {
                "capability": self.request.capability,
                "device_family": self.request.family.value,
                "device_profile": self.profile.profile_id,
                "status": "planned",
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return {
            "result": "planned",
            "request_id": self.request.request_id,
            "test_summary": summary,
            "device_evidence_id": "",
            "artifact_exception_used": "false",
            "runner_profile": self.execution_overlay_profile,
            "base_runner_profile": self.profile.base_runner_profile,
            "runs_on_json": runs_on_json,
            "workspace_profile": self.profile.workspace_profile,
            "timeout_minutes": str(
                min(self.request.max_duration_minutes, self.profile.timeout_minutes)
            ),
            "source_trust": self.request.source_trust,
            "execution_authorized": str(self.execution_authorized).lower(),
            "lock_backend": self.lock_backend,
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
            "test_summary": json.dumps(
                {
                    "cleanup": self.cleanup_result,
                    "result": self.result,
                    "selected_device_hash": self.selected_device_hash,
                },
                sort_keys=True,
                separators=(",", ":"),
            ),
        }


def stable_failure(code: str) -> DeviceValidationError:
    return DeviceValidationError(code)


def sorted_unique(values: Sequence[str]) -> tuple[str, ...]:
    return tuple(sorted(set(values)))
