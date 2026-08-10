"""Typed models for bounded source-only GitOps validation."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import Enum
from typing import Mapping

_SAFE_CODE = re.compile(r"^[a-z][a-z0-9_]{2,95}$")


class GitOpsValidationError(RuntimeError):
    """Fail-closed validation error carrying one stable public code."""

    def __init__(self, code: str) -> None:
        if _SAFE_CODE.fullmatch(code) is None:
            raise ValueError("GitOps validation error code must be safe")
        self.code = code
        super().__init__(code)


class GitOpsProfile(str, Enum):
    SOURCE_AUDIT = "source-audit"
    YAML = "yaml"
    HELM_RENDER = "helm-render"
    KUSTOMIZE_RENDER = "kustomize-render"
    CHANGED_TREE = "changed-tree"
    FULL = "full"


class GitOpsTargetKind(str, Enum):
    YAML = "yaml"
    HELM = "helm"
    KUSTOMIZE = "kustomize"


@dataclass(frozen=True, slots=True)
class GitOpsToolPin:
    name: str
    version: str
    url: str
    sha256: str
    archive_member: str
    max_bytes: int
    version_args: tuple[str, ...]
    version_pattern: str


@dataclass(frozen=True, slots=True)
class GitOpsTarget:
    target_id: str
    kind: GitOpsTargetKind
    root: str
    include: tuple[str, ...]
    schema_path: str | None = None
    values_files: tuple[str, ...] = ()
    required_values: tuple[str, ...] = ()
    sops_files: tuple[str, ...] = ()
    expected_render_path: str | None = None
    kubernetes_version: str | None = None
    schema_locations: tuple[str, ...] = ()
    dependency_archives: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class GitOpsPolicyScript:
    policy_id: str
    path: str
    argv: tuple[str, ...]
    allowed_profiles: tuple[str, ...]
    timeout_seconds: int
    max_output_bytes: int
    sha256: str


@dataclass(frozen=True, slots=True)
class GitOpsRequest:
    repository: str
    admitted_sha: str
    consumer_contract: str
    validation_profile: GitOpsProfile
    source_trust: str
    change_base_sha: str | None = None
    policy_script_profile: str | None = None
    artifact_exception_id: str | None = None


@dataclass(frozen=True, slots=True)
class GitOpsPlan:
    request: GitOpsRequest
    runner_profile: str
    workspace_profile: str
    timeout_minutes: int
    tools: tuple[GitOpsToolPin, ...]
    targets: tuple[GitOpsTarget, ...]
    policy_script: GitOpsPolicyScript | None

    def planning_outputs(self) -> dict[str, str]:
        return {
            "result": "planned",
            "source_sha": self.request.admitted_sha,
            "consumer_contract": self.request.consumer_contract,
            "validation_profile": self.request.validation_profile.value,
            "runner_profile": self.runner_profile,
            "workspace_profile": self.workspace_profile,
            "timeout_minutes": str(self.timeout_minutes),
            "source_trust": self.request.source_trust,
            "target_ids_json": _compact_json(
                [target.target_id for target in self.targets]
            ),
            "tool_versions_json": _compact_json(
                {tool.name: tool.version for tool in self.tools}
            ),
            "policy_script": (
                self.policy_script.policy_id if self.policy_script else ""
            ),
            "artifact_exception_used": "false",
            "cleanup_result": "not-run",
            "failure_code": "",
        }


@dataclass(frozen=True, slots=True)
class ObjectIdentity:
    api_version: str
    kind: str
    namespace: str
    name: str

    @property
    def label(self) -> str:
        return "/".join(
            (self.api_version, self.kind, self.namespace or "_cluster", self.name)
        )


@dataclass(frozen=True, slots=True)
class GitOpsResult:
    plan: GitOpsPlan
    rendered_objects: int
    validated_files: int
    selected_targets: tuple[str, ...]
    render_digest: str
    policy_result: str
    clean_tree: bool
    cleanup_result: str
    evidence_id: str
    tool_versions: Mapping[str, str]

    def output_values(self) -> dict[str, str]:
        summary = {
            "files": self.validated_files,
            "objects": self.rendered_objects,
            "profile": self.plan.request.validation_profile.value,
            "status": "passed",
            "targets": list(self.selected_targets),
        }
        return {
            "result": "success",
            "source_sha": self.plan.request.admitted_sha,
            "consumer_contract": self.plan.request.consumer_contract,
            "validation_profile": self.plan.request.validation_profile.value,
            "runner_profile": self.plan.runner_profile,
            "source_trust": self.plan.request.source_trust,
            "test_summary": _compact_json(summary),
            "selected_targets_json": _compact_json(list(self.selected_targets)),
            "validated_files": str(self.validated_files),
            "rendered_objects": str(self.rendered_objects),
            "render_digest": self.render_digest,
            "policy_result": self.policy_result,
            "tool_versions_json": _compact_json(dict(self.tool_versions)),
            "clean_tree": str(self.clean_tree).lower(),
            "cleanup_result": self.cleanup_result,
            "artifact_exception_used": "false",
            "evidence_id": self.evidence_id,
            "failure_code": "",
        }


def _compact_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))
