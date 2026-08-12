"""Central exact dependency allowlist for Helm product planning."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlsplit

from .helm_contract import NAME, SEMVER, require
from .helm_contract import resolve_validation_plan as _resolve_validation_plan
from .helm_types import HelmPlan, HelmRequest, HelmValidationError


DEPENDENCY_POLICY_PATH = Path("contracts/helm-dependency-policy.json")
CENTRAL_ROOT = Path(__file__).resolve().parents[2]
PRODUCT_IDS = {
    "iptv-backend-chart",
    "agent-state-chart",
    "flux-github-actions-runner-chart",
}


def _repository(value: Any) -> str:
    require(isinstance(value, str), "invalid_dependency_policy")
    if value.startswith("oci://"):
        parsed = value.removeprefix("oci://")
        require(
            bool(parsed)
            and "/" in parsed
            and "@" not in parsed
            and "?" not in parsed
            and "#" not in parsed,
            "invalid_dependency_policy",
        )
        return value
    parsed = urlsplit(value)
    require(
        parsed.scheme == "https"
        and bool(parsed.hostname)
        and parsed.username is None
        and parsed.password is None
        and not parsed.query
        and not parsed.fragment
        and parsed.path not in {"", "/"},
        "invalid_dependency_policy",
    )
    return value


def _rows(value: Any) -> tuple[tuple[str, str, str], ...]:
    require(isinstance(value, list), "invalid_dependency_policy")
    rows: list[tuple[str, str, str]] = []
    for item in value:
        require(
            isinstance(item, Mapping)
            and set(item) == {"name", "repository", "version"},
            "invalid_dependency_policy",
        )
        name = item.get("name")
        version = item.get("version")
        require(
            isinstance(name, str) and NAME.fullmatch(name) is not None,
            "invalid_dependency_policy",
        )
        require(
            isinstance(version, str) and SEMVER.fullmatch(version) is not None,
            "invalid_dependency_policy",
        )
        rows.append((name, version, _repository(item.get("repository"))))
    require(rows == sorted(set(rows)), "invalid_dependency_policy")
    return tuple(rows)


def load_dependency_policy(root: Path = CENTRAL_ROOT) -> Mapping[str, Any]:
    try:
        payload = json.loads(
            (root / DEPENDENCY_POLICY_PATH).read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError) as error:
        raise HelmValidationError("invalid_dependency_policy") from error
    require(
        isinstance(payload, Mapping)
        and set(payload) == {"schema_version", "products"}
        and payload.get("schema_version") == 1,
        "invalid_dependency_policy",
    )
    products = payload.get("products")
    require(
        isinstance(products, Mapping) and set(products) == PRODUCT_IDS,
        "invalid_dependency_policy",
    )
    for product_id in sorted(PRODUCT_IDS):
        _rows(products[product_id])
    return payload


def enforce_dependency_policy(
    plan: HelmPlan,
    policy: Mapping[str, Any],
) -> HelmPlan:
    products = policy.get("products")
    require(isinstance(products, Mapping), "invalid_dependency_policy")
    require(plan.product.product_id in products, "unsupported_product")
    expected = _rows(products[plan.product.product_id])
    require(
        plan.product.locked_dependencies == expected,
        "dependency_policy_mismatch",
    )
    return plan


def resolve_validation_plan(
    source_root: Path,
    contract: Mapping[str, Any],
    request: HelmRequest,
    *,
    contract_root: Path = CENTRAL_ROOT,
) -> HelmPlan:
    """Resolve caller product intent and enforce the central dependency tuples."""

    require(
        contract.get("dependency_policy_contract")
        == DEPENDENCY_POLICY_PATH.as_posix(),
        "invalid_contract",
    )
    plan = _resolve_validation_plan(source_root, contract, request)
    return enforce_dependency_policy(plan, load_dependency_policy(contract_root))


__all__ = [
    "DEPENDENCY_POLICY_PATH",
    "enforce_dependency_policy",
    "load_dependency_policy",
    "resolve_validation_plan",
]
