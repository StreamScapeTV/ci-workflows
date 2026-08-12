"""Resolve one structured reconciliation plan from exact Flux-owned policy source."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from .maintenance_contract import MaintenanceContract, MaintenanceError
from .flux_reconcile_fs import _exclusive_write, _state_directory
from .flux_reconcile_model import FluxPlan, _DNS, _PRODUCT, _clean, _git, _read_plan, _regular, _sha256

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
    """Run only the exact Flux-owned policy adapter and validate its plan."""
    contract.validate_request_id(request_id)
    contract.validate_sha(admitted_sha)
    policy = contract.operation("flux_reconcile")
    if source_repository != policy["repository"]:
        raise MaintenanceError("flux_repository_rejected")
    if operation not in policy["allowed_operations"]:
        raise MaintenanceError("flux_operation_rejected")
    if _DNS.fullmatch(target_id) is None or _PRODUCT.fullmatch(product_id) is None:
        raise MaintenanceError("flux_identifier_rejected")
    if policy_path != policy["policy_path"] or allowlist_path != policy["allowlist_path"]:
        raise MaintenanceError("flux_policy_path_rejected")
    if _git(source_root, "rev-parse", "HEAD") != admitted_sha or not _clean(source_root):
        raise MaintenanceError("flux_source_identity_invalid")

    policy_file = _regular(source_root, policy_path)
    allowlist = _regular(source_root, allowlist_path)
    executor = _regular(source_root, str(policy["executor_path"]))
    state = _state_directory(state_root)
    request_file = state / "request.json"
    plan_file = state / "plan.json"
    if request_file.exists() or request_file.is_symlink() or plan_file.exists() or plan_file.is_symlink():
        raise MaintenanceError("flux_state_invalid")
    _exclusive_write(
        request_file,
        json.dumps(
            {
                "schema_version": 1,
                "target_id": target_id,
                "product_id": product_id,
                "operation": operation,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n",
        code="flux_state_invalid",
    )
    try:
        completed = subprocess.run(
            [
                sys.executable,
                str(policy_file),
                "--central-interface",
                str(policy["policy_interface"]),
                "--central-request",
                str(request_file),
                "--allowlist",
                str(allowlist),
                "--output-plan",
                str(plan_file),
            ],
            cwd=source_root,
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=60,
            env={
                "PATH": os.environ.get("PATH", ""),
                "PYTHONDONTWRITEBYTECODE": "1",
            },
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise MaintenanceError("flux_policy_rejected") from error
    if completed.returncode:
        raise MaintenanceError("flux_policy_rejected")
    plan = _read_plan(
        plan_file,
        admitted_sha=admitted_sha,
        target_id=target_id,
        product_id=product_id,
        operation=operation,
        request_id=request_id,
        policy_sha256=_sha256(policy_file),
        allowlist_sha256=_sha256(allowlist),
        executor_sha256=_sha256(executor),
    )
    if _git(source_root, "rev-parse", "HEAD") != admitted_sha or not _clean(source_root):
        raise MaintenanceError("flux_source_mutated_by_policy")
    return plan
