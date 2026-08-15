"""Typed data and stable failures for the bounded Helm workflow family."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


class HelmValidationError(ValueError):
    """A stable, non-sensitive Helm validation failure."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class HelmRequest:
    repository: str
    admitted_sha: str
    product_id: str
    release_version: str | None
    values_profile: str | None
    policy_path: str | None
    artifact_exception_id: str | None
    source_trust: str


@dataclass(frozen=True)
class HelmProduct:
    product_id: str
    repository: str
    chart_name: str
    chart_root: str
    values_profiles: Mapping[str, str]
    policy_path: str | None
    registry_repository: str
    locked_dependencies: tuple[tuple[str, str, str], ...]
    required_image_references: tuple[str, ...]


@dataclass(frozen=True)
class HelmPlan:
    product: HelmProduct
    release_version: str | None
    values_profile: str
    values_path: str
    policy_path: str | None
    runner_profile: str = "portable"
    workspace_profile: str = "minimal"
    timeout_minutes: int = 60

    def planning_outputs(self) -> dict[str, str]:
        return {
            "result": "planned",
            "runner_profile": self.runner_profile,
            "workspace_profile": self.workspace_profile,
            "timeout_minutes": str(self.timeout_minutes),
            "chart_name": self.product.chart_name,
        }


@dataclass(frozen=True)
class HelmValidationResult:
    chart_digest: str
    package_sha256: str
    summary: str
    archive_path: Path

    def output_values(self) -> dict[str, str]:
        return {
            "result": "success",
            "chart_digest": self.chart_digest,
            "artifact_exception_used": "false",
            "test_summary": self.summary,
            "chart_package_sha256": self.package_sha256,
        }


@dataclass(frozen=True)
class HelmPublicationResult:
    chart_digest: str
    immutable_references_json: str
    package_sha256: str
    published: bool

    def output_values(self) -> dict[str, str]:
        return {
            "result": "success",
            "chart_digest": self.chart_digest,
            "immutable_references_json": self.immutable_references_json,
            "chart_package_sha256": self.package_sha256,
            "published": str(self.published).lower(),
        }
