"""Central Helm product layout policy for chart roots and render profiles."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from .helm_contract import (
    IMMUTABLE_IMAGE_REFERENCE,
    NAME,
    PRODUCT_MANIFEST_PATH,
    bounded_path,
    require,
    safe_relative,
)
from .helm_types import HelmValidationError


PRODUCT_LAYOUT_PATH = Path("contracts/helm-product-layout.json")
CENTRAL_ROOT = Path(__file__).resolve().parents[2]
PRODUCT_IDS = {
    "iptv-backend-chart",
    "agent-state-chart",
    "flux-github-actions-runner-chart",
}


def _profiles(value: Any, code: str) -> dict[str, str]:
    require(isinstance(value, Mapping) and bool(value), code)
    profiles: dict[str, str] = {}
    for name, path in value.items():
        require(
            isinstance(name, str) and NAME.fullmatch(name) is not None,
            code,
        )
        profiles[name] = safe_relative(path, code)
    require(list(profiles) == sorted(profiles), code)
    return profiles


def _repositories(value: Any) -> tuple[str, ...]:
    require(isinstance(value, list), "invalid_product_layout")
    repositories: list[str] = []
    for repository in value:
        require(
            isinstance(repository, str)
            and "/" in repository
            and "@" not in repository
            and ":" not in repository.rsplit("/", 1)[-1],
            "invalid_product_layout",
        )
        repositories.append(repository)
    require(repositories == sorted(set(repositories)), "invalid_product_layout")
    return tuple(repositories)


def _product(value: Any) -> tuple[str, dict[str, str], int, tuple[str, ...]]:
    require(
        isinstance(value, Mapping)
        and set(value)
        == {
            "chart_root",
            "values_profiles",
            "minimum_required_image_references",
            "required_image_repositories",
        },
        "invalid_product_layout",
    )
    chart_root = safe_relative(value.get("chart_root"), "invalid_product_layout")
    profiles = _profiles(value.get("values_profiles"), "invalid_product_layout")
    minimum = value.get("minimum_required_image_references")
    require(
        isinstance(minimum, int) and not isinstance(minimum, bool) and 0 <= minimum <= 16,
        "invalid_product_layout",
    )
    repositories = _repositories(value.get("required_image_repositories"))
    require(minimum <= len(repositories), "invalid_product_layout")
    return chart_root, profiles, minimum, repositories


def load_product_layout(root: Path = CENTRAL_ROOT) -> Mapping[str, Any]:
    try:
        payload = json.loads((root / PRODUCT_LAYOUT_PATH).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise HelmValidationError("invalid_product_layout") from error
    require(
        isinstance(payload, Mapping)
        and set(payload) == {"schema_version", "products"}
        and payload.get("schema_version") == 1,
        "invalid_product_layout",
    )
    products = payload.get("products")
    require(
        isinstance(products, Mapping) and set(products) == PRODUCT_IDS,
        "invalid_product_layout",
    )
    for product_id in sorted(PRODUCT_IDS):
        _product(products[product_id])
    return payload


def enforce_product_layout(
    source_root: Path,
    product_id: str,
    policy: Mapping[str, Any],
) -> None:
    products = policy.get("products")
    require(isinstance(products, Mapping), "invalid_product_layout")
    require(product_id in products, "unsupported_product")
    expected_root, expected_profiles, minimum_images, expected_repositories = _product(
        products[product_id]
    )

    manifest_path = bounded_path(
        source_root,
        PRODUCT_MANIFEST_PATH.as_posix(),
        "product_layout_invalid",
    )
    require(
        manifest_path.is_file() and not manifest_path.is_symlink(),
        "product_layout_invalid",
    )
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise HelmValidationError("product_layout_invalid") from error
    require(isinstance(manifest, Mapping), "product_layout_invalid")

    actual_root = safe_relative(
        manifest.get("chart_root"),
        "product_layout_invalid",
    )
    actual_profiles = _profiles(
        manifest.get("values_profiles"),
        "product_layout_invalid",
    )
    images = manifest.get("required_image_references")
    require(
        isinstance(images, list)
        and all(
            isinstance(item, str)
            and IMMUTABLE_IMAGE_REFERENCE.fullmatch(item) is not None
            for item in images
        )
        and images == sorted(set(images)),
        "product_layout_invalid",
    )
    repositories = tuple(item.rsplit("@", 1)[0] for item in images)
    require(
        actual_root == expected_root
        and actual_profiles == expected_profiles
        and len(images) >= minimum_images
        and repositories == expected_repositories,
        "product_layout_mismatch",
    )


__all__ = [
    "PRODUCT_LAYOUT_PATH",
    "enforce_product_layout",
    "load_product_layout",
]
