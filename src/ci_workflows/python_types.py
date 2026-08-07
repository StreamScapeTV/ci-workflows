"Typed models and stable errors for reusable Python validation."

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Mapping

SAFE_CODE = re.compile(r"^[a-z][a-z0-9_]{2,95}$")


class PythonValidationError(RuntimeError):
    "Fail-closed Python validation error carrying one stable code."

    def __init__(self, code: str) -> None:
        if SAFE_CODE.fullmatch(code) is None:
            raise ValueError("python validation error code must be safe")
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class PythonValidationRequest:
    repository: str
    admitted_sha: str
    validation_profile: str
    command_profile: str
    working_directory: str
    version_file: str | None
    script_path: str | None
    artifact_exception_id: str | None
    source_trust: str


@dataclass(frozen=True)
class PythonCommand:
    stage: str
    argv: tuple[str, ...]


@dataclass(frozen=True)
class PythonValidationPlan:
    repository: str
    admitted_sha: str
    validation_profile: str
    command_profile: str
    runner_profile: str
    timeout_minutes: int
    workspace_profile: str
    isolation: str
    runtime_id: str
    runtime_reference: str | None
    python_version: str
    working_directory: str
    version_file: str | None
    dependency_file: str | None
    script_path: str | None
    database_environment_variable: str | None
    environment: Mapping[str, str]
    commands: tuple[PythonCommand, ...]
    postgres_runtime_reference: str | None
    readiness_attempts: int
    readiness_interval_seconds: int

    def planning_outputs(self) -> dict[str, str]:
        return {
            "source_sha": self.admitted_sha,
            "validation_profile": self.validation_profile,
            "command_profile": self.command_profile,
            "runner_profile": self.runner_profile,
            "timeout_minutes": str(self.timeout_minutes),
            "resolved_python_version": self.python_version,
            "result": "planned",
            "test_summary": "bounded Python validation plan accepted",
            "cleanup_result": "not-run",
            "failure_code": "",
            "artifact_exception_used": "",
        }


@dataclass(frozen=True)
class PythonValidationResult:
    source_sha: str
    resolved_python_version: str
    validation_profile: str
    command_profile: str
    stage_count: int
    cleanup_result: str
    evidence_id: str

    def output_values(self) -> dict[str, str]:
        return {
            "result": "success",
            "test_summary": f"{self.stage_count} bounded Python stage(s) passed",
            "source_sha": self.source_sha,
            "resolved_python_version": self.resolved_python_version,
            "validation_profile": self.validation_profile,
            "command_profile": self.command_profile,
            "cleanup_result": self.cleanup_result,
            "failure_code": "",
            "artifact_exception_used": "",
            "evidence_id": self.evidence_id,
        }
