"""Sanitized immutable producer-to-Flux handoff construction."""
from __future__ import annotations

import re
from typing import Any, Mapping

from .release_contract import validate_release_version
from .release_manifest import canonical_json, sha256_text
from .release_types import PublicationIdentity, ReleaseError, ReleasePlan


FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
IDENTIFIER = re.compile(r"^[a-z][a-z0-9-]{1,63}$")
REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
SAFE_SELECTION_ID = re.compile(r"^[A-Za-z0-9._:@/+\-]{0,256}$")
GITHUB_RELEASE = re.compile(
    r"^https://github\.com/StreamScapeTV/[A-Za-z0-9_.-]+/releases/tag/[0-9A-Za-z.-]+$"
)
FORBIDDEN_TEXT = re.compile(
    r"(?i)(authorization\s*[:=]|password\s*[:=]|secret\s*[:=]|token\s*[:=]|"
    r"-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----)"
)
BASE_HANDOFF_KEYS = {
    "schema_version",
    "kind",
    "producer_repository",
    "target_repository",
    "release_id",
    "release_version",
    "source_sha",
    "release_manifest_sha256",
    "github_release_url",
    "products",
    "requested_action",
    "mutation_authorized",
    "secrets_included",
}
PRODUCT_KEYS = {
    "product_id",
    "kind",
    "digest",
    "digests",
    "immutable_references",
}


def require(condition: bool, code: str) -> None:
    if not condition:
        raise ReleaseError(code)


def _verify_sanitized(value: Any) -> None:
    if isinstance(value, str):
        require(
            len(value) <= 512 and FORBIDDEN_TEXT.search(value) is None,
            "handoff_secret_rejected",
        )
        return
    if isinstance(value, list):
        require(len(value) <= 16, "handoff_bounds_rejected")
        for item in value:
            _verify_sanitized(item)
        return
    if isinstance(value, dict):
        require(len(value) <= 24, "handoff_bounds_rejected")
        for key, item in value.items():
            require(
                isinstance(key, str) and len(key) <= 64,
                "handoff_bounds_rejected",
            )
            _verify_sanitized(item)
        return
    require(
        value is None or isinstance(value, (bool, int)),
        "handoff_type_rejected",
    )


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


def _validate_product(value: Any) -> Mapping[str, Any]:
    require(
        isinstance(value, Mapping) and set(value) == PRODUCT_KEYS,
        "handoff_product_rejected",
    )
    product_id = value.get("product_id")
    kind = value.get("kind")
    digest = value.get("digest")
    digests = value.get("digests")
    references = value.get("immutable_references")
    require(
        isinstance(product_id, str) and IDENTIFIER.fullmatch(product_id) is not None,
        "handoff_product_rejected",
    )
    require(kind in {"oci-image", "helm-chart"}, "handoff_product_rejected")
    require(
        isinstance(digest, str) and DIGEST.fullmatch(digest) is not None,
        "handoff_product_rejected",
    )
    require(
        isinstance(digests, Mapping)
        and 0 < len(digests) <= 16
        and all(
            isinstance(key, str)
            and 0 < len(key) <= 128
            and not any(character.isspace() for character in key)
            and isinstance(item, str)
            and DIGEST.fullmatch(item) is not None
            for key, item in digests.items()
        ),
        "handoff_product_rejected",
    )
    require(
        isinstance(references, list)
        and 0 < len(references) <= 16
        and len(references) == len(set(references))
        and all(
            isinstance(reference, str)
            and 0 < len(reference) <= 512
            and not any(character.isspace() for character in reference)
            and ":latest" not in reference.casefold()
            for reference in references
        ),
        "handoff_product_rejected",
    )
    return value


def validate_flux_handoff_payload(value: Any) -> dict[str, Any]:
    """Validate the exact bounded handoff shape before any cross-repository send."""
    require(isinstance(value, dict), "handoff_json_rejected")
    release_id = value.get("release_id")
    expected_keys = set(BASE_HANDOFF_KEYS)
    if release_id == "flux-runner-assets":
        expected_keys.add("selection")
    require(set(value) == expected_keys, "handoff_contract_rejected")
    require(value.get("schema_version") == 1, "handoff_contract_rejected")
    require(value.get("kind") == "flux-selection-request", "handoff_contract_rejected")
    producer_repository = value.get("producer_repository")
    require(
        isinstance(producer_repository, str)
        and REPOSITORY.fullmatch(producer_repository) is not None,
        "handoff_contract_rejected",
    )
    require(
        value.get("target_repository") == "StreamScapeTV/flux",
        "handoff_contract_rejected",
    )
    require(
        isinstance(release_id, str) and IDENTIFIER.fullmatch(release_id) is not None,
        "handoff_contract_rejected",
    )
    release_version = value.get("release_version")
    require(isinstance(release_version, str), "handoff_contract_rejected")
    version = validate_release_version(release_version)
    source_sha = value.get("source_sha")
    manifest_sha = value.get("release_manifest_sha256")
    require(
        isinstance(source_sha, str) and FULL_SHA.fullmatch(source_sha) is not None,
        "handoff_contract_rejected",
    )
    require(
        isinstance(manifest_sha, str) and SHA256.fullmatch(manifest_sha) is not None,
        "handoff_contract_rejected",
    )
    github_release_url = value.get("github_release_url")
    expected_release_url = (
        f"https://github.com/{producer_repository}/releases/tag/v{version}"
    )
    require(
        isinstance(github_release_url, str)
        and GITHUB_RELEASE.fullmatch(github_release_url) is not None
        and github_release_url == expected_release_url,
        "handoff_contract_rejected",
    )
    products = value.get("products")
    require(
        isinstance(products, list) and len(products) == 2,
        "handoff_product_rejected",
    )
    validated_products = [_validate_product(product) for product in products]
    require(
        {product["kind"] for product in validated_products}
        == {"oci-image", "helm-chart"}
        and len({product["product_id"] for product in validated_products}) == 2,
        "handoff_product_rejected",
    )
    require(
        value.get("requested_action") == "review-selection"
        and value.get("mutation_authorized") is False
        and value.get("secrets_included") is False,
        "handoff_contract_rejected",
    )
    if release_id == "flux-runner-assets":
        selection = value.get("selection")
        require(
            isinstance(selection, Mapping)
            and set(selection)
            == {"canary_id", "previous_known_good", "rollback_id"}
            and all(
                isinstance(item, str)
                and bool(item)
                and SAFE_SELECTION_ID.fullmatch(item) is not None
                for item in selection.values()
            ),
            "handoff_selection_rejected",
        )
    _verify_sanitized(value)
    return dict(value)


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
    require(
        SHA256.fullmatch(release_manifest_sha256) is not None,
        "manifest_digest_rejected",
    )
    require(
        GITHUB_RELEASE.fullmatch(github_release_url) is not None,
        "github_release_url_rejected",
    )
    require(
        image.product_id == plan.image_product_id and image.kind == "oci-image",
        "image_identity_mismatch",
    )
    require(
        chart.product_id == plan.chart_product_id and chart.kind == "helm-chart",
        "chart_identity_mismatch",
    )
    require(
        plan.handoff_kind == "flux-selection-request",
        "handoff_contract_rejected",
    )
    require(
        plan.handoff_target_repository == "StreamScapeTV/flux",
        "handoff_contract_rejected",
    )
    require(
        plan.handoff_requested_action == "review-selection",
        "handoff_contract_rejected",
    )
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
    return validate_flux_handoff_payload(payload)


def flux_handoff_json(**kwargs: Any) -> tuple[str, str]:
    rendered = canonical_json(build_flux_handoff(**kwargs))
    return rendered, sha256_text(rendered)
