"""Expected-state protected organization artifact cleanup."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

from .maintenance_contract import MaintenanceContract, MaintenanceError
from .maintenance_core import (
    MaintenanceApi,
    OperationResult,
    _nested,
    _positive,
    _timestamp,
    load_json_file,
)

Candidate = tuple[
    str,
    Mapping[str, Any],
    int | None,
    tuple[Any, ...] | None,
    str,
]
RetentionCandidate = tuple[
    str,
    Mapping[str, Any],
    int | None,
    tuple[Any, ...] | None,
]


def _artifact_snapshot(value: Mapping[str, Any]) -> tuple[Any, ...]:
    return (
        value.get("id"),
        value.get("name"),
        value.get("size_in_bytes"),
        value.get("created_at"),
        _nested(value, "workflow_run", "id"),
    )


def _artifact_run_snapshot(
    value: Mapping[str, Any] | None,
) -> tuple[Any, ...] | None:
    if value is None:
        return None
    return (
        value.get("id"),
        value.get("status"),
        value.get("conclusion"),
        value.get("run_attempt"),
        value.get("updated_at"),
    )


def _artifact_size(value: Mapping[str, Any]) -> int:
    size = value.get("size_in_bytes")
    if isinstance(size, bool) or not isinstance(size, int) or size < 0:
        raise MaintenanceError("artifact_invalid")
    return size


def _exception_rows(exceptions: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    rows = exceptions.get("exceptions")
    if (
        exceptions.get("schema_version") != 1
        or exceptions.get("default") != "zero-artifacts"
        or not isinstance(rows, list)
    ):
        raise MaintenanceError("artifact_exception_contract_invalid")
    result: list[Mapping[str, Any]] = []
    seen_ids: set[str] = set()
    seen_names: set[str] = set()
    expected = {
        "id",
        "issue",
        "allowed_names",
        "trust_modes",
        "maximum_count",
        "maximum_total_bytes",
        "maximum_retention_days",
        "reason",
    }
    for row in rows:
        if not isinstance(row, Mapping) or set(row) != expected:
            raise MaintenanceError("artifact_exception_contract_invalid")
        identifier = row.get("id")
        issue = row.get("issue")
        names = row.get("allowed_names")
        trust_modes = row.get("trust_modes")
        count = row.get("maximum_count")
        total_bytes = row.get("maximum_total_bytes")
        days = row.get("maximum_retention_days")
        reason = row.get("reason")
        if (
            not isinstance(identifier, str)
            or not identifier
            or identifier in seen_ids
            or isinstance(issue, bool)
            or not isinstance(issue, int)
            or issue <= 0
            or not isinstance(names, list)
            or not names
            or any(not isinstance(name, str) or not name for name in names)
            or len(names) != len(set(names))
            or any(name in seen_names for name in names)
            or not isinstance(trust_modes, list)
            or not trust_modes
            or any(
                not isinstance(mode, str) or not mode for mode in trust_modes
            )
            or len(trust_modes) != len(set(trust_modes))
            or isinstance(count, bool)
            or not isinstance(count, int)
            or count <= 0
            or isinstance(total_bytes, bool)
            or not isinstance(total_bytes, int)
            or total_bytes <= 0
            or isinstance(days, bool)
            or not isinstance(days, int)
            or days <= 0
            or not isinstance(reason, str)
            or not reason
        ):
            raise MaintenanceError("artifact_exception_contract_invalid")
        seen_ids.add(identifier)
        seen_names.update(str(name) for name in names)
        result.append(row)
    return tuple(result)


def _exception_match(
    value: Mapping[str, Any],
    rows: tuple[Mapping[str, Any], ...],
    now: datetime,
) -> Mapping[str, Any] | None:
    name = value.get("name")
    if not isinstance(name, str) or not name:
        raise MaintenanceError("artifact_invalid")
    created = _timestamp(value.get("created_at"))
    matches = [
        row
        for row in rows
        if name in row["allowed_names"]
        and now - created <= timedelta(days=int(row["maximum_retention_days"]))
    ]
    if len(matches) > 1:
        raise MaintenanceError("artifact_exception_contract_invalid")
    return matches[0] if matches else None


def _retention_sort_key(value: RetentionCandidate) -> tuple[datetime, int]:
    item = value[1]
    return _timestamp(item.get("created_at")), _positive(
        item.get("id"),
        "artifact_invalid",
    )


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
    contract.validate_request_id(request_id)
    policy = contract.operation("artifacts")
    exceptions = load_json_file(root, contract.artifact_exceptions_path)
    exception_rows = _exception_rows(exceptions)
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    cutoff = current - timedelta(hours=int(policy["minimum_age_hours"]))
    candidates: list[Candidate] = []
    retained: dict[str, list[RetentionCandidate]] = {
        str(row["id"]): [] for row in exception_rows
    }
    result = OperationResult("success", request_id)

    for project in contract.selected_projects(repository_scope):
        for item in api.list_artifacts(project.repository):
            artifact_id = _positive(item.get("id"), "artifact_invalid")
            _artifact_size(item)
            if _timestamp(item.get("created_at")) > cutoff:
                continue
            run_id_raw = _nested(item, "workflow_run", "id")
            run_id = _positive(run_id_raw) if run_id_raw is not None else None
            run = api.get_run(project.repository, run_id) if run_id is not None else None
            if run is not None and run.get("status") != "completed":
                result.decisions.append(
                    {
                        "repository": project.repository,
                        "artifact_id": artifact_id,
                        "action": "preserve",
                        "reason": "workflow_run_not_completed",
                    }
                )
                continue
            run_snapshot = _artifact_run_snapshot(run)
            exception = _exception_match(item, exception_rows, current)
            if exception is not None:
                retained[str(exception["id"])].append(
                    (project.repository, item, run_id, run_snapshot)
                )
                continue
            candidates.append(
                (
                    project.repository,
                    item,
                    run_id,
                    run_snapshot,
                    "expired_completed_run_artifact",
                )
            )

    for row in exception_rows:
        identifier = str(row["id"])
        maximum_count = int(row["maximum_count"])
        maximum_total_bytes = int(row["maximum_total_bytes"])
        used_count = 0
        used_bytes = 0
        for repository, item, run_id, run_snapshot in sorted(
            retained[identifier],
            key=_retention_sort_key,
            reverse=True,
        ):
            artifact_id = _positive(item.get("id"), "artifact_invalid")
            size = _artifact_size(item)
            if (
                used_count < maximum_count
                and used_bytes + size <= maximum_total_bytes
            ):
                used_count += 1
                used_bytes += size
                result.decisions.append(
                    {
                        "repository": repository,
                        "artifact_id": artifact_id,
                        "action": "preserve",
                        "reason": "retained_artifact_exception",
                        "exception_id": identifier,
                    }
                )
                continue
            candidates.append(
                (
                    repository,
                    item,
                    run_id,
                    run_snapshot,
                    "artifact_exception_limit_exceeded",
                )
            )

    if len(candidates) > int(policy["maximum_deletions"]):
        raise MaintenanceError("artifact_deletion_bound_exceeded")
    for repository, old, run_id, run_snapshot, reason in candidates:
        artifact_id = _positive(old.get("id"), "artifact_invalid")
        fresh = api.get_artifact(repository, artifact_id)
        if fresh is None:
            result.decisions.append(
                {
                    "repository": repository,
                    "artifact_id": artifact_id,
                    "action": "none",
                    "reason": "artifact_already_absent",
                }
            )
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
            if api.get_artifact(repository, artifact_id) is not None:
                raise MaintenanceError("artifact_delete_verification_failed")
            result.mutation_count += 1
        result.decisions.append(
            {
                "repository": repository,
                "artifact_id": artifact_id,
                "action": action,
                "reason": reason,
            }
        )
    return result
