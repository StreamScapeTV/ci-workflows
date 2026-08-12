"""Bounded contract loading for organization maintenance operations."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

CONTRACT_PATH = Path("contracts/organization-maintenance.json")
_SHA = re.compile(r"^[0-9a-f]{40}$")
_PROJECT = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")

class MaintenanceError(RuntimeError):
    """Stable fail-closed maintenance error."""
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)

@dataclass(frozen=True)
class ProjectPolicy:
    project_id: str
    repository: str
    integration_branch: str

@dataclass(frozen=True)
class MaintenanceContract:
    organization: str
    request_id_pattern: str
    projects: tuple[ProjectPolicy, ...]
    operations: Mapping[str, Mapping[str, Any]]
    workflow_inventory_path: str
    artifact_exceptions_path: str
    maintenance_runner_selector: tuple[str, ...]
    flux_runner_selector: tuple[str, ...]
    def project(self, project_id: str) -> ProjectPolicy:
        for project in self.projects:
            if project.project_id == project_id:
                return project
        raise MaintenanceError("project_not_allowlisted")
    def selected_projects(self, repository_scope: str) -> tuple[ProjectPolicy, ...]:
        scope = repository_scope.strip()
        return self.projects if not scope else (self.project(scope),)
    def operation(self, name: str) -> Mapping[str, Any]:
        value = self.operations.get(name)
        if not isinstance(value, Mapping):
            raise MaintenanceError("operation_not_allowlisted")
        return value
    def validate_request_id(self, request_id: str) -> str:
        if re.fullmatch(self.request_id_pattern, request_id) is None:
            raise MaintenanceError("invalid_request_id")
        return request_id
    @staticmethod
    def validate_sha(value: str) -> str:
        if _SHA.fullmatch(value) is None:
            raise MaintenanceError("invalid_expected_head_sha")
        return value

def _strings(value: Any, *, nonempty: bool = True) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
        raise MaintenanceError("invalid_contract")
    result = tuple(value)
    if (nonempty and not result) or len(set(result)) != len(result):
        raise MaintenanceError("invalid_contract")
    return result

def _relative(value: Any) -> str:
    if not isinstance(value, str) or not value or value.startswith("/") or "\\" in value:
        raise MaintenanceError("invalid_contract")
    if any(part in {"", ".", ".."} for part in value.split("/")):
        raise MaintenanceError("invalid_contract")
    return value

def load_contract(root: Path) -> MaintenanceContract:
    try:
        raw = json.loads((root / CONTRACT_PATH).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise MaintenanceError("invalid_contract") from error
    if not isinstance(raw, Mapping) or raw.get("schema_version") != 1 or raw.get("contract_version") != "1.0.0" or raw.get("organization") != "StreamScapeTV":
        raise MaintenanceError("invalid_contract")
    request_pattern = raw.get("request_id_pattern")
    if not isinstance(request_pattern, str):
        raise MaintenanceError("invalid_contract")
    try:
        re.compile(request_pattern)
    except re.error as error:
        raise MaintenanceError("invalid_contract") from error
    projects_raw = raw.get("projects")
    if not isinstance(projects_raw, list) or not projects_raw:
        raise MaintenanceError("invalid_contract")
    projects: list[ProjectPolicy] = []
    seen: set[str] = set(); repositories: set[str] = set()
    for item in projects_raw:
        if not isinstance(item, Mapping) or set(item) != {"project_id", "repository", "integration_branch"}:
            raise MaintenanceError("invalid_contract")
        project_id, repository, branch = item.get("project_id"), item.get("repository"), item.get("integration_branch")
        if not isinstance(project_id, str) or _PROJECT.fullmatch(project_id) is None or not isinstance(repository, str) or not repository.startswith("StreamScapeTV/") or repository.count("/") != 1 or not isinstance(branch, str) or not branch or "/" in branch:
            raise MaintenanceError("invalid_contract")
        if project_id in seen or repository in repositories:
            raise MaintenanceError("invalid_contract")
        seen.add(project_id); repositories.add(repository); projects.append(ProjectPolicy(project_id, repository, branch))
    operations = raw.get("operations")
    if not isinstance(operations, Mapping) or set(operations) != {"artifacts", "branches", "conformance", "runner_retry", "flux_reconcile"}:
        raise MaintenanceError("invalid_contract")
    if any(not isinstance(value, Mapping) or value.get("dry_run_default") is not True for value in operations.values()):
        raise MaintenanceError("invalid_contract")
    artifacts = operations["artifacts"]
    if not isinstance(artifacts.get("minimum_age_hours"), int) or not 1 <= int(artifacts["minimum_age_hours"]) <= 168 or not isinstance(artifacts.get("maximum_deletions"), int) or not 1 <= int(artifacts["maximum_deletions"]) <= 1000:
        raise MaintenanceError("invalid_contract")
    retry = operations["runner_retry"]
    if not isinstance(retry.get("maximum_failed_jobs"), int) or not 1 <= int(retry["maximum_failed_jobs"]) <= 50 or not isinstance(retry.get("maximum_log_bytes"), int) or not 1024 <= int(retry["maximum_log_bytes"]) <= 4 * 1024 * 1024:
        raise MaintenanceError("invalid_contract")
    for field in ("allowed_inventory_trust", "allowed_events", "infrastructure_signatures", "product_failure_signatures"):
        _strings(retry.get(field))
    flux = operations["flux_reconcile"]
    if flux.get("repository") != "StreamScapeTV/flux" or flux.get("policy_interface") != "central-flux-policy-v1":
        raise MaintenanceError("invalid_contract")
    _strings(flux.get("allowed_operations")); _strings(flux.get("credentials"))
    for field in ("policy_path", "allowlist_path", "executor_path"):
        _relative(flux.get(field))
    retired = raw.get("retired_boundaries")
    if not isinstance(retired, Mapping):
        raise MaintenanceError("invalid_contract")
    forbidden = {item.casefold() for item in _strings(retired.get("agent_state_transport"))}
    if not {"agent-state-claim", "agent-state-lifecycle", "agent-state-ownership"} <= forbidden:
        raise MaintenanceError("invalid_contract")
    return MaintenanceContract("StreamScapeTV", request_pattern, tuple(projects), operations, _relative(raw.get("workflow_inventory_path")), _relative(raw.get("artifact_exceptions_path")), _strings(raw.get("maintenance_runner_selector")), _strings(raw.get("flux_runner_selector")))
