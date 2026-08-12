"""Infrastructure-only bounded workflow rerun logic."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from .maintenance_contract import MaintenanceContract, MaintenanceError, ProjectPolicy
from .maintenance_core import MaintenanceApi, OperationResult, _inventory_repo, _nested, _positive, _workflow_rows, load_json_file

_FAILED = {"failure", "cancelled", "startup_failure"}
_USER_FAILED = {"failure", "timed_out", "action_required"}

def _run_path(run: Mapping[str, Any]) -> str:
    value = run.get("path")
    return value.split("@", 1)[0] if isinstance(value, str) else ""


def _retry_allowed(inventory: Mapping[str, Any], project: ProjectPolicy, run: Mapping[str, Any], trust: set[str]) -> bool:
    row = _workflow_rows(_inventory_repo(inventory, project.repository)).get(_run_path(run))
    return bool(row and row[3] != "retire" and row[5] in trust)


def _user_step_failed(job: Mapping[str, Any]) -> bool:
    steps = job.get("steps")
    return isinstance(steps, list) and any(isinstance(step, Mapping) and str(step.get("name", "")).strip().casefold() != "set up job" and step.get("conclusion") in _USER_FAILED for step in steps)


def _classify(job: Mapping[str, Any], logs: str, policy: Mapping[str, Any]) -> tuple[bool, str]:
    _positive(job.get("id"), "job_invalid")
    labels = job.get("labels")
    if job.get("conclusion") not in _FAILED:
        return False, "job_conclusion_not_retryable"
    if not isinstance(labels, list) or "self-hosted" not in {str(label).casefold() for label in labels}:
        return False, "job_not_self_hosted"
    if _user_step_failed(job):
        return False, "user_step_failed"
    normalized = logs.casefold()
    if any(str(signature).casefold() in normalized for signature in policy["product_failure_signatures"]):
        return False, "deterministic_product_failure"
    if any(str(signature).casefold() in normalized for signature in policy["infrastructure_signatures"]):
        return True, "runner_infrastructure_failure"
    return False, "no_proven_runner_infrastructure_signature"


def _run_snapshot(run: Mapping[str, Any]) -> tuple[Any, ...]:
    return (run.get("id"), run.get("workflow_id"), _run_path(run), run.get("event"), run.get("status"), run.get("conclusion"), run.get("run_attempt"), run.get("head_branch"), run.get("head_sha"), _nested(run, "head_repository", "full_name"), run.get("pull_requests"))


def _target_current(api: MaintenanceApi, project: ProjectPolicy, run: Mapping[str, Any]) -> bool:
    if run.get("event") == "pull_request":
        pulls = run.get("pull_requests")
        if not isinstance(pulls, list):
            return False
        matches = [pull for pull in pulls if isinstance(pull, Mapping) and _nested(pull, "head", "sha") == run.get("head_sha") and _nested(pull, "head", "ref") == run.get("head_branch") and _nested(pull, "head", "repo", "full_name") == project.repository and _nested(pull, "base", "ref") == project.integration_branch and _nested(pull, "base", "repo", "full_name") == project.repository]
        if len(matches) != 1 or not isinstance(matches[0].get("number"), int):
            return False
        current = api.get_pull(project.repository, int(matches[0]["number"]))
        return bool(current and current.get("state") == "open" and _nested(current, "head", "sha") == run.get("head_sha") and _nested(current, "head", "ref") == run.get("head_branch") and _nested(current, "head", "repo", "full_name") == project.repository and _nested(current, "base", "ref") == project.integration_branch and _nested(current, "base", "repo", "full_name") == project.repository)
    if run.get("head_branch") != project.integration_branch:
        return False
    current = api.get_branch(project.repository, project.integration_branch)
    return bool(current and _nested(current, "commit", "sha") == run.get("head_sha"))


def runner_retry(contract: MaintenanceContract, api: MaintenanceApi, *, root: Path, project_id: str, run_id: int, expected_head_sha: str, dry_run: bool, request_id: str) -> OperationResult:
    contract.validate_request_id(request_id)
    contract.validate_sha(expected_head_sha)
    project = contract.project(project_id)
    policy = contract.operation("runner_retry")
    inventory = load_json_file(root, contract.workflow_inventory_path)
    run = api.get_run(project.repository, run_id)
    if run is None:
        raise MaintenanceError("run_not_found")
    if not _retry_allowed(inventory, project, run, set(policy["allowed_inventory_trust"])):
        raise MaintenanceError("workflow_not_allowlisted")
    if run.get("status") != "completed" or run.get("conclusion") not in {"failure", "cancelled"} or run.get("run_attempt") != 1 or run.get("event") not in policy["allowed_events"] or run.get("head_sha") != expected_head_sha or _nested(run, "head_repository", "full_name") != project.repository:
        raise MaintenanceError("run_not_retryable")
    if not _target_current(api, project, run):
        raise MaintenanceError("run_target_no_longer_current")
    failed = [job for job in api.list_attempt_jobs(project.repository, run_id, 1) if job.get("conclusion") in _FAILED]
    if not failed:
        raise MaintenanceError("no_failed_jobs")
    if len(failed) > int(policy["maximum_failed_jobs"]):
        raise MaintenanceError("failed_job_bound_exceeded")
    decisions: list[dict[str, Any]] = []
    for job in failed:
        job_id = _positive(job.get("id"), "job_invalid")
        eligible, reason = _classify(job, api.download_job_logs(project.repository, job_id, int(policy["maximum_log_bytes"])), policy)
        decisions.append({"job_id": job_id, "eligible": eligible, "reason": reason})
        if not eligible:
            raise MaintenanceError("deterministic_or_unproven_failure")
    fresh = api.get_run(project.repository, run_id)
    if fresh is None or _run_snapshot(fresh) != _run_snapshot(run) or not _target_current(api, project, fresh):
        raise MaintenanceError("run_changed_before_retry")
    result = OperationResult("success", request_id, retry_run_id=str(run_id), decisions=decisions)
    if not dry_run:
        api.rerun_failed_jobs(project.repository, run_id)
        result.mutation_count = 1
    return result
