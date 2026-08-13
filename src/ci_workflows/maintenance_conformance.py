"""Inventory-driven organization conformance reporting."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Mapping

from .maintenance_contract import MaintenanceContract, MaintenanceError
from .maintenance_core import MaintenanceApi, OperationResult, _inventory_repo, _positive, _workflow_rows, load_json_file

_SHARED_REF = re.compile(r"StreamScapeTV/ci-workflows/\.github/workflows/[^@\s\"']+@([A-Za-z0-9._/-]+)")


def _issue_snapshot(value: Mapping[str, Any]) -> tuple[Any, ...]:
    return (
        value.get("number"),
        value.get("title"),
        value.get("body"),
        value.get("state"),
        value.get("updated_at"),
    )


def _report_matches(issues: list[Mapping[str, Any]], title: str) -> list[Mapping[str, Any]]:
    return [issue for issue in issues if issue.get("title") == title and "pull_request" not in issue]


def conformance(contract: MaintenanceContract, api: MaintenanceApi, *, root: Path, repository_scope: str, dry_run: bool, request_id: str) -> OperationResult:
    contract.validate_request_id(request_id)
    policy = contract.operation("conformance")
    inventory = load_json_file(root, contract.workflow_inventory_path)
    findings: list[dict[str, Any]] = []
    if inventory.get("schema_version") != 2:
        raise MaintenanceError("workflow_inventory_invalid")
    for project in contract.selected_projects(repository_scope):
        expected = _workflow_rows(_inventory_repo(inventory, project.repository))
        live = set(api.list_workflow_files(project.repository))
        for path in sorted(set(expected) - live):
            findings.append({"repository": project.repository, "path": path, "kind": "missing_inventory_workflow"})
        for path in sorted(live - set(expected)):
            findings.append({"repository": project.repository, "path": path, "kind": "unregistered_live_workflow"})
        for path in sorted(live & set(expected)):
            row = expected[path]
            if row[3] == "retire":
                findings.append({"repository": project.repository, "path": path, "kind": "retired_agent_state_transport_present" if row[5] == "legacy-agent-state" else "retired_workflow_present"})
            text = api.get_file_text(project.repository, path, project.integration_branch)
            if text is None:
                findings.append({"repository": project.repository, "path": path, "kind": "workflow_disappeared_during_scan"})
                continue
            for ref in sorted(set(_SHARED_REF.findall(text))):
                findings.append({"repository": project.repository, "path": path, "kind": "shared_workflow_reference", "reference": ref, "immutable": bool(re.fullmatch(r"[0-9a-f]{40}", ref))})
        if len(findings) > int(policy["maximum_findings"]):
            raise MaintenanceError("conformance_finding_bound_exceeded")
    findings.sort(key=lambda item: (str(item.get("repository")), str(item.get("path")), str(item.get("kind")), str(item.get("reference", ""))))
    result = OperationResult("success", request_id, decisions=findings)
    if dry_run:
        return result
    report_repository = str(policy["report_repository"])
    scope = repository_scope.strip() or "all"
    title = f"{policy['report_title_prefix']}: {scope}"
    body = f"<!-- ci-workflows-maintenance:{scope} -->\n# Organization conformance report\n\n- Request: `{request_id}`\n- Scope: `{scope}`\n- Findings: **{len(findings)}**\n\n```json\n{json.dumps(findings, indent=2, sort_keys=True)}\n```\n"
    if len(body.encode()) > 60000:
        raise MaintenanceError("conformance_report_too_large")
    existing = _report_matches(api.list_open_issues(report_repository), title)
    if len(existing) > 1:
        raise MaintenanceError("conformance_report_ambiguous")
    if existing:
        issue = existing[0]
        issue_number = _positive(issue.get("number"))
        url = issue.get("html_url")
        result.report_issue_url = url if isinstance(url, str) else ""
        if issue.get("body") == body:
            result.decisions.append({"repository": report_repository, "issue_number": issue_number, "action": "none", "reason": "conformance_report_unchanged"})
            return result
        fresh = _report_matches(api.list_open_issues(report_repository), title)
        if len(fresh) != 1 or _issue_snapshot(fresh[0]) != _issue_snapshot(issue):
            raise MaintenanceError("conformance_report_changed_before_update")
        updated = api.update_issue(report_repository, issue_number, title, body)
        result.mutation_count = 1
        url = updated.get("html_url")
    else:
        if _report_matches(api.list_open_issues(report_repository), title):
            raise MaintenanceError("conformance_report_changed_before_create")
        created = api.create_issue(report_repository, title, body)
        result.mutation_count = 1
        url = created.get("html_url")
    result.report_issue_url = url if isinstance(url, str) else ""
    return result
