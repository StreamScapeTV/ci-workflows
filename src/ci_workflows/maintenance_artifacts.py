"""Expected-state protected organization artifact cleanup."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

from .maintenance_contract import MaintenanceContract, MaintenanceError
from .maintenance_core import MaintenanceApi, OperationResult, _nested, _positive, _timestamp, load_json_file

def _artifact_snapshot(value: Mapping[str, Any]) -> tuple[Any, ...]:
    return (value.get("id"), value.get("name"), value.get("size_in_bytes"), value.get("created_at"), _nested(value, "workflow_run", "id"))


def _artifact_run_snapshot(value: Mapping[str, Any] | None) -> tuple[Any, ...] | None:
    if value is None:
        return None
    return (value.get("id"), value.get("status"), value.get("conclusion"), value.get("run_attempt"), value.get("updated_at"))


def _retained(value: Mapping[str, Any], exceptions: Mapping[str, Any], now: datetime) -> bool:
    name = value.get("name")
    created = _timestamp(value.get("created_at"))
    rows = exceptions.get("exceptions")
    if not isinstance(name, str) or not isinstance(rows, list):
        raise MaintenanceError("artifact_exception_contract_invalid")
    for row in rows:
        if not isinstance(row, Mapping):
            raise MaintenanceError("artifact_exception_contract_invalid")
        names = row.get("allowed_names")
        days = row.get("maximum_retention_days")
        if isinstance(names, list) and name in names and isinstance(days, int) and days > 0 and now - created <= timedelta(days=days):
            return True
    return False


def artifacts(contract: MaintenanceContract, api: MaintenanceApi, *, root: Path, repository_scope: str, dry_run: bool, request_id: str, now: datetime | None = None) -> OperationResult:
    contract.validate_request_id(request_id)
    policy = contract.operation("artifacts")
    exceptions = load_json_file(root, contract.artifact_exceptions_path)
    if exceptions.get("schema_version") != 1:
        raise MaintenanceError("artifact_exception_contract_invalid")
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    cutoff = current - timedelta(hours=int(policy["minimum_age_hours"]))
    candidates: list[tuple[str, Mapping[str, Any], int | None, tuple[Any, ...] | None]] = []
    result = OperationResult("success", request_id)
    for project in contract.selected_projects(repository_scope):
        for item in api.list_artifacts(project.repository):
            artifact_id = _positive(item.get("id"), "artifact_invalid")
            if _timestamp(item.get("created_at")) > cutoff:
                continue
            if _retained(item, exceptions, current):
                result.decisions.append({"repository": project.repository, "artifact_id": artifact_id, "action": "preserve", "reason": "retained_artifact_exception"})
                continue
            run_id_raw = _nested(item, "workflow_run", "id")
            run_id = _positive(run_id_raw) if run_id_raw is not None else None
            run = api.get_run(project.repository, run_id) if run_id is not None else None
            if run is not None and run.get("status") != "completed":
                result.decisions.append({"repository": project.repository, "artifact_id": artifact_id, "action": "preserve", "reason": "workflow_run_not_completed"})
                continue
            candidates.append((project.repository, item, run_id, _artifact_run_snapshot(run)))
    if len(candidates) > int(policy["maximum_deletions"]):
        raise MaintenanceError("artifact_deletion_bound_exceeded")
    for repository, old, run_id, run_snapshot in candidates:
        artifact_id = int(old["id"])
        fresh = api.get_artifact(repository, artifact_id)
        if fresh is None:
            result.decisions.append({"repository": repository, "artifact_id": artifact_id, "action": "none", "reason": "artifact_already_absent"})
            continue
        if _artifact_snapshot(fresh) != _artifact_snapshot(old):
            raise MaintenanceError("artifact_changed_before_delete")
        if run_id is not None:
            fresh_run = api.get_run(repository, run_id)
            if _artifact_run_snapshot(fresh_run) != run_snapshot:
                raise MaintenanceError("artifact_run_changed_before_delete")
        action = "would_delete" if dry_run else "delete"
        if not dry_run:
            api.delete_artifact(repository, artifact_id)
            result.mutation_count += 1
        result.decisions.append({"repository": repository, "artifact_id": artifact_id, "action": action, "reason": "expired_completed_run_artifact"})
    return result
