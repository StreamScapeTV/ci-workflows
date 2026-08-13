"""Generic bounded projection of already-sanitized GitHub decisions."""
from __future__ import annotations

import re
from typing import Any, Mapping, Sequence

from .maintenance_contract import MaintenanceContract, MaintenanceError
from .maintenance_core import MaintenanceApi, OperationResult, _positive, _timestamp

_STATUS_STATES = {"error", "failure", "pending", "success"}
_CONTEXT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._:/-]{0,99}$")
_MARKER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,79}$")


def _bounded_line(
    value: str,
    *,
    maximum: int,
    code: str,
    allow_empty: bool = False,
) -> str:
    if (
        not isinstance(value, str)
        or (not allow_empty and not value)
        or len(value.encode("utf-8")) > maximum
        or "\r" in value
        or "\n" in value
        or "\x00" in value
    ):
        raise MaintenanceError(code)
    return value


def _bounded_body(value: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value.encode("utf-8")) > 16000
        or "\x00" in value
    ):
        raise MaintenanceError("projection_body_invalid")
    return value


def _labels(value: Sequence[str]) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or len(value) > 20:
        raise MaintenanceError("projection_labels_invalid")
    result: list[str] = []
    for label in value:
        if (
            not isinstance(label, str)
            or not label
            or len(label.encode("utf-8")) > 50
            or any(ord(character) < 32 for character in label)
        ):
            raise MaintenanceError("projection_labels_invalid")
        result.append(label)
    if len(set(result)) != len(result):
        raise MaintenanceError("projection_labels_invalid")
    return tuple(sorted(result))


def _issue_labels(issue: Mapping[str, Any]) -> tuple[str, ...]:
    raw = issue.get("labels")
    if not isinstance(raw, list):
        raise MaintenanceError("projection_issue_invalid")
    names: list[str] = []
    for item in raw:
        if isinstance(item, str):
            names.append(item)
        elif isinstance(item, Mapping) and isinstance(item.get("name"), str):
            names.append(str(item["name"]))
        else:
            raise MaintenanceError("projection_issue_invalid")
    return tuple(sorted(names))


def _issue_snapshot(issue: Mapping[str, Any]) -> tuple[Any, ...]:
    return (
        issue.get("number"),
        issue.get("state"),
        issue.get("updated_at"),
        _issue_labels(issue),
    )


def _status_snapshot(
    statuses: list[Mapping[str, Any]],
    context: str,
) -> tuple[Any, ...] | None:
    matches: list[tuple[Any, ...]] = []
    for status in statuses:
        if status.get("context") != context:
            continue
        status_id = _positive(
            status.get("id"),
            "projection_status_invalid",
        )
        updated_at = status.get("updated_at")
        _timestamp(updated_at)
        matches.append(
            (
                status_id,
                status.get("state"),
                status.get("context"),
                status.get("description"),
                status.get("target_url"),
                updated_at,
            )
        )
    if not matches:
        return None
    return max(
        matches,
        key=lambda value: (_timestamp(value[5]), value[0]),
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
    """Project one bounded commit status after exact-source revalidation."""
    contract.validate_request_id(request_id)
    contract.validate_sha(expected_sha)
    project = contract.project(project_id)
    if state not in _STATUS_STATES or _CONTEXT.fullmatch(context) is None:
        raise MaintenanceError("projection_status_invalid")
    _bounded_line(
        description,
        maximum=140,
        code="projection_status_invalid",
        allow_empty=True,
    )

    commit = api.get_commit(project.repository, expected_sha)
    if commit is None or commit.get("sha") != expected_sha:
        raise MaintenanceError("projection_source_changed")

    statuses = api.list_statuses(project.repository, expected_sha)
    current = _status_snapshot(statuses, context)
    desired = (state, context, description, "")
    if current is not None and current[1:5] == desired:
        return OperationResult(
            "success",
            request_id,
            decisions=[
                {
                    "repository": project.repository,
                    "sha": expected_sha,
                    "action": "none",
                    "reason": "status_unchanged",
                }
            ],
        )

    fresh_commit = api.get_commit(project.repository, expected_sha)
    fresh_statuses = api.list_statuses(project.repository, expected_sha)
    if (
        fresh_commit is None
        or fresh_commit.get("sha") != expected_sha
        or _status_snapshot(fresh_statuses, context) != current
    ):
        raise MaintenanceError("projection_status_changed_before_update")

    api.create_status(
        project.repository,
        expected_sha,
        state=state,
        context=context,
        description=description,
    )
    return OperationResult(
        "success",
        request_id,
        mutation_count=1,
        decisions=[
            {
                "repository": project.repository,
                "sha": expected_sha,
                "action": "status",
                "context": context,
            }
        ],
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
    """Create or update one deterministic marked issue/PR comment."""
    contract.validate_request_id(request_id)
    project = contract.project(project_id)
    number = _positive(issue_number, "projection_issue_invalid")
    _bounded_line(
        expected_updated_at,
        maximum=64,
        code="projection_issue_invalid",
    )
    if _MARKER.fullmatch(marker) is None:
        raise MaintenanceError("projection_marker_invalid")
    rendered = f"<!-- ci-workflows-projection:{marker} -->\n{_bounded_body(body)}"
    prefix = f"<!-- ci-workflows-projection:{marker} -->"

    issue = api.get_issue(project.repository, number)
    if issue is None:
        raise MaintenanceError("projection_issue_missing")
    comments = api.list_issue_comments(project.repository, number)
    matches = [
        comment
        for comment in comments
        if str(comment.get("body", "")).startswith(prefix)
    ]
    if len(matches) > 1:
        raise MaintenanceError("projection_comment_ambiguous")
    if matches and matches[0].get("body") == rendered:
        return OperationResult(
            "success",
            request_id,
            decisions=[
                {
                    "repository": project.repository,
                    "issue_number": number,
                    "action": "none",
                    "reason": "comment_unchanged",
                }
            ],
        )
    if issue.get("updated_at") != expected_updated_at:
        raise MaintenanceError("projection_issue_changed")

    fresh_issue = api.get_issue(project.repository, number)
    fresh_comments = api.list_issue_comments(project.repository, number)
    fresh_matches = [
        comment
        for comment in fresh_comments
        if str(comment.get("body", "")).startswith(prefix)
    ]
    if (
        fresh_issue is None
        or _issue_snapshot(fresh_issue) != _issue_snapshot(issue)
        or len(fresh_matches) != len(matches)
        or (matches and fresh_matches[0].get("id") != matches[0].get("id"))
        or (
            matches
            and fresh_matches[0].get("body") != matches[0].get("body")
        )
    ):
        raise MaintenanceError("projection_comment_changed_before_update")

    if matches:
        comment_id = _positive(
            matches[0].get("id"),
            "projection_comment_invalid",
        )
        api.update_issue_comment(project.repository, comment_id, rendered)
        action = "update_comment"
    else:
        api.create_issue_comment(project.repository, number, rendered)
        action = "create_comment"
    return OperationResult(
        "success",
        request_id,
        mutation_count=1,
        decisions=[
            {
                "repository": project.repository,
                "issue_number": number,
                "action": action,
            }
        ],
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
    """Replace the complete label set from an exact expected issue snapshot."""
    contract.validate_request_id(request_id)
    project = contract.project(project_id)
    number = _positive(issue_number, "projection_issue_invalid")
    _bounded_line(
        expected_updated_at,
        maximum=64,
        code="projection_issue_invalid",
    )
    expected = _labels(expected_labels)
    desired = _labels(desired_labels)

    issue = api.get_issue(project.repository, number)
    if issue is None:
        raise MaintenanceError("projection_issue_missing")
    current = _issue_labels(issue)
    if current == desired:
        return OperationResult(
            "success",
            request_id,
            decisions=[
                {
                    "repository": project.repository,
                    "issue_number": number,
                    "action": "none",
                    "reason": "labels_unchanged",
                }
            ],
        )
    if current != expected or issue.get("updated_at") != expected_updated_at:
        raise MaintenanceError("projection_issue_changed")

    fresh = api.get_issue(project.repository, number)
    if fresh is None or _issue_snapshot(fresh) != _issue_snapshot(issue):
        raise MaintenanceError("projection_labels_changed_before_update")
    api.set_issue_labels(project.repository, number, list(desired))
    return OperationResult(
        "success",
        request_id,
        mutation_count=1,
        decisions=[
            {
                "repository": project.repository,
                "issue_number": number,
                "action": "set_labels",
            }
        ],
    )
