"""Central product-owned Helm policy hook admission and execution."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from .helm_contract import PRODUCT_MANIFEST_PATH, bounded_path, require, safe_relative
from .helm_execution import _run, _runtime_environment, verify_exact_source
from .helm_types import HelmPlan, HelmValidationError


POLICY_HOOKS_PATH = Path("contracts/helm-policy-hooks.json")
CENTRAL_ROOT = Path(__file__).resolve().parents[2]
PRODUCT_IDS = {
    "iptv-backend-chart",
    "agent-state-chart",
    "flux-github-actions-runner-chart",
}


def _policy_path(value: Any) -> str | None:
    require(value is None or isinstance(value, str), "invalid_policy_hook_contract")
    if value is None:
        return None
    path = safe_relative(value, "invalid_policy_hook_contract")
    require(path.endswith(".sh"), "invalid_policy_hook_contract")
    return path


def load_policy_hook_contract(root: Path = CENTRAL_ROOT) -> Mapping[str, Any]:
    try:
        payload = json.loads((root / POLICY_HOOKS_PATH).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise HelmValidationError("invalid_policy_hook_contract") from error
    require(
        isinstance(payload, Mapping)
        and set(payload) == {"schema_version", "products"}
        and payload.get("schema_version") == 1,
        "invalid_policy_hook_contract",
    )
    products = payload.get("products")
    require(
        isinstance(products, Mapping) and set(products) == PRODUCT_IDS,
        "invalid_policy_hook_contract",
    )
    for product_id in sorted(PRODUCT_IDS):
        _policy_path(products[product_id])
    return payload


def enforce_policy_hook(
    source_root: Path,
    product_id: str,
    policy: Mapping[str, Any],
) -> None:
    products = policy.get("products")
    require(isinstance(products, Mapping), "invalid_policy_hook_contract")
    require(product_id in products, "unsupported_product")
    expected = _policy_path(products[product_id])

    manifest_path = bounded_path(
        source_root,
        PRODUCT_MANIFEST_PATH.as_posix(),
        "policy_hook_invalid",
    )
    require(
        manifest_path.is_file() and not manifest_path.is_symlink(),
        "policy_hook_invalid",
    )
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise HelmValidationError("policy_hook_invalid") from error
    require(isinstance(manifest, Mapping), "policy_hook_invalid")
    actual = manifest.get("policy_path")
    require(actual == expected, "policy_hook_policy_mismatch")
    if expected is not None:
        hook = bounded_path(source_root, expected, "policy_hook_invalid")
        require(hook.is_file() and not hook.is_symlink(), "policy_hook_invalid")


def run_policy_hook(
    source_root: Path,
    state_root: Path,
    plan: HelmPlan,
    admitted_sha: str,
    inherited: Mapping[str, str],
) -> int:
    """Run one centrally approved checked-in shell hook with scrubbed state."""

    if plan.policy_path is None:
        return 0
    hook = bounded_path(source_root, plan.policy_path, "policy_hook_invalid")
    require(hook.is_file() and not hook.is_symlink(), "policy_hook_invalid")
    work_chart = state_root / "helm-validation" / "work" / plan.product.chart_name
    require(
        work_chart.is_dir() and not work_chart.is_symlink(),
        "policy_hook_invalid",
    )
    work_values = bounded_path(
        work_chart,
        plan.values_path,
        "policy_hook_invalid",
    )
    require(
        work_values.is_file() and not work_values.is_symlink(),
        "policy_hook_invalid",
    )

    environment = _runtime_environment(inherited, state_root)
    environment.update(
        {
            "CIW_HELM_PRODUCT_ID": plan.product.product_id,
            "CIW_HELM_CHART_ROOT": str(work_chart),
            "CIW_HELM_VALUES_PATH": str(work_values),
            "CIW_HELM_VALUES_PROFILE": plan.values_profile,
            "CIW_HELM_RELEASE_VERSION": plan.release_version or "",
        }
    )
    verify_exact_source(source_root, admitted_sha, environment)
    _run(
        ["bash", "--noprofile", "--norc", str(hook)],
        cwd=work_chart,
        environment=environment,
        timeout=120,
        code="policy_hook_failed",
    )
    verify_exact_source(source_root, admitted_sha, environment)
    return 1


__all__ = [
    "POLICY_HOOKS_PATH",
    "enforce_policy_hook",
    "load_policy_hook_contract",
    "run_policy_hook",
]
