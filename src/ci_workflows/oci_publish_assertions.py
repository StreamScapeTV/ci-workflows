"""Reuse the merged OCI build filesystem assertions for publication read-back."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from . import oci_contract
from . import oci_execution_safe
from .oci_publish import OciPublishError, PublishPlan, PublishTarget
from .oci_types import OciBuildError, OciTarget


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise OciPublishError(code)


def _mapping(value: Any) -> Mapping[str, Any]:
    _require(isinstance(value, Mapping), "invalid_contract")
    return value


def _build_target(
    repository_root: Path,
    plan: PublishPlan,
    target: PublishTarget,
) -> OciTarget:
    """Resolve one publication target through the authoritative #16 parser."""

    try:
        payload = json.loads(
            (repository_root / "contracts/oci-products.json").read_text(
                encoding="utf-8"
            )
        )
    except (OSError, json.JSONDecodeError) as error:
        raise OciPublishError("publication_dependency_missing") from error
    contract = _mapping(payload)
    platform_sets = _mapping(contract.get("platform_sets"))
    products = _mapping(contract.get("products"))
    product = _mapping(products.get(plan.product_id))
    raw_targets = product.get("targets")
    _require(isinstance(raw_targets, list) and bool(raw_targets), "invalid_contract")
    matches = [
        _mapping(raw)
        for raw in raw_targets
        if isinstance(raw, Mapping) and raw.get("target_id") == target.target_id
    ]
    _require(len(matches) == 1, "invalid_contract")
    try:
        build_target = oci_contract._validate_target(  # noqa: SLF001
            matches[0], platform_sets
        )
    except OciBuildError as error:
        raise OciPublishError(error.code) from error
    _require(build_target.platforms == target.platforms, "platform_mismatch")
    return build_target


def assert_filesystem_contract(
    repository_root: Path,
    plan: PublishPlan,
    target: PublishTarget,
    layout: Path,
) -> None:
    """Prove required/forbidden filesystem state on a verified OCI layout."""

    build_target = _build_target(repository_root, plan, target)
    if not (
        build_target.required_files
        or build_target.required_tools
        or build_target.forbidden_tools
    ):
        return
    try:
        oci_execution_safe._assert_target_filesystem(  # noqa: SLF001
            layout, build_target
        )
    except OciBuildError as error:
        raise OciPublishError(error.code) from error
