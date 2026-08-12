"""Reviewed public Flux orchestration component facade."""
from __future__ import annotations

from pathlib import Path

from .flux_reconcile import FluxPlan
from .flux_reconcile import reconcile as _reconcile
from .flux_reconcile import resolve_request as _resolve_request
from .flux_reconcile import verify_health as _verify_health
from .maintenance_contract import MaintenanceContract


def resolve_request(
    contract: MaintenanceContract,
    *,
    source_root: Path,
    source_repository: str,
    admitted_sha: str,
    target_id: str,
    product_id: str,
    operation: str,
    policy_path: str,
    allowlist_path: str,
    request_id: str,
    state_root: Path,
) -> FluxPlan:
    """Resolve one typed request using only exact Flux-owned policy source."""

    return _resolve_request(
        contract,
        source_root=source_root,
        source_repository=source_repository,
        admitted_sha=admitted_sha,
        target_id=target_id,
        product_id=product_id,
        operation=operation,
        policy_path=policy_path,
        allowlist_path=allowlist_path,
        request_id=request_id,
        state_root=state_root,
    )


def reconcile(
    contract: MaintenanceContract,
    plan: FluxPlan,
    *,
    source_root: Path,
    state_root: Path,
    flux_kubeconfig: str,
    flux_sops_age_key: str,
) -> None:
    """Apply one already-resolved plan through the bounded Flux executor."""

    _reconcile(
        contract,
        plan,
        source_root=source_root,
        state_root=state_root,
        flux_kubeconfig=flux_kubeconfig,
        flux_sops_age_key=flux_sops_age_key,
    )


def verify_health(plan: FluxPlan) -> None:
    """Verify the bounded structural health contract for one Flux plan."""

    _verify_health(plan)
