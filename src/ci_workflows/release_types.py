"""Bounded types for exact-tag release orchestration."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


class ReleaseError(RuntimeError):
    """Fail-closed release contract or replay error."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class ReleasePlan:
    release_id: str
    repository: str
    image_product_id: str
    chart_product_id: str
    chart_requires_image_identity: bool
    github_release: bool
    handoff_kind: str
    handoff_target_repository: str
    handoff_requested_action: str

    @property
    def product_ids(self) -> tuple[str, str]:
        return (self.image_product_id, self.chart_product_id)

    def as_dict(self) -> dict[str, Any]:
        return {
            "release_id": self.release_id,
            "repository": self.repository,
            "image_product_id": self.image_product_id,
            "chart_product_id": self.chart_product_id,
            "chart_requires_image_identity": self.chart_requires_image_identity,
            "github_release": self.github_release,
            "handoff": {
                "kind": self.handoff_kind,
                "target_repository": self.handoff_target_repository,
                "requested_action": self.handoff_requested_action,
                "mutation_authorized": False,
            },
        }


@dataclass(frozen=True)
class PublicationIdentity:
    product_id: str
    kind: str
    digest: str
    digests: dict[str, str]
    immutable_references: tuple[str, ...]
    evidence: dict[str, Any]

    def as_manifest_dict(self) -> dict[str, Any]:
        return {
            "product_id": self.product_id,
            "kind": self.kind,
            "digest": self.digest,
            "digests": self.digests,
            "immutable_references": list(self.immutable_references),
            "evidence": self.evidence,
        }

    def as_handoff_dict(self) -> dict[str, Any]:
        return {
            "product_id": self.product_id,
            "kind": self.kind,
            "digest": self.digest,
            "digests": self.digests,
            "immutable_references": list(self.immutable_references),
        }
