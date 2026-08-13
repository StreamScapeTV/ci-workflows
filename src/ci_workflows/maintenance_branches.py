"""Exact merged-branch hygiene."""
from __future__ import annotations

from typing import Any, Mapping

from .maintenance_contract import (
    MaintenanceContract,
    MaintenanceError,
    ProjectPolicy,
)
from .maintenance_core import MaintenanceApi, OperationResult, _nested, _positive


def _pull_snapshot(value: Mapping[str, Any]) -> tuple[Any, ...]:
    return (
        value.get("number"),
        value.get("state"),
        value.get("merged_at"),
        _nested(value, "head", "sha"),
        _nested(value, "head", "ref"),
        _nested(value, "head", "repo", "full_name"),
        _nested(value, "base", "ref"),
        _nested(value, "base", "repo", "full_name"),
    )


def _select_pull(
    api: MaintenanceApi,
    project: ProjectPolicy,
    pr_number: int | None,
    sha: str,
    prefix: str,
) -> Mapping[str, Any]:
    if pr_number is not None:
        if isinstance(pr_number, bool) or not isinstance(pr_number, int) or pr_number <= 0:
            raise MaintenanceError("invalid_pull_request_number")
        pull = api.get_pull(project.repository, pr_number)
        pulls = [] if pull is None else [pull]
    else:
        pulls = api.list_closed_pulls(
            project.repository,
            project.integration_branch,
        )
    matches = [
        pull
        for pull in pulls
        if isinstance(pull, Mapping)
        and pull.get("state") == "closed"
        and pull.get("merged_at")
        and _nested(pull, "head", "sha") == sha
        and _nested(pull, "head", "repo", "full_name") == project.repository
        and _nested(pull, "base", "ref") == project.integration_branch
        and _nested(pull, "base", "repo", "full_name") == project.repository
        and isinstance(_nested(pull, "head", "ref"), str)
        and str(_nested(pull, "head", "ref")).startswith(prefix)
    ]
    if len(matches) != 1:
        raise MaintenanceError("exact_merged_pull_not_found")
    return matches[0]


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
    contract.validate_request_id(request_id)
    contract.validate_sha(expected_head_sha)
    project = contract.project(project_id)
    pull = _select_pull(
        api,
        project,
        pr_number,
        expected_head_sha,
        str(contract.operation("branches")["branch_prefix"]),
    )
    pull_number = _positive(pull.get("number"), "pull_request_invalid")
    branch = _nested(pull, "head", "ref")
    if not isinstance(branch, str) or not branch:
        raise MaintenanceError("pull_request_invalid")
    result = OperationResult("success", request_id)
    current = api.get_branch(project.repository, branch)
    if current is None:
        result.decisions.append(
            {
                "repository": project.repository,
                "branch": branch,
                "action": "none",
                "reason": "branch_already_absent",
            }
        )
        return result
    if branch == project.integration_branch or current.get("protected") is True:
        raise MaintenanceError("protected_branch_rejected")
    if _nested(current, "commit", "sha") != expected_head_sha:
        raise MaintenanceError("branch_changed_before_delete")
    if dry_run:
        result.decisions.append(
            {
                "repository": project.repository,
                "branch": branch,
                "action": "would_delete",
                "reason": "exact_tip_merged_by_pull_request",
            }
        )
        return result
    fresh_pull = api.get_pull(project.repository, pull_number)
    fresh_branch = api.get_branch(project.repository, branch)
    if (
        fresh_pull is None
        or _pull_snapshot(fresh_pull) != _pull_snapshot(pull)
        or fresh_branch is None
        or fresh_branch.get("protected") is True
        or _nested(fresh_branch, "commit", "sha") != expected_head_sha
    ):
        raise MaintenanceError("branch_changed_before_delete")
    api.delete_branch(project.repository, branch)
    if api.get_branch(project.repository, branch) is not None:
        raise MaintenanceError("branch_delete_verification_failed")
    result.mutation_count = 1
    result.decisions.append(
        {
            "repository": project.repository,
            "branch": branch,
            "action": "delete",
            "reason": "exact_tip_merged_by_pull_request",
        }
    )
    return result
