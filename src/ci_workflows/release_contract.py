"""Strict checked-in release plan admission for issue #19."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Mapping

from .release_types import ReleaseError, ReleasePlan


RELEASES_PATH = Path("contracts/releases.json")
PRODUCTS_PATH = Path("contracts/products.json")
SEMVER = re.compile(
    r"^(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)
REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
IDENTIFIER = re.compile(r"^[a-z][a-z0-9-]{1,63}$")
IMAGE_KINDS = {"oci-image", "oci-runner-image-family"}
CHART_KINDS = {"helm-oci-chart", "helm-oci-chart-assets"}
EXPECTED_RELEASE_IDS = ("agent-state", "flux-runner-assets", "iptv-backend")


def require(condition: bool, code: str) -> None:
    if not condition:
        raise ReleaseError(code)


def _read_json(path: Path, code: str) -> Mapping[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ReleaseError(code) from error
    require(isinstance(value, Mapping), code)
    return value


def validate_release_version(value: str) -> str:
    candidate = value.strip()
    require(SEMVER.fullmatch(candidate) is not None, "release_version_rejected")
    require(candidate.casefold() != "latest", "latest_forbidden")
    return candidate


def _load_products(root: Path) -> dict[str, Mapping[str, Any]]:
    payload = _read_json(root / PRODUCTS_PATH, "product_inventory_invalid")
    require(payload.get("schema_version") == 1, "product_inventory_invalid")
    rows = payload.get("products")
    require(isinstance(rows, list) and rows, "product_inventory_invalid")
    result: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        require(isinstance(row, Mapping), "product_inventory_invalid")
        identifier = row.get("id")
        require(isinstance(identifier, str) and IDENTIFIER.fullmatch(identifier) is not None, "product_inventory_invalid")
        require(identifier not in result, "product_inventory_invalid")
        result[identifier] = row
    return result


def _validate_policy(payload: Mapping[str, Any]) -> None:
    require(payload.get("schema_version") == 1, "release_contract_invalid")
    require(payload.get("release_api") == "release.orchestrate", "release_contract_invalid")
    require(payload.get("release_api_version") == "1.0.0", "release_contract_invalid")
    tag = payload.get("tag_policy")
    require(isinstance(tag, Mapping), "release_contract_invalid")
    require(
        tag == {
            "default_mode": "tag-push",
            "trusted_replay_mode": "existing-tag",
            "version_pattern": SEMVER.pattern,
            "latest_forbidden": True,
            "exact_source_required": True,
        },
        "release_contract_invalid",
    )
    publication = payload.get("publication_policy")
    require(
        publication
        == {
            "remote_read_back_required": True,
            "matching_replay": "idempotent",
            "conflicting_replay": "fail-closed",
            "partial_publication": "verify-and-resume",
            "routine_actions_artifacts": "forbidden",
            "deployment_separate": True,
        },
        "release_contract_invalid",
    )


def load_release_plans(root: Path) -> dict[str, ReleasePlan]:
    root = root.resolve()
    payload = _read_json(root / RELEASES_PATH, "release_contract_invalid")
    require(
        set(payload)
        == {
            "schema_version",
            "release_api",
            "release_api_version",
            "tag_policy",
            "publication_policy",
            "releases",
        },
        "release_contract_invalid",
    )
    _validate_policy(payload)
    products = _load_products(root)
    rows = payload.get("releases")
    require(isinstance(rows, list) and len(rows) == 3, "release_contract_invalid")
    identifiers = [row.get("release_id") if isinstance(row, Mapping) else None for row in rows]
    require(tuple(identifiers) == EXPECTED_RELEASE_IDS, "release_contract_invalid")
    plans: dict[str, ReleasePlan] = {}
    for row in rows:
        require(isinstance(row, Mapping), "release_contract_invalid")
        require(
            set(row)
            == {
                "release_id",
                "repository",
                "image_product_id",
                "chart_product_id",
                "chart_requires_image_identity",
                "github_release",
                "handoff",
            },
            "release_contract_invalid",
        )
        release_id = row.get("release_id")
        repository = row.get("repository")
        image_id = row.get("image_product_id")
        chart_id = row.get("chart_product_id")
        require(isinstance(release_id, str) and IDENTIFIER.fullmatch(release_id) is not None, "release_contract_invalid")
        require(isinstance(repository, str) and REPOSITORY.fullmatch(repository) is not None, "release_contract_invalid")
        require(isinstance(image_id, str) and image_id in products, "release_contract_invalid")
        require(isinstance(chart_id, str) and chart_id in products, "release_contract_invalid")
        image = products[image_id]
        chart = products[chart_id]
        require(image.get("repository") == repository and chart.get("repository") == repository, "release_contract_invalid")
        require(image.get("status") == "current" and chart.get("status") == "current", "release_contract_invalid")
        require(image.get("kind") in IMAGE_KINDS, "release_contract_invalid")
        require(chart.get("kind") in CHART_KINDS, "release_contract_invalid")
        require(row.get("chart_requires_image_identity") is True, "release_contract_invalid")
        require(row.get("github_release") is True, "release_contract_invalid")
        handoff = row.get("handoff")
        require(
            handoff
            == {
                "kind": "flux-selection-request",
                "target_repository": "StreamScapeTV/flux",
                "requested_action": "review-selection",
                "mutation_authorized": False,
            },
            "release_contract_invalid",
        )
        plans[release_id] = ReleasePlan(
            release_id=release_id,
            repository=repository,
            image_product_id=image_id,
            chart_product_id=chart_id,
            chart_requires_image_identity=True,
            github_release=True,
            handoff_kind="flux-selection-request",
            handoff_target_repository="StreamScapeTV/flux",
            handoff_requested_action="review-selection",
        )
    return plans


def resolve_release_plan(root: Path, release_id: str, repository: str) -> ReleasePlan:
    require(isinstance(release_id, str) and IDENTIFIER.fullmatch(release_id.strip()) is not None, "release_id_rejected")
    require(isinstance(repository, str) and REPOSITORY.fullmatch(repository.strip()) is not None, "repository_rejected")
    plans = load_release_plans(root)
    plan = plans.get(release_id.strip())
    require(plan is not None, "release_id_rejected")
    require(plan.repository == repository.strip(), "repository_rejected")
    return plan
