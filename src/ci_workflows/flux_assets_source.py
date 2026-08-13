"""Merged-inventory and no-follow source guards for Flux infrastructure assets."""
from __future__ import annotations

import json
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

from .flux_assets import FluxAssetError, validate_source_contract


def validate_dependency_product_inventory(
    contract: Mapping[str, Any], *, products_path: Path
) -> dict[str, str]:
    """Require every dependency product identity to exist in merged Flux inventory."""

    try:
        payload = json.loads(products_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise FluxAssetError(
            "invalid_product_inventory", "merged product inventory is unavailable"
        ) from error
    if not isinstance(payload, Mapping) or payload.get("schema_version") != 1:
        raise FluxAssetError(
            "invalid_product_inventory", "merged product inventory is invalid"
        )
    rows = payload.get("products")
    if not isinstance(rows, list):
        raise FluxAssetError(
            "invalid_product_inventory", "merged product inventory products are invalid"
        )

    current_flux_ids: set[str] = set()
    for row in rows:
        if not isinstance(row, Mapping):
            raise FluxAssetError(
                "invalid_product_inventory", "merged product inventory row is invalid"
            )
        identifier = row.get("id")
        repository = row.get("repository")
        status = row.get("status")
        if (
            repository == "StreamScapeTV/flux"
            and status == "current"
            and isinstance(identifier, str)
            and identifier
        ):
            current_flux_ids.add(identifier)
    if not current_flux_ids:
        raise FluxAssetError(
            "invalid_product_inventory", "merged Flux product inventory is empty"
        )

    interfaces = contract.get("dependency_interfaces")
    if not isinstance(interfaces, Mapping) or not interfaces:
        raise FluxAssetError(
            "invalid_contract", "dependency_interfaces must be a non-empty object"
        )
    resolved: dict[str, str] = {}
    for api_name, raw in interfaces.items():
        if not isinstance(api_name, str) or not isinstance(raw, Mapping):
            raise FluxAssetError(
                "invalid_contract", "dependency interface entry is invalid"
            )
        product_id = raw.get("product_id")
        if not isinstance(product_id, str) or not product_id:
            raise FluxAssetError(
                "invalid_contract", f"{api_name}.product_id is required"
            )
        if product_id not in current_flux_ids:
            raise FluxAssetError(
                "dependency_product_unregistered",
                f"{api_name} references an unmerged Flux product identity",
            )
        resolved[api_name] = product_id
    return dict(sorted(resolved.items()))


def _relative_contract_path(value: Any, *, name: str) -> PurePosixPath:
    if not isinstance(value, str) or not value or "\\" in value:
        raise FluxAssetError("source_path_escape", f"{name} must be a relative POSIX path")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or path.as_posix() != value:
        raise FluxAssetError("source_path_escape", f"{name} escaped admitted source")
    return path


def _resolve_inside(root: Path, relative: PurePosixPath, *, kind: str) -> Path:
    candidate = root.joinpath(*relative.parts)
    if candidate.is_symlink():
        raise FluxAssetError("source_path_escape", f"{kind} may not be a symlink")
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as error:
        raise FluxAssetError("missing_source", f"{relative.as_posix()} is missing") from error
    if root not in resolved.parents:
        raise FluxAssetError("source_path_escape", f"{kind} escaped admitted source")
    return resolved


def validate_source_contract_strict(
    contract: Mapping[str, Any], *, product_id: str, source_root: Path
) -> dict[str, Any]:
    """Validate source without following chart roots/files outside admitted source.

    Runner-image validation delegates to the existing Dockerfile validator, which
    already resolves each Dockerfile strictly beneath the admitted source root.
    The chart path receives the same boundary here before any publication
    dependency can receive credentials.
    """

    products = contract.get("products")
    if not isinstance(products, Mapping):
        raise FluxAssetError("invalid_contract", "products must be an object")
    product = products.get(product_id)
    if not isinstance(product, Mapping):
        raise FluxAssetError("unsupported_product", f"unsupported product {product_id!r}")
    if product.get("kind") != "runner-chart-bundle":
        return validate_source_contract(
            contract, product_id=product_id, source_root=source_root
        )

    try:
        root = source_root.resolve(strict=True)
    except OSError as error:
        raise FluxAssetError("missing_source", "admitted source root is missing") from error
    if source_root.is_symlink() or not root.is_dir():
        raise FluxAssetError("source_path_escape", "admitted source root is invalid")

    chart_relative = _relative_contract_path(product.get("chart_root"), name="chart_root")
    chart_root = _resolve_inside(root, chart_relative, kind="chart root")
    if not chart_root.is_dir():
        raise FluxAssetError("missing_source", "chart root is not a directory")

    required = ("Chart.yaml", "values.yaml", "values.schema.json")
    for name in required:
        relative = PurePosixPath(*chart_relative.parts, name)
        resolved = _resolve_inside(root, relative, kind=f"chart source {name}")
        if chart_root not in resolved.parents or not resolved.is_file():
            raise FluxAssetError("source_path_escape", f"chart source {name} escaped chart root")

    return {
        "kind": "runner-chart-bundle",
        "chart_root": chart_relative.as_posix(),
    }
