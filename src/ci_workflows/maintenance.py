"""Domain-neutral organization maintenance public component facade."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from .maintenance_artifacts import artifacts as _artifacts
from .maintenance_branches import branches as _branches
from .maintenance_conformance import conformance as _conformance
from .maintenance_core import MaintenanceApi, OperationResult, render_result
from .maintenance_http import GitHubApi
from .maintenance_http_transport import _SafeRedirectHandler
from .maintenance_retry import runner_retry as _runner_retry
from .maintenance_contract import MaintenanceContract


def artifacts(
    contract: MaintenanceContract,
    api: MaintenanceApi,
    *,
    root: Path,
    repository_scope: str,
    dry_run: bool,
    request_id: str,
    now: datetime | None = None,
) -> OperationResult:
    """Run expected-state protected organization artifact cleanup."""

    return _artifacts(
        contract,
        api,
        root=root,
        repository_scope=repository_scope,
        dry_run=dry_run,
        request_id=request_id,
        now=now,
    )


def branches(
    contract: MaintenanceContract,
    api: MaintenanceApi,
    *,
    project_id: str,
    pr_number: int | None,
    expected_head_sha: str,
    dry_run: bool,
    request_id: str,
) -> OperationResult:
    """Delete only an exact unchanged issue branch proven merged."""

    return _branches(
        contract,
        api,
        project_id=project_id,
        pr_number=pr_number,
        expected_head_sha=expected_head_sha,
        dry_run=dry_run,
        request_id=request_id,
    )


def conformance(
    contract: MaintenanceContract,
    api: MaintenanceApi,
    *,
    root: Path,
    repository_scope: str,
    dry_run: bool,
    request_id: str,
) -> OperationResult:
    """Produce or maintain one bounded organization conformance report."""

    return _conformance(
        contract,
        api,
        root=root,
        repository_scope=repository_scope,
        dry_run=dry_run,
        request_id=request_id,
    )


def runner_retry(
    contract: MaintenanceContract,
    api: MaintenanceApi,
    *,
    root: Path,
    project_id: str,
    run_id: int,
    expected_head_sha: str,
    dry_run: bool,
    request_id: str,
) -> OperationResult:
    """Rerun only a current attempt-1 proven infrastructure failure."""

    return _runner_retry(
        contract,
        api,
        root=root,
        project_id=project_id,
        run_id=run_id,
        expected_head_sha=expected_head_sha,
        dry_run=dry_run,
        request_id=request_id,
    )


__all__ = [
    "GitHubApi",
    "MaintenanceApi",
    "OperationResult",
    "artifacts",
    "branches",
    "conformance",
    "render_result",
    "runner_retry",
]
