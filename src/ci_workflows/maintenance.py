"""Domain-neutral organization maintenance public component facade."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Sequence

from .maintenance_artifacts import artifacts as _artifacts
from .maintenance_branches import branches as _branches
from .maintenance_conformance import conformance as _conformance
from .maintenance_contract import MaintenanceContract
from .maintenance_core import MaintenanceApi, OperationResult, render_result
from .maintenance_http import GitHubApi
from .maintenance_projection import (
    project_comment as _project_comment,
    project_labels as _project_labels,
    project_status as _project_status,
)
from .maintenance_retry import runner_retry as _runner_retry


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
    shared_reference_target_sha: str = "",
    dry_run: bool,
    request_id: str,
) -> OperationResult:
    """Produce one bounded inventory report plus review-only repin proposals."""
    return _conformance(
        contract,
        api,
        root=root,
        repository_scope=repository_scope,
        shared_reference_target_sha=shared_reference_target_sha,
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


def project_status(
    contract: MaintenanceContract,
    api: MaintenanceApi,
    *,
    project_id: str,
    expected_sha: str,
    state: str,
    context: str,
    description: str,
    request_id: str,
) -> OperationResult:
    """Project one already-sanitized bounded commit-status decision."""
    return _project_status(
        contract,
        api,
        project_id=project_id,
        expected_sha=expected_sha,
        state=state,
        context=context,
        description=description,
        request_id=request_id,
    )


def project_comment(
    contract: MaintenanceContract,
    api: MaintenanceApi,
    *,
    project_id: str,
    issue_number: int,
    expected_updated_at: str,
    marker: str,
    body: str,
    request_id: str,
) -> OperationResult:
    """Project one deterministic already-sanitized comment decision."""
    return _project_comment(
        contract,
        api,
        project_id=project_id,
        issue_number=issue_number,
        expected_updated_at=expected_updated_at,
        marker=marker,
        body=body,
        request_id=request_id,
    )


def project_labels(
    contract: MaintenanceContract,
    api: MaintenanceApi,
    *,
    project_id: str,
    issue_number: int,
    expected_updated_at: str,
    expected_labels: Sequence[str],
    desired_labels: Sequence[str],
    request_id: str,
) -> OperationResult:
    """Project one exact expected-state bounded label decision."""
    return _project_labels(
        contract,
        api,
        project_id=project_id,
        issue_number=issue_number,
        expected_updated_at=expected_updated_at,
        expected_labels=expected_labels,
        desired_labels=desired_labels,
        request_id=request_id,
    )


__all__ = [
    "GitHubApi",
    "MaintenanceApi",
    "OperationResult",
    "artifacts",
    "branches",
    "conformance",
    "project_comment",
    "project_labels",
    "project_status",
    "render_result",
    "runner_retry",
]
