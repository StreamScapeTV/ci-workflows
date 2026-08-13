"""Merged-inventory and no-follow source guards for Flux infrastructure assets."""
from __future__ import annotations

import json
import re
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

from .flux_assets import FluxAssetError, validate_source_contract

_API_PRODUCT_KIND = {
    "oci.build": "oci-runner-image-family",
    "oci.publish": "oci-runner-image-family",
    "helm.validate": "helm-oci-chart-assets",
    "helm.publish": "helm-oci-chart-assets",
}
_RUNTIME_REPOSITORIES = {"StreamScapeTV/ci-workflows", "StreamScapeTV/flux"}
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_PLATFORM = re.compile(
    r"^[a-z0-9][a-z0-9._-]{0,63}/[a-z0-9][a-z0-9._-]{0,63}"
    r"(?:/[a-z0-9][a-z0-9._-]{0,63})?$"
)


def validate_runtime_repository(repository: str) -> str:
    """Allow only central self-test or the authoritative Flux product repository."""

    if repository not in _RUNTIME_REPOSITORIES:
        raise FluxAssetError(
            "caller_repository_forbidden",
            "Flux infrastructure asset runtime requires central self-test or Flux caller",
        )
    return repository


def _load_json_mapping(value: Any, *, code: str, name: str) -> dict[str, Any]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as error:
            raise FluxAssetError(code, f"{name} is not valid JSON") from error
    if not isinstance(value, Mapping):
        raise FluxAssetError(code, f"{name} must be a JSON object")
    return dict(value)


def validate_oci_build_dependency_evidence(
    dependency_outputs: Mapping[str, Any], *, oci_products_path: Path
) -> dict[str, Any] | None:
    """Validate the exact merged oci.build platform result shape when present."""

    raw = dependency_outputs.get("oci.build")
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise FluxAssetError(
            "oci_build_evidence_invalid", "oci.build evidence must be an object"
        )

    try:
        inventory = json.loads(oci_products_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise FluxAssetError(
            "invalid_oci_product_inventory", "merged OCI product inventory is unavailable"
        ) from error
    if not isinstance(inventory, Mapping) or inventory.get("schema_version") != 1:
        raise FluxAssetError(
            "invalid_oci_product_inventory", "merged OCI product inventory is invalid"
        )
    products = inventory.get("products")
    platform_sets = inventory.get("platform_sets")
    if not isinstance(products, Mapping) or not isinstance(platform_sets, Mapping):
        raise FluxAssetError(
            "invalid_oci_product_inventory", "merged OCI product inventory is incomplete"
        )
    product = products.get("flux-runner-images")
    if (
        not isinstance(product, Mapping)
        or product.get("repository") != "StreamScapeTV/flux"
    ):
        raise FluxAssetError(
            "invalid_oci_product_inventory", "merged Flux OCI product is invalid"
        )
    targets = product.get("targets")
    if not isinstance(targets, list) or not targets:
        raise FluxAssetError(
            "invalid_oci_product_inventory", "merged Flux OCI targets are invalid"
        )

    expected_platforms: dict[str, tuple[str, ...]] = {}
    for target in targets:
        if not isinstance(target, Mapping):
            raise FluxAssetError(
                "invalid_oci_product_inventory", "merged Flux OCI target is invalid"
            )
        target_id = target.get("target_id")
        platform_set = target.get("platform_set")
        platforms = platform_sets.get(platform_set) if isinstance(platform_set, str) else None
        if (
            not isinstance(target_id, str)
            or not target_id
            or not isinstance(platforms, list)
            or not platforms
            or not all(
                isinstance(platform, str) and _PLATFORM.fullmatch(platform) is not None
                for platform in platforms
            )
        ):
            raise FluxAssetError(
                "invalid_oci_product_inventory", "merged Flux OCI platform set is invalid"
            )
        expected_platforms[target_id] = tuple(platforms)

    digests = _load_json_mapping(
        raw.get("image_digest"),
        code="oci_build_evidence_invalid",
        name="oci.build.image_digest",
    )
    platform_rows = _load_json_mapping(
        raw.get("platform_digests_json"),
        code="oci_build_evidence_invalid",
        name="oci.build.platform_digests_json",
    )
    if set(digests) != set(expected_platforms) or set(platform_rows) != set(
        expected_platforms
    ):
        raise FluxAssetError(
            "oci_build_evidence_invalid",
            "oci.build target set differs from merged Flux OCI inventory",
        )

    normalized_platforms: dict[str, list[dict[str, Any]]] = {}
    for target_id in sorted(expected_platforms):
        digest = digests[target_id]
        if not isinstance(digest, str) or _DIGEST.fullmatch(digest) is None:
            raise FluxAssetError(
                "oci_build_evidence_invalid", "oci.build image digest is invalid"
            )
        rows = platform_rows[target_id]
        if not isinstance(rows, list) or not rows or len(rows) > 16:
            raise FluxAssetError(
                "oci_build_evidence_invalid", "oci.build platform rows are invalid"
            )
        normalized_rows: list[dict[str, Any]] = []
        seen: set[str] = set()
        for row in rows:
            if not isinstance(row, Mapping) or set(row) != {
                "platform",
                "manifest_digest",
                "config_digest",
                "layer_digests",
            }:
                raise FluxAssetError(
                    "oci_build_evidence_invalid", "oci.build platform row shape is invalid"
                )
            platform = row.get("platform")
            manifest_digest = row.get("manifest_digest")
            config_digest = row.get("config_digest")
            layers = row.get("layer_digests")
            if (
                not isinstance(platform, str)
                or platform not in expected_platforms[target_id]
                or platform in seen
                or not isinstance(manifest_digest, str)
                or _DIGEST.fullmatch(manifest_digest) is None
                or not isinstance(config_digest, str)
                or _DIGEST.fullmatch(config_digest) is None
                or not isinstance(layers, list)
                or len(layers) > 128
                or not all(
                    isinstance(layer, str) and _DIGEST.fullmatch(layer) is not None
                    for layer in layers
                )
            ):
                raise FluxAssetError(
                    "oci_build_evidence_invalid", "oci.build platform identity is invalid"
                )
            seen.add(platform)
            normalized_rows.append(
                {
                    "platform": platform,
                    "manifest_digest": manifest_digest,
                    "config_digest": config_digest,
                    "layer_digests": list(layers),
                }
            )
        if seen != set(expected_platforms[target_id]):
            raise FluxAssetError(
                "oci_build_evidence_invalid",
                "oci.build platform set differs from merged Flux OCI inventory",
            )
        normalized_platforms[target_id] = sorted(
            normalized_rows, key=lambda item: str(item["platform"])
        )

    return {
        "image_digest": {key: str(digests[key]) for key in sorted(digests)},
        "platform_digests_json": normalized_platforms,
    }


def validate_dependency_product_inventory(
    contract: Mapping[str, Any], *, products_path: Path
) -> dict[str, str]:
    """Require dependency identities and kinds to exist in merged Flux inventory."""

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

    current_flux: dict[str, str] = {}
    for row in rows:
        if not isinstance(row, Mapping):
            raise FluxAssetError(
                "invalid_product_inventory", "merged product inventory row is invalid"
            )
        identifier = row.get("id")
        repository = row.get("repository")
        status = row.get("status")
        kind = row.get("kind")
        if (
            repository == "StreamScapeTV/flux"
            and status == "current"
            and isinstance(identifier, str)
            and identifier
            and isinstance(kind, str)
            and kind
        ):
            current_flux[identifier] = kind
    if not current_flux:
        raise FluxAssetError(
            "invalid_product_inventory", "merged Flux product inventory is empty"
        )

    interfaces = contract.get("dependency_interfaces")
    if not isinstance(interfaces, Mapping) or not interfaces:
        raise FluxAssetError(
            "invalid_contract", "dependency_interfaces must be a non-empty object"
        )
    if set(interfaces) != set(_API_PRODUCT_KIND):
        raise FluxAssetError(
            "invalid_contract", "dependency interface API set is not canonical"
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
        product_kind = current_flux.get(product_id)
        if product_kind is None:
            raise FluxAssetError(
                "dependency_product_unregistered",
                f"{api_name} references an unmerged Flux product identity",
            )
        if product_kind != _API_PRODUCT_KIND[api_name]:
            raise FluxAssetError(
                "dependency_product_kind_mismatch",
                f"{api_name} references the wrong merged Flux product kind",
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
