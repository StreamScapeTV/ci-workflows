"""Typed models and stable errors for reusable Android validation."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Mapping

_SAFE_CODE = re.compile(r"^[a-z][a-z0-9_]{2,95}$")


class AndroidValidationError(RuntimeError):
    """Fail-closed Android validation error carrying one stable code."""

    def __init__(self, code: str) -> None:
        if _SAFE_CODE.fullmatch(code) is None:
            raise ValueError("android validation error code must be safe")
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class AndroidValidationRequest:
    repository: str
    admitted_sha: str
    validation_profile: str
    task_profile: str
    working_directory: str
    gradle_wrapper_path: str
    targeted_test_selector: str | None
    consumer_script_profile: str | None
    private_dependency_contract_id: str | None
    private_dependency_sha: str | None
    artifact_exception_id: str | None
    device_family: str | None
    device_request_id: str | None
    source_trust: str


@dataclass(frozen=True)
class AndroidCommand:
    stage: str
    argv: tuple[str, ...]


@dataclass(frozen=True)
class AndroidWrapperContract:
    mode: str
    version: str
    properties_path: str
    distribution_url: str
    distribution_sha256: str | None
    tracked_blob_sha1: str | None
    properties_blob_sha1: str | None


@dataclass(frozen=True)
class AndroidValidationPlan:
    repository: str
    admitted_sha: str
    validation_profile: str
    task_profile: str
    runner_profile: str
    planner_runner_profile: str
    timeout_minutes: int
    source_trust: str
    working_directory: str
    gradle_wrapper_path: str
    wrapper: AndroidWrapperContract
    commands: tuple[AndroidCommand, ...]
    fixed_gradle_arguments: tuple[str, ...]
    targeted_test_selector: str | None
    consumer_script_path: str | None
    private_dependency_contract_id: str | None
    private_dependency_repository: str | None
    private_dependency_sha: str | None
    private_dependency_subdirectory: str | None
    private_dependency_id: str | None
    artifact_exception_id: str | None
    protected_paths: tuple[str, ...]
    schema_paths: tuple[str, ...]
    expected_debug_outputs: tuple[str, ...]
    output_mode: str
    device_family: str | None
    device_request_id: str | None

    @property
    def requires_private_dependency(self) -> bool:
        return self.private_dependency_contract_id is not None

    @property
    def is_device_handoff(self) -> bool:
        return self.validation_profile == "device-handoff"

    def planning_outputs(self) -> dict[str, str]:
        summary = json.dumps(
            {
                "status": "planned",
                "task_profile": self.task_profile,
                "validation_profile": self.validation_profile,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return {
            "result": "planned",
            "source_sha": self.admitted_sha,
            "validation_profile": self.validation_profile,
            "task_profile": self.task_profile,
            "test_summary": summary,
            "resolved_java_major": "25",
            "resolved_android_api": "37",
            "gradle_version": self.wrapper.version,
            "private_dependency_used": str(self.requires_private_dependency).lower(),
            "private_dependency_contract_id": self.private_dependency_contract_id or "",
            "private_dependency_repository": self.private_dependency_repository or "",
            "private_dependency_sha": self.private_dependency_sha or "",
            "private_dependency_subdirectory": self.private_dependency_subdirectory or "",
            "private_dependency_id": self.private_dependency_id or "",
            "artifact_exception_used": "false",
            "device_handoff_json": "",
            "clean_tree": "false",
            "cleanup_result": "not-run",
            "failure_code": "",
            "evidence_id": "",
            "runner_profile": self.runner_profile,
            "planner_runner_profile": self.planner_runner_profile,
            "workspace_profile": "android-isolated",
            "timeout_minutes": str(self.timeout_minutes),
            "source_trust": self.source_trust,
        }


@dataclass(frozen=True)
class AndroidValidationResult:
    source_sha: str
    validation_profile: str
    task_profile: str
    java_major: int
    android_api: int
    gradle_version: str
    stage_count: int
    private_dependency_used: bool
    debug_output_verified: bool
    schema_verified: bool
    clean_tree: bool
    cleanup_result: str
    artifact_exception_used: bool
    device_handoff: Mapping[str, str] | None
    evidence_id: str

    def output_values(self) -> dict[str, str]:
        summary = json.dumps(
            {
                "stages": self.stage_count,
                "status": "passed",
                "task_profile": self.task_profile,
                "validation_profile": self.validation_profile,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return {
            "result": "success",
            "source_sha": self.source_sha,
            "validation_profile": self.validation_profile,
            "task_profile": self.task_profile,
            "test_summary": summary,
            "resolved_java_major": str(self.java_major),
            "resolved_android_api": str(self.android_api),
            "gradle_version": self.gradle_version,
            "private_dependency_used": str(self.private_dependency_used).lower(),
            "artifact_exception_used": str(self.artifact_exception_used).lower(),
            "device_handoff_json": (
                json.dumps(self.device_handoff, sort_keys=True, separators=(",", ":"))
                if self.device_handoff is not None
                else ""
            ),
            "debug_output_verified": str(self.debug_output_verified).lower(),
            "schema_verified": str(self.schema_verified).lower(),
            "clean_tree": str(self.clean_tree).lower(),
            "cleanup_result": self.cleanup_result,
            "failure_code": "",
            "evidence_id": self.evidence_id,
        }
