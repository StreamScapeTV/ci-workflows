"""Sanitized immutable producer-to-Flux handoff construction."""
from __future__ import annotations

import re
from typing import Any

from .release_contract import validate_release_version
from .release_manifest import canonical_json, sha256_text
from .release_types import PublicationIdentity, ReleaseError, ReleasePlan


FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
SAFE_SELECTION_ID = re.compile(r"^[A-Za-z0-9._:@/+\-]{0,256}$")
GITHUB_RELEASE = re.compile(
    r"^https://github\.com/StreamScapeTV/[A-Za-z0-9_.-]+/releases/tag/[0-9A-Za-z.-]+$"
)
FORBIDDEN_TEXT = re.compile(
    r"(?i)(authorization\s*[:=]|password\s*[:=]|secret\s*[:=]|token\s*[:=]|"
    r"-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----)"
)


def require(condition: bool, code: str) -> None:
    if not condition:
        raise ReleaseError(code)


def _verify_sanitized(value: Any) -> None:
    if isinstance(value, str):
        require(len(value) <= 512 and FORBIDDEN_TEXT.search(value) is None, "handoff_secret_rejected")
        return
    if isinstance(value, list):
        require(len(value) <= 16, "handoff_bounds_rejected")
        for item in value:
            _verify_sanitized(item)
        return
    if isinstance(value, dict):
        require(len(value) <= 24, "handoff_bounds_rejected")
        for key, item in value.items():
            require(isinstance(key, str) and len(key) <= 64, "handoff_bounds_rejected")
            _verify_sanitized(item)
        return
    require(value is None or isinstance(value, (bool, int)), "handoff_type_rejected")


def _selection(
    canary_id: str,
    previous_known_good: str,
    rollback_id: str,
) -> dict[str, str] | None:
    values = {
        "canary_id": canary_id.strip(),
        "previous_known_good": previous_known_good.strip(),
        "rollback_id": rollback_id.strip(),
    }
    require(
        all(SAFE_SELECTION_ID.fullmatch(value) is not None for value in values.values()),
        "handoff_selection_rejected",
    )
    populated = [bool(value) for value in values.values()]
    require(all(populated) or not any(populated), "handoff_selection_rejected")
    return values if all(populated) else None


def build_flux_handoff(
    *,
    plan: ReleasePlan,
    release_version: str,
    source_sha: str,
    release_manifest_sha256: str,
    github_release_url: str,
    image: PublicationIdentity,
    chart: PublicationIdentity,
    canary_id: str = "",
    previous_known_good: str = "",
    rollback_id: str = "",
) -> dict[str, Any]:
    version = validate_release_version(release_version)
    require(FULL_SHA.fullmatch(source_sha) is not None, "release_sha_rejected")
    require(SHA256.fullmatch(release_manifest_sha256) is not None, "manifest_digest_rejected")
    require(GITHUB_RELEASE.fullmatch(github_release_url) is not None, "github_release_url_rejected")
    require(image.product_id == plan.image_product_id and image.kind == "oci-image", "image_identity_mismatch")
    require(chart.product_id == plan.chart_product_id and chart.kind == "helm-chart", "chart_identity_mismatch")
    require(plan.handoff_kind == "flux-selection-request", "handoff_contract_rejected")
    require(plan.handoff_target_repository == "StreamScapeTV/flux", "handoff_contract_rejected")
    require(plan.handoff_requested_action == "review-selection", "handoff_contract_rejected")
    selection = _selection(canary_id, previous_known_good, rollback_id)
    if plan.release_id == "flux-runner-assets":
        require(selection is not None, "handoff_selection_required")
    else:
        require(selection is None, "handoff_selection_rejected")
    payload: dict[str, Any] = {
        "schema_version": 1,
        "kind": "flux-selection-request",
        "producer_repository": plan.repository,
        "target_repository": "StreamScapeTV/flux",
        "release_id": plan.release_id,
        "release_version": version,
        "source_sha": source_sha,
        "release_manifest_sha256": release_manifest_sha256,
        "github_release_url": github_release_url,
        "products": [image.as_handoff_dict(), chart.as_handoff_dict()],
        "requested_action": "review-selection",
        "mutation_authorized": False,
        "secrets_included": False,
    }
    if selection is not None:
        payload["selection"] = selection
    _verify_sanitized(payload)
    return payload


def flux_handoff_json(**kwargs: Any) -> tuple[str, str]:
    rendered = canonical_json(build_flux_handoff(**kwargs))
    return rendered, sha256_text(rendered)
