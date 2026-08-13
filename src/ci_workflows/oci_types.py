"""Typed models and stable errors for non-publishing OCI builds."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Mapping

_SAFE_CODE = re.compile(r"^[a-z][a-z0-9_]{2,95}$")
_BASE_NAME = re.compile(r"^[a-z0-9](?:[a-z0-9._:/-]{0,253}[a-z0-9])?$")
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")


def is_exact_base_reference(value: object) -> bool:
    """Return whether value is a bounded canonical scratch/digest base identity."""

    if value == "scratch":
        return True
    if not isinstance(value, str) or len(value) > 327:
        return False
    name, separator, digest = value.rpartition("@")
    if (
        separator != "@"
        or _BASE_NAME.fullmatch(name) is None
        or _DIGEST.fullmatch(digest) is None
        or any(part in {"", ".", ".."} for part in name.split("/"))
    ):
        return False
    return True


class OciBuildError(RuntimeError):
    """Fail-closed OCI build error carrying one stable code."""

    def __init__(self, code: str) -> None:
        if _SAFE_CODE.fullmatch(code) is None:
            raise ValueError("OCI build error code must be safe")
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class OciBuildRequest:
    repository: str
    admitted_sha: str
    product_id: str
    release_version: str | None
    platform_set: str | None
    artifact_exception_id: str | None
    source_trust: str


@dataclass(frozen=True)
class OciTarget:
    target_id: str
    context_path: str
    dockerfile_path: str
    target_stage: str | None
    platforms: tuple[str, ...]
    smoke_script: str | None
    required_user: str | None
    required_entrypoint: tuple[str, ...]
    required_command: tuple[str, ...]
    required_ports: tuple[str, ...]
    required_files: tuple[str, ...]
    required_tools: tuple[str, ...]
    forbidden_tools: tuple[str, ...]
    fixed_build_args: Mapping[str, str]
    secret_mount_ids: tuple[str, ...]


@dataclass(frozen=True)
class OciBuildPlan:
    repository: str
    admitted_sha: str
    product_id: str
    release_version: str
    source_trust: str
    runner_profile: str
    runs_on: tuple[str, ...]
    workspace_profile: str
    timeout_minutes: int
    builder_id: str
    storage_driver: str
    targets: tuple[OciTarget, ...]
    flux_asset: bool
    canary_id: str | None
    previous_known_good: str | None
    rollback_id: str | None
    adoption_ready: bool

    def planning_outputs(self) -> dict[str, str]:
        return {
            "result": "planned",
            "product_id": self.product_id,
            "release_version": self.release_version,
            "runner_profile": self.runner_profile,
            "runs_on_json": json.dumps(list(self.runs_on), separators=(",", ":")),
            "workspace_profile": self.workspace_profile,
            "timeout_minutes": str(self.timeout_minutes),
            "builder_id": self.builder_id,
            "source_trust": self.source_trust,
            "platforms_json": json.dumps(
                {target.target_id: list(target.platforms) for target in self.targets},
                sort_keys=True,
                separators=(",", ":"),
            ),
            "flux_asset": str(self.flux_asset).lower(),
            "canary_id": self.canary_id or "",
            "previous_known_good": self.previous_known_good or "",
            "rollback_id": self.rollback_id or "",
            "failure_code": "",
        }


@dataclass(frozen=True)
class OciPlatformResult:
    platform: str
    manifest_digest: str
    config_digest: str
    layer_digests: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "platform": self.platform,
            "manifest_digest": self.manifest_digest,
            "config_digest": self.config_digest,
            "layer_digests": list(self.layer_digests),
        }


@dataclass(frozen=True)
class OciTargetResult:
    target_id: str
    index_digest: str
    publication_manifest_digest: str
    platform_results: tuple[OciPlatformResult, ...]
    labels: Mapping[str, str]
    smoke_result: str
    resolved_base_references: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {
            "target_id": self.target_id,
            "index_digest": self.index_digest,
            "publication_manifest_digest": self.publication_manifest_digest,
            "platform_results": [row.to_dict() for row in self.platform_results],
            "labels": dict(sorted(self.labels.items())),
            "smoke_result": self.smoke_result,
            "resolved_base_references": list(self.resolved_base_references),
        }


@dataclass(frozen=True)
class OciBuildResult:
    product_id: str
    admitted_sha: str
    release_version: str
    source_date_epoch: int
    targets: tuple[OciTargetResult, ...]
    clean_tree: bool
    cleanup_result: str
    evidence_id: str
    canary_id: str | None
    previous_known_good: str | None
    rollback_id: str | None

    def output_values(self) -> dict[str, str]:
        manifests = {row.target_id: row.index_digest for row in self.targets}
        publication_manifests = {
            row.target_id: row.publication_manifest_digest for row in self.targets
        }
        platforms = {
            row.target_id: [item.to_dict() for item in row.platform_results]
            for row in self.targets
        }
        return {
            "result": "success",
            "source_sha": self.admitted_sha,
            "product_id": self.product_id,
            "release_version": self.release_version,
            "manifest_digests_json": json.dumps(
                manifests, sort_keys=True, separators=(",", ":")
            ),
            "publication_manifest_digests_json": json.dumps(
                publication_manifests, sort_keys=True, separators=(",", ":")
            ),
            "platform_results_json": json.dumps(
                platforms, sort_keys=True, separators=(",", ":")
            ),
            "resolved_base_references_json": json.dumps(
                {
                    row.target_id: list(row.resolved_base_references)
                    for row in self.targets
                },
                sort_keys=True,
                separators=(",", ":"),
            ),
            "clean_tree": str(self.clean_tree).lower(),
            "cleanup_result": self.cleanup_result,
            "artifact_exception_used": "false",
            "evidence_id": self.evidence_id,
            "canary_id": self.canary_id or "",
            "previous_known_good": self.previous_known_good or "",
            "rollback_id": self.rollback_id or "",
            "failure_code": "",
        }
