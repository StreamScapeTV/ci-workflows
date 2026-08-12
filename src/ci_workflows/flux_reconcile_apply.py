"""Revalidate and apply one structured Flux reconciliation plan."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from .maintenance_contract import MaintenanceContract, MaintenanceError
from .flux_reconcile_fs import _state_directory, _write_secret
from .flux_reconcile_model import FluxPlan, _args, _clean, _git, _regular, _sha256

def _revalidate(contract: MaintenanceContract, plan: FluxPlan, source: Path) -> Path:
    policy = contract.operation("flux_reconcile")
    if _git(source, "rev-parse", "HEAD") != plan.admitted_sha or not _clean(source):
        raise MaintenanceError("flux_source_changed_before_apply")
    paths = [
        _regular(source, str(policy[field]))
        for field in ("policy_path", "allowlist_path", "executor_path")
    ]
    if [_sha256(path) for path in paths] != [
        plan.policy_sha256,
        plan.allowlist_sha256,
        plan.executor_sha256,
    ]:
        raise MaintenanceError("flux_source_changed_before_apply")
    return paths[2]


def reconcile(
    contract: MaintenanceContract,
    plan: FluxPlan,
    *,
    source_root: Path,
    state_root: Path,
    flux_kubeconfig: str,
    flux_sops_age_key: str,
) -> None:
    executor = _revalidate(contract, plan, source_root)
    if not flux_kubeconfig.strip() or not flux_sops_age_key.strip():
        raise MaintenanceError("flux_credentials_missing")
    if shutil.which("flux") is None or shutil.which("kubectl") is None:
        raise MaintenanceError("flux_tooling_missing")
    state = _state_directory(state_root)
    kubeconfig = state / "kubeconfig"
    age_key = state / "sops-age-key"
    if kubeconfig.exists() or kubeconfig.is_symlink() or age_key.exists() or age_key.is_symlink():
        raise MaintenanceError("flux_state_invalid")

    primary_error: MaintenanceError | None = None
    cleanup_failed = False
    try:
        _write_secret(kubeconfig, flux_kubeconfig)
        _write_secret(age_key, flux_sops_age_key)
        environment = {
            "PATH": os.environ.get("PATH", ""),
            "PYTHONDONTWRITEBYTECODE": "1",
            "KUBECONFIG": str(kubeconfig),
            "SOPS_AGE_KEY_FILE": str(age_key),
        }
        try:
            completed = subprocess.run(
                [sys.executable, str(executor), *_args(plan)],
                cwd=source_root,
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=3600,
                env=environment,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise MaintenanceError("flux_reconciliation_failed") from error
        if completed.returncode:
            raise MaintenanceError("flux_reconciliation_failed")
        verify_health(plan)
        if _git(source_root, "rev-parse", "HEAD") != plan.admitted_sha or not _clean(source_root):
            raise MaintenanceError("flux_source_mutated_during_apply")
    except MaintenanceError as error:
        primary_error = error
    finally:
        for path in (kubeconfig, age_key):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                cleanup_failed = True
            if path.exists() or path.is_symlink():
                cleanup_failed = True
    if cleanup_failed:
        raise MaintenanceError("flux_credential_cleanup_failed")
    if primary_error is not None:
        raise primary_error


def verify_health(plan: FluxPlan) -> None:
    if plan.operation == "deploy" and not any(
        (plan.flux_source, plan.kustomization, plan.helm_release)
    ):
        raise MaintenanceError("flux_health_contract_invalid")
    if plan.operation == "restart" and not plan.workloads:
        raise MaintenanceError("flux_health_contract_invalid")


def plan_summary(plan: FluxPlan, *, dry_run: bool) -> dict[str, str]:
    return {
        "result": "success",
        "reconciliation_state": "dry-run" if dry_run else "authorized",
        "request_id": plan.request_id,
    }
