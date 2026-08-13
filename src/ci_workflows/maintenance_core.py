"""Shared types and validation helpers for organization maintenance."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence

from .maintenance_contract import MaintenanceError


@dataclass
class OperationResult:
    result: str
    request_id: str
    mutation_count: int = 0
    retry_run_id: str = ""
    report_issue_url: str = ""
    decisions: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def succeeded(self) -> bool:
        return self.result == "success" and not self.errors


class MaintenanceApi(Protocol):
    def list_artifacts(self, repository: str) -> list[Mapping[str, Any]]: ...
    def get_artifact(self, repository: str, artifact_id: int) -> Mapping[str, Any] | None: ...
    def delete_artifact(self, repository: str, artifact_id: int) -> None: ...
    def get_run(self, repository: str, run_id: int) -> Mapping[str, Any] | None: ...
    def get_pull(self, repository: str, number: int) -> Mapping[str, Any] | None: ...
    def list_closed_pulls(self, repository: str, base: str) -> list[Mapping[str, Any]]: ...
    def get_branch(self, repository: str, branch: str) -> Mapping[str, Any] | None: ...
    def delete_branch(self, repository: str, branch: str) -> None: ...
    def get_commit(self, repository: str, sha: str) -> Mapping[str, Any] | None: ...
    def list_statuses(self, repository: str, sha: str) -> list[Mapping[str, Any]]: ...
    def create_status(
        self,
        repository: str,
        sha: str,
        *,
        state: str,
        context: str,
        description: str,
    ) -> Mapping[str, Any]: ...
    def list_workflow_files(self, repository: str) -> list[str]: ...
    def get_file_text(self, repository: str, path: str, ref: str) -> str | None: ...
    def get_issue(self, repository: str, number: int) -> Mapping[str, Any] | None: ...
    def list_open_issues(self, repository: str) -> list[Mapping[str, Any]]: ...
    def create_issue(self, repository: str, title: str, body: str) -> Mapping[str, Any]: ...
    def update_issue(self, repository: str, number: int, title: str, body: str) -> Mapping[str, Any]: ...
    def list_issue_comments(self, repository: str, number: int) -> list[Mapping[str, Any]]: ...
    def create_issue_comment(self, repository: str, number: int, body: str) -> Mapping[str, Any]: ...
    def update_issue_comment(self, repository: str, comment_id: int, body: str) -> Mapping[str, Any]: ...
    def set_issue_labels(
        self,
        repository: str,
        number: int,
        labels: Sequence[str],
    ) -> Mapping[str, Any]: ...
    def list_attempt_jobs(self, repository: str, run_id: int, attempt: int) -> list[Mapping[str, Any]]: ...
    def download_job_logs(self, repository: str, job_id: int, maximum_bytes: int) -> str: ...
    def rerun_failed_jobs(self, repository: str, run_id: int) -> None: ...


def _nested(value: Any, *keys: str) -> Any:
    for key in keys:
        if not isinstance(value, Mapping):
            return None
        value = value.get(key)
    return value


def _positive(value: Any, code: str = "github_response_invalid") -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise MaintenanceError(code)
    return value


def _timestamp(value: Any) -> datetime:
    if not isinstance(value, str) or not value:
        raise MaintenanceError("github_timestamp_invalid")
    try:
        parsed = datetime.fromisoformat(
            value[:-1] + "+00:00" if value.endswith("Z") else value
        )
    except ValueError as error:
        raise MaintenanceError("github_timestamp_invalid") from error
    if parsed.tzinfo is None:
        raise MaintenanceError("github_timestamp_invalid")
    return parsed.astimezone(timezone.utc)


def load_json_file(root: Path, relative: str) -> Mapping[str, Any]:
    try:
        payload = json.loads((root / relative).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise MaintenanceError("supporting_contract_invalid") from error
    if not isinstance(payload, Mapping):
        raise MaintenanceError("supporting_contract_invalid")
    return payload


def _inventory_repo(inventory: Mapping[str, Any], repository: str) -> Mapping[str, Any]:
    rows = inventory.get("repositories")
    if not isinstance(rows, list):
        raise MaintenanceError("workflow_inventory_invalid")
    for row in rows:
        if isinstance(row, Mapping) and row.get("repository") == repository:
            return row
    raise MaintenanceError("workflow_inventory_missing_repository")


def _workflow_rows(repository: Mapping[str, Any]) -> dict[str, tuple[Any, ...]]:
    rows = repository.get("workflows")
    if not isinstance(rows, list):
        raise MaintenanceError("workflow_inventory_invalid")
    output: dict[str, tuple[Any, ...]] = {}
    for row in rows:
        if (
            not isinstance(row, list)
            or len(row) != 7
            or not isinstance(row[0], str)
            or row[0] in output
        ):
            raise MaintenanceError("workflow_inventory_invalid")
        output[row[0]] = tuple(row)
    return output


def render_result(value: OperationResult) -> dict[str, str]:
    return {
        "result": "success" if value.succeeded else "failure",
        "mutation_count": str(value.mutation_count),
        "retry_run_id": value.retry_run_id,
        "report_issue_url": value.report_issue_url,
        "request_id": value.request_id,
        "decision_count": str(len(value.decisions)),
    }
