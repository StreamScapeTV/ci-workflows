"""Typed models and stable errors for reusable Node validation."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Mapping

_SAFE_CODE = re.compile(r"^[a-z][a-z0-9_]{2,95}$")


class NodeValidationError(RuntimeError):
    """Fail-closed Node validation error carrying one stable code."""

    def __init__(self, code: str) -> None:
        if _SAFE_CODE.fullmatch(code) is None:
            raise ValueError("node validation error code must be safe")
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class NodeValidationRequest:
    repository: str
    admitted_sha: str
    validation_profile: str
    version_file: str | None
    node_version: str | None
    working_directory: str
    install_profile: str
    command_profile: str
    script_path: str | None
    static_output_directory: str | None
    output_verifier_path: str | None
    public_environment: Mapping[str, str]
    artifact_exception_id: str | None
    source_trust: str


@dataclass(frozen=True)
class NodeCommand:
    stage: str
    argv: tuple[str, ...]


@dataclass(frozen=True)
class NodeValidationPlan:
    repository: str
    admitted_sha: str
    validation_profile: str
    command_profile: str
    runner_profile: str
    timeout_minutes: int
    workspace_profile: str
    source_trust: str
    node_version: str
    working_directory: str
    install_profile: str
    manifest_path: str | None
    lockfile_path: str | None
    script_path: str | None
    static_output_directory: str | None
    output_verifier_path: str | None
    output_mode: str
    allowed_public_environment: tuple[str, ...]
    public_environment: Mapping[str, str]
    commands: tuple[NodeCommand, ...]
    adoption_ready: bool

    def planning_outputs(self) -> dict[str, str]:
        summary = json.dumps(
            {
                "command_profile": self.command_profile,
                "status": "planned",
                "validation_profile": self.validation_profile,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return {
            "result": "planned",
            "node_version": self.node_version,
            "npm_version": "",
            "install_result": "planned" if self.install_profile == "npm-ci" else "skipped",
            "test_summary": summary,
            "build_result": "planned" if self.output_mode != "none" else "skipped",
            "output_verified": "false",
            "output_digest": "",
            "clean_tree": "false",
            "cleanup_result": "not-run",
            "artifact_exception_used": "false",
            "evidence_id": "",
            "runner_profile": self.runner_profile,
            "workspace_profile": self.workspace_profile,
            "timeout_minutes": str(self.timeout_minutes),
            "source_trust": self.source_trust,
        }


@dataclass(frozen=True)
class NodeValidationResult:
    node_version: str
    npm_version: str
    validation_profile: str
    command_profile: str
    install_result: str
    test_count: int
    build_result: str
    output_verified: bool
    output_digest: str | None
    clean_tree: bool
    cleanup_result: str
    evidence_id: str

    def output_values(self) -> dict[str, str]:
        summary = json.dumps(
            {
                "command_profile": self.command_profile,
                "status": "passed",
                "tests": self.test_count,
                "validation_profile": self.validation_profile,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return {
            "result": "success",
            "node_version": self.node_version,
            "npm_version": self.npm_version,
            "install_result": self.install_result,
            "test_summary": summary,
            "build_result": self.build_result,
            "output_verified": str(self.output_verified).lower(),
            "output_digest": self.output_digest or "",
            "clean_tree": str(self.clean_tree).lower(),
            "cleanup_result": self.cleanup_result,
            "artifact_exception_used": "false",
            "evidence_id": self.evidence_id,
        }
