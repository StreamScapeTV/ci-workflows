"""Execute the protected parameterized Agent State command workflow."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Mapping, MutableMapping, Sequence

from .agent_state_contract import (
    Command,
    CommandFailure,
    Contracts,
    DispatchContext,
    HttpStatusError,
    RepositoryMapping,
    SHA_PATTERN,
    TargetContext,
    load_contracts,
    result_for_failure,
    sanitize_result,
    validate_dispatch_environment,
    validate_inputs,
    require,
)
from .agent_state_transport import (
    AgentStateClient,
    GitHubClient,
    JsonHttpClient,
    normalize_api_url,
    synthetic_command_id,
)


def resolve_target(
    command: Command,
    dispatch: DispatchContext,
    mapping: RepositoryMapping,
    github: GitHubClient,
    agent_state: AgentStateClient,
) -> TargetContext:
    github.repository(command.repository)
    context = agent_state.context(command.repository, command.project)
    require(
        context.get("accepted") is True,
        str(context.get("instruction") or "agent_state_context_rejected"),
    )
    require(
        context.get("repository") == command.repository,
        "agent_state_context_repository_mismatch",
    )
    require(
        context.get("project") == command.project,
        "agent_state_context_project_mismatch",
    )
    require(
        context.get("integration_ref") == mapping.integration_branch,
        "agent_state_context_branch_mismatch",
    )
    integration_sha = github.branch_sha(command.repository, mapping.integration_branch)
    if command.base_sha is not None:
        require(command.base_sha == integration_sha, "stale_base_sha")

    issue_state: str | None = None
    if command.issue_number is not None:
        issue = github.issue(command.repository, command.issue_number)
        issue_state = str(issue["state"])
        if command.action not in {"done", "cancel"}:
            require(issue_state == "open", "origin_issue_is_not_open")

    pr_head_sha: str | None = None
    if command.pr_number is not None:
        pull = github.pull(command.repository, command.pr_number)
        head = (pull.get("head") or {}).get("sha")
        base = (pull.get("base") or {}).get("ref")
        require(
            isinstance(head, str)
            and SHA_PATTERN.fullmatch(head.casefold()) is not None,
            "github_pull_head_invalid",
        )
        require(base == mapping.integration_branch, "github_pull_base_mismatch")
        pr_head_sha = head.casefold()
        require(command.head_sha == pr_head_sha, "stale_head_sha")
    return TargetContext(
        actor=dispatch.actor,
        integration_ref=mapping.integration_branch,
        integration_sha=integration_sha,
        issue_state=issue_state,
        pr_head_sha=pr_head_sha,
    )


def execute(
    command: Command,
    dispatch: DispatchContext,
    contracts: Contracts,
    github: GitHubClient,
    agent_state: AgentStateClient,
) -> dict[str, Any]:
    target = resolve_target(
        command,
        dispatch,
        contracts.repositories[command.repository],
        github,
        agent_state,
    )
    if command.transport == "direct":
        raw = agent_state.direct(command, target)
    elif command.transport == "github-lifecycle-compat":
        raw = agent_state.lifecycle_compat(command, target)
    else:
        raise CommandFailure("unsupported_agent_state_transport")
    return sanitize_result(command, raw, contracts.command)


def _write_output(path: str | None, values: Mapping[str, Any]) -> None:
    if not path:
        return
    lines: list[str] = []
    for key in (
        "accepted",
        "decision",
        "instruction",
        "receipt_id",
        "agent_id",
        "status",
        "request_id",
    ):
        value = values.get(key, "")
        if isinstance(value, bool):
            value = "true" if value else "false"
        text = str(value).replace("\r", " ").replace("\n", " ")
        lines.append(f"{key}={text}")
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_summary(path: str | None, values: Mapping[str, Any]) -> None:
    if not path:
        return
    rows = [
        ("Accepted", "yes" if values.get("accepted") is True else "no"),
        ("Action", values.get("action", "")),
        ("Repository", values.get("repository", "")),
        ("Project", values.get("project", "")),
        ("Decision", values.get("decision", "")),
        ("Instruction", values.get("instruction", "")),
        ("Receipt", values.get("receipt_id", "—")),
        ("Agent", values.get("agent_id", "—")),
        ("Status", values.get("status", "—")),
        ("Request", values.get("request_id", "")),
    ]
    lines = [
        "## Agent State command",
        "",
        "| Field | Result |",
        "|---|---|",
    ]
    for name, value in rows:
        text = str(value).replace("|", "\\|").replace("\r", " ").replace("\n", " ")
        lines.append(f"| {name} | `{text}` |")
    lines += [
        "",
        "This run is the response surface. No lifecycle comment, label, consumer commit, or artifact was created.",
        "",
    ]
    Path(path).write_text("\n".join(lines), encoding="utf-8")


def _raw_inputs(environment: Mapping[str, str]) -> dict[str, str]:
    names = (
        "request_id",
        "repository",
        "project",
        "action",
        "session_name",
        "agent_id",
        "issue_number",
        "pr_number",
        "task",
        "base_sha",
        "head_sha",
        "branch",
        "files",
        "packages",
        "claim_type",
        "claim_mode",
        "summary",
        "reason",
    )
    return {
        name: environment.get("INPUT_" + name.upper(), "") for name in names
    }


def run(
    *,
    root: Path,
    environment: MutableMapping[str, str],
    http: JsonHttpClient | None = None,
) -> tuple[int, dict[str, Any]]:
    raw = _raw_inputs(environment)
    try:
        contracts = load_contracts(root)
        dispatch = validate_dispatch_environment(environment, contracts.command)
        command = validate_inputs(raw, contracts)
        http_client = http or JsonHttpClient()
        github = GitHubClient(
            environment.get("TARGET_GITHUB_TOKEN", ""),
            environment.get("GITHUB_API_URL", "https://api.github.com"),
            http_client,
        )
        agent_state = AgentStateClient(
            environment.get("AGENT_STATE_API_URL", ""),
            environment.get("AGENT_STATE_API_TOKEN", ""),
            http_client,
            retry_attempts=int(contracts.command["limits"]["retry_attempts"]),
            retry_after_max=int(
                contracts.command["limits"]["retry_after_seconds_max"]
            ),
        )
        result = execute(command, dispatch, contracts, github, agent_state)
        exit_code = 0 if result["accepted"] is True else 1
    except CommandFailure as failure:
        result = result_for_failure(raw, failure)
        exit_code = failure.exit_code

    _write_output(environment.get("GITHUB_OUTPUT"), result)
    _write_summary(environment.get("GITHUB_STEP_SUMMARY"), result)
    print(
        "AGENT_STATE_COMMAND_RESULT "
        + json.dumps(result, separators=(",", ":"), sort_keys=True)
    )
    return exit_code, result


def main(argv: Sequence[str] | None = None) -> int:
    del argv
    root = Path(__file__).resolve().parents[2]
    exit_code, _ = run(root=root, environment=os.environ)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
