"""Inventory-driven organization conformance reporting."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Mapping

from .maintenance_contract import MaintenanceContract, MaintenanceError
from .maintenance_core import (
    MaintenanceApi,
    OperationResult,
    _inventory_repo,
    _nested,
    _positive,
    _workflow_rows,
    load_json_file,
)

_SHARED_REF = re.compile(
    r"(?P<uses>StreamScapeTV/ci-workflows/\.github/workflows/"
    r"(?P<workflow>[^@\s\"']+))@(?P<ref>[A-Za-z0-9._/-]+)"
)
_CENTRAL_REPOSITORY = "StreamScapeTV/ci-workflows"


def _issue_snapshot(value: Mapping[str, Any]) -> tuple[Any, ...]:
    return (
        value.get("number"),
        value.get("title"),
        value.get("body"),
        value.get("state"),
        value.get("updated_at"),
    )


def _report_matches(
    issues: list[Mapping[str, Any]],
    title: str,
) -> list[Mapping[str, Any]]:
    if not isinstance(issues, list) or any(
        not isinstance(issue, Mapping) for issue in issues
    ):
        raise MaintenanceError("conformance_report_invalid")
    return [
        issue
        for issue in issues
        if issue.get("title") == title and "pull_request" not in issue
    ]


def _verify_report_response(
    value: Mapping[str, Any],
    *,
    title: str,
    body: str,
    expected_number: int | None = None,
) -> tuple[int, str]:
    if not isinstance(value, Mapping):
        raise MaintenanceError("conformance_report_verification_failed")
    number = _positive(
        value.get("number"),
        "conformance_report_verification_failed",
    )
    url = value.get("html_url")
    if (
        (expected_number is not None and number != expected_number)
        or value.get("title") != title
        or value.get("body") != body
        or not isinstance(url, str)
        or not url.startswith("https://")
        or "\r" in url
        or "\n" in url
    ):
        raise MaintenanceError("conformance_report_verification_failed")
    return number, url


def _reference_findings(
    *,
    repository: str,
    path: str,
    text: str,
    target_sha: str,
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for match in _SHARED_REF.finditer(text):
        workflow = str(match.group("workflow"))
        reference = str(match.group("ref"))
        key = (workflow, reference)
        if key in seen:
            continue
        seen.add(key)
        findings.append(
            {
                "repository": repository,
                "path": path,
                "kind": "shared_workflow_reference",
                "workflow": workflow,
                "reference": reference,
                "immutable": bool(re.fullmatch(r"[0-9a-f]{40}", reference)),
            }
        )
        if target_sha and reference != target_sha:
            findings.append(
                {
                    "repository": repository,
                    "path": path,
                    "kind": "shared_workflow_update_proposal",
                    "workflow": workflow,
                    "current_reference": reference,
                    "proposed_reference": target_sha,
                    "review_only": True,
                }
            )
    return findings


def _validate_reference_target(
    contract: MaintenanceContract,
    api: MaintenanceApi,
    target_sha: str,
) -> str:
    target = target_sha.strip()
    if not target:
        return ""
    contract.validate_sha(target)
    central = contract.project("ci-workflows")
    if central.repository != _CENTRAL_REPOSITORY:
        raise MaintenanceError("shared_reference_target_invalid")
    branch = api.get_branch(central.repository, central.integration_branch)
    head_sha = _nested(branch, "commit", "sha")
    if (
        not isinstance(branch, Mapping)
        or branch.get("protected") is not True
        or not isinstance(head_sha, str)
    ):
        raise MaintenanceError("shared_reference_target_invalid")
    try:
        contract.validate_sha(head_sha)
    except MaintenanceError as error:
        raise MaintenanceError("shared_reference_target_invalid") from error
    commit = api.get_commit(central.repository, target)
    if commit is None or commit.get("sha") != target:
        raise MaintenanceError("shared_reference_target_invalid")
    if target == head_sha:
        return target
    comparison = api.compare_commits(central.repository, target, head_sha)
    if (
        not isinstance(comparison, Mapping)
        or comparison.get("status") != "ahead"
        or _nested(comparison, "base_commit", "sha") != target
        or _nested(comparison, "merge_base_commit", "sha") != target
    ):
        raise MaintenanceError("shared_reference_target_invalid")
    return target


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
    contract.validate_request_id(request_id)
    policy = contract.operation("conformance")
    target_sha = _validate_reference_target(
        contract,
        api,
        shared_reference_target_sha,
    )
    inventory = load_json_file(root, contract.workflow_inventory_path)
    findings: list[dict[str, Any]] = []
    if inventory.get("schema_version") != 2:
        raise MaintenanceError("workflow_inventory_invalid")
    for project in contract.selected_projects(repository_scope):
        expected = _workflow_rows(_inventory_repo(inventory, project.repository))
        live = set(api.list_workflow_files(project.repository))
        for path in sorted(set(expected) - live):
            findings.append(
                {
                    "repository": project.repository,
                    "path": path,
                    "kind": "missing_inventory_workflow",
                }
            )
        for path in sorted(live - set(expected)):
            findings.append(
                {
                    "repository": project.repository,
                    "path": path,
                    "kind": "unregistered_live_workflow",
                }
            )
        for path in sorted(live & set(expected)):
            row = expected[path]
            if row[3] == "retire":
                findings.append(
                    {
                        "repository": project.repository,
                        "path": path,
                        "kind": (
                            "retired_agent_state_transport_present"
                            if row[5] == "legacy-agent-state"
                            else "retired_workflow_present"
                        ),
                    }
                )
            text = api.get_file_text(
                project.repository,
                path,
                project.integration_branch,
            )
            if text is None:
                findings.append(
                    {
                        "repository": project.repository,
                        "path": path,
                        "kind": "workflow_disappeared_during_scan",
                    }
                )
                continue
            findings.extend(
                _reference_findings(
                    repository=project.repository,
                    path=path,
                    text=text,
                    target_sha=target_sha,
                )
            )
        if len(findings) > int(policy["maximum_findings"]):
            raise MaintenanceError("conformance_finding_bound_exceeded")
    findings.sort(
        key=lambda item: (
            str(item.get("repository")),
            str(item.get("path")),
            str(item.get("kind")),
            str(item.get("workflow", "")),
            str(item.get("reference", item.get("current_reference", ""))),
        )
    )
    result = OperationResult("success", request_id, decisions=findings)
    if dry_run:
        return result

    report_repository = str(policy["report_repository"])
    scope = repository_scope.strip() or "all"
    title = f"{policy['report_title_prefix']}: {scope}"
    target_line = (
        f"- Shared-reference proposal target: `{target_sha}`\n"
        if target_sha
        else "- Shared-reference proposal target: not requested\n"
    )
    body = (
        f"<!-- ci-workflows-maintenance:{scope} -->\n"
        "# Organization conformance report\n\n"
        f"- Request: `{request_id}`\n"
        f"- Scope: `{scope}`\n"
        f"{target_line}"
        f"- Findings/proposals: **{len(findings)}**\n"
        "- Consumer source is not changed by this report; repin entries are review-only proposals.\n\n"
        "```json\n"
        f"{json.dumps(findings, indent=2, sort_keys=True)}\n"
        "```\n"
    )
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
            result.decisions.append(
                {
                    "repository": report_repository,
                    "issue_number": issue_number,
                    "action": "none",
                    "reason": "conformance_report_unchanged",
                }
            )
            return result
        fresh = _report_matches(api.list_open_issues(report_repository), title)
        if len(fresh) != 1 or _issue_snapshot(fresh[0]) != _issue_snapshot(issue):
            raise MaintenanceError("conformance_report_changed_before_update")
        updated = api.update_issue(
            report_repository,
            issue_number,
            title,
            body,
        )
        _, verified_url = _verify_report_response(
            updated,
            title=title,
            body=body,
            expected_number=issue_number,
        )
        result.mutation_count = 1
        result.report_issue_url = verified_url
        return result

    if _report_matches(api.list_open_issues(report_repository), title):
        raise MaintenanceError("conformance_report_changed_before_create")
    created = api.create_issue(report_repository, title, body)
    _, verified_url = _verify_report_response(
        created,
        title=title,
        body=body,
    )
    result.mutation_count = 1
    result.report_issue_url = verified_url
    return result
