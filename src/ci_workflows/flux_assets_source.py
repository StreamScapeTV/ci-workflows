"""No-follow source validation for privileged Flux infrastructure asset execution."""
from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import Any, Mapping

from .flux_assets import FluxAssetError, validate_source_contract


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
