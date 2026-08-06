"""Typed contracts and redaction for the Agent State command workflow."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
REPOSITORY_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
PROJECT_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
INTEGER_PATTERN = re.compile(r"^[1-9][0-9]*$")
CLAIM_TYPES = frozenset({"advisory", "write", "exclusive"})
CLAIM_MODES = frozenset({"exact", "prefix"})
EMPTY_SENTINELS = frozenset({"", "none"})
URL_PATTERN = re.compile(r"(?i)\b(?:https?|ftp)://[^\s<>`]+")
BEARER_PATTERN = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")
SECRET_ASSIGNMENT_PATTERN = re.compile(
    r"(?i)\b(token|password|secret|authorization|api[_-]?key)\s*[:=]\s*[^\s,;]+"
)


class CommandFailure(RuntimeError):
    """Fail-closed, user-safe command error."""

    def __init__(
        self,
        instruction: str,
        *,
        decision: str = "error",
        retryable: bool = False,
        exit_code: int = 2,
    ) -> None:
        super().__init__(instruction)
        self.instruction = instruction
        self.decision = decision
        self.retryable = retryable
        self.exit_code = exit_code


class HttpStatusError(CommandFailure):
    """Private HTTP failure whose URL and body must never reach output."""

    def __init__(
        self,
        service: str,
        status: int,
        headers: Mapping[str, str],
        payload: Any,
    ) -> None:
        super().__init__(
            f"{service.casefold().replace(' ', '_')}_http_{status}",
            decision="error",
            exit_code=3,
        )
        self.service = service
        self.status = status
        self.headers = headers
        self.payload = payload


@dataclass(frozen=True)
class RepositoryMapping:
    repository: str
    project: str
    integration_branch: str


@dataclass(frozen=True)
class Contracts:
    command: Mapping[str, Any]
    repositories: Mapping[str, RepositoryMapping]


@dataclass(frozen=True)
class DispatchContext:
    actor: str
    central_sha: str


@dataclass(frozen=True)
class Command:
    request_id: str
    repository: str
    project: str
    action: str
    session_name: str
    agent_id: str | None
    issue_number: int | None
    pr_number: int | None
    task: str | None
    base_sha: str | None
    head_sha: str | None
    branch: str | None
    files: tuple[str, ...]
    packages: tuple[str, ...]
    claim_type: str | None
    claim_mode: str | None
    summary: str | None
    reason: str | None
    transport: str


@dataclass(frozen=True)
class TargetContext:
    actor: str
    integration_ref: str
    integration_sha: str
    issue_state: str | None
    pr_head_sha: str | None


def require(condition: bool, instruction: str) -> None:
    if not condition:
        raise CommandFailure(instruction)


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise CommandFailure("agent_state_contract_unavailable") from error


def load_contracts(root: Path) -> Contracts:
    command = _read_json(root / "contracts/agent-state-command.json")
    projects = _read_json(root / "contracts/agent-state-projects.json")
    require(isinstance(command, dict), "agent_state_command_contract_invalid")
    require(command.get("schema_version") == 1, "agent_state_command_contract_invalid")
    require(isinstance(projects, dict), "agent_state_project_contract_invalid")
    require(projects.get("schema_version") == 1, "agent_state_project_contract_invalid")
    require(
        projects.get("organization")
        == command.get("dispatch_repository", "").split("/", 1)[0],
        "agent_state_contract_organization_mismatch",
    )

    rows = projects.get("repositories")
    require(isinstance(rows, list) and rows, "agent_state_project_contract_invalid")
    mappings: dict[str, RepositoryMapping] = {}
    for row in rows:
        require(isinstance(row, dict), "agent_state_project_contract_invalid")
        repository = row.get("repository")
        project = row.get("project")
        branch = row.get("integration_branch")
        require(
            isinstance(repository, str)
            and REPOSITORY_PATTERN.fullmatch(repository) is not None,
            "agent_state_project_contract_invalid",
        )
        require(
            isinstance(project, str) and PROJECT_PATTERN.fullmatch(project) is not None,
            "agent_state_project_contract_invalid",
        )
        require(
            isinstance(branch, str)
            and 0 < len(branch) <= 255
            and branch == branch.strip(),
            "agent_state_project_contract_invalid",
        )
        require(repository not in mappings, "agent_state_project_contract_duplicate")
        mappings[repository] = RepositoryMapping(repository, project, branch)

    actors = command.get("authorized_actors")
    require(
        isinstance(actors, list)
        and actors
        and all(isinstance(actor, str) and actor for actor in actors),
        "agent_state_actor_contract_invalid",
    )
    actions = command.get("actions")
    require(isinstance(actions, dict) and actions, "agent_state_action_contract_invalid")
    inputs = command.get("inputs")
    require(
        isinstance(inputs, list)
        and inputs
        and len(inputs) == len(set(inputs))
        and all(isinstance(value, str) and value for value in inputs),
        "agent_state_input_contract_invalid",
    )
    return Contracts(command=command, repositories=mappings)


def validate_dispatch_environment(
    environment: Mapping[str, str],
    contract: Mapping[str, Any],
) -> DispatchContext:
    repository = environment.get("GITHUB_REPOSITORY", "")
    ref = environment.get("GITHUB_REF", "")
    actor = environment.get("GITHUB_TRIGGERING_ACTOR", "") or environment.get(
        "GITHUB_ACTOR", ""
    )
    original_actor = environment.get("GITHUB_ACTOR", "")
    central_sha = environment.get("GITHUB_SHA", "").casefold()
    require(repository == contract.get("dispatch_repository"), "untrusted_dispatch_repository")
    require(ref == contract.get("protected_ref"), "agent_state_command_requires_protected_main")
    authorized = set(contract.get("authorized_actors", ()))
    require(actor in authorized and original_actor in authorized, "unauthorized_dispatch_actor")
    require(SHA_PATTERN.fullmatch(central_sha) is not None, "invalid_central_source_sha")
    return DispatchContext(actor=actor, central_sha=central_sha)


def _input_text(raw: Mapping[str, Any], name: str) -> str:
    value = raw.get(name, "")
    if value is None:
        return ""
    require(isinstance(value, (str, int)), f"invalid_{name}")
    return str(value)


def _optional_text(
    raw: Mapping[str, Any],
    name: str,
    *,
    limit: int,
    strip: bool = True,
) -> str | None:
    value = _input_text(raw, name)
    if value.casefold() in EMPTY_SENTINELS:
        return None
    normalized = value.strip() if strip else value
    require(bool(normalized), f"{name}_is_empty")
    require(len(normalized) <= limit, f"{name}_too_long")
    require(
        not any(
            ord(character) < 32 and character not in "\t\n\r"
            for character in normalized
        ),
        f"{name}_contains_control_character",
    )
    return normalized


def _number(raw: Mapping[str, Any], name: str) -> int | None:
    value = _input_text(raw, name).strip()
    if value.casefold() in EMPTY_SENTINELS:
        return None
    require(INTEGER_PATTERN.fullmatch(value) is not None, f"invalid_{name}")
    number = int(value)
    require(number <= 2_147_483_647, f"invalid_{name}")
    return number


def _sha(raw: Mapping[str, Any], name: str) -> str | None:
    value = _input_text(raw, name).strip().casefold()
    if value.casefold() in EMPTY_SENTINELS:
        return None
    require(SHA_PATTERN.fullmatch(value) is not None, f"invalid_{name}")
    return value


def _normalize_file(path: str, max_length: int) -> str:
    require(path == path.strip() and path, "invalid_file_claim")
    require(len(path) <= max_length, "file_claim_too_long")
    require("\\" not in path and not path.startswith("/"), "invalid_file_claim")
    require(
        not any(ord(character) < 32 or ord(character) == 127 for character in path),
        "invalid_file_claim",
    )
    require(not any(character in "*?[]" for character in path), "file_claim_glob_forbidden")
    pure = PurePosixPath(path)
    require(
        path not in {".", ".."} and all(part not in {"", ".", ".."} for part in pure.parts),
        "invalid_file_claim",
    )
    normalized = pure.as_posix()
    require(normalized == path, "file_claim_not_canonical")
    return normalized


def _normalize_package(value: str, max_length: int) -> str:
    require(value == value.strip() and value, "invalid_package_claim")
    require(len(value) <= max_length, "package_claim_too_long")
    require(
        re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]*", value) is not None,
        "invalid_package_claim",
    )
    return value


def normalize_multiline(
    value: str,
    *,
    kind: str,
    max_items: int,
    max_length: int,
) -> tuple[str, ...]:
    lines = [line for line in value.splitlines() if line.strip()]
    require(len(lines) <= max_items, f"{kind}_claim_limit_exceeded")
    normalizer = _normalize_file if kind == "file" else _normalize_package
    normalized = [normalizer(line, max_length) for line in lines]
    require(len(normalized) == len(set(normalized)), f"duplicate_{kind}_claim")
    return tuple(sorted(normalized))


def _identity_slot(
    session_name: str,
    agent_id: str | None,
    contract: Mapping[str, Any],
) -> None:
    session_pattern = re.compile(str(contract.get("session_name_pattern", "")))
    agent_pattern = re.compile(str(contract.get("agent_id_pattern", "")))
    match = session_pattern.fullmatch(session_name)
    require(match is not None, "invalid_chatgpt_session_name")
    if agent_id is None:
        return
    agent_match = agent_pattern.fullmatch(agent_id)
    require(agent_match is not None, "named_agent_id_required")
    require(
        session_name.split(" ", 1)[1] == agent_id.split("-", 3)[2],
        "agent_id_session_name_mismatch",
    )


def validate_inputs(raw: Mapping[str, Any], contracts: Contracts) -> Command:
    contract = contracts.command
    known = set(contract["inputs"])
    require(not (set(raw) - known), "unknown_workflow_inputs")

    request_id = _input_text(raw, "request_id").strip()
    request_pattern = re.compile(str(contract.get("request_id_pattern", "")))
    require(request_pattern.fullmatch(request_id) is not None, "invalid_request_id")

    repository = _input_text(raw, "repository").strip()
    project = _input_text(raw, "project").strip()
    action = _input_text(raw, "action").strip()
    session_name = _input_text(raw, "session_name").strip()
    require(REPOSITORY_PATTERN.fullmatch(repository) is not None, "invalid_target_repository")
    require(PROJECT_PATTERN.fullmatch(project) is not None, "invalid_target_project")
    mapping = contracts.repositories.get(repository)
    require(mapping is not None, "repository_is_not_mapped_to_agent_state")
    require(mapping.project == project, "repository_project_mismatch")

    actions = contract["actions"]
    require(action in actions, "unsupported_action")
    specification = actions[action]
    require(isinstance(specification, dict), "agent_state_action_contract_invalid")
    required = set(specification.get("required", ()))
    optional = set(specification.get("optional", ()))
    allowed = required | optional
    for name in required:
        value = _input_text(raw, name)
        require(value.strip() and value.casefold() != "none", f"{name}_required_for_{action}")
    for name in known - allowed:
        value = _input_text(raw, name)
        require(value.strip().casefold() in EMPTY_SENTINELS, f"{name}_not_allowed_for_{action}")

    limits = contract["limits"]
    task = _optional_text(raw, "task", limit=int(limits["task"]))
    summary = _optional_text(raw, "summary", limit=int(limits["summary"]))
    reason = _optional_text(raw, "reason", limit=int(limits["reason"]))
    branch = _optional_text(raw, "branch", limit=255, strip=False)
    if branch is not None:
        require(branch == branch.strip(), "branch_surrounding_whitespace")
        require(
            re.fullmatch(
                r"issue/[1-9][0-9]*-[A-Za-z0-9][A-Za-z0-9._-]*-[a-z0-9]{4}",
                branch,
            )
            is not None,
            "invalid_issue_branch",
        )

    agent_id = _optional_text(raw, "agent_id", limit=128, strip=False)
    if agent_id is not None:
        require(agent_id == agent_id.strip(), "agent_id_surrounding_whitespace")
    _identity_slot(session_name, agent_id, contract)

    issue_number = _number(raw, "issue_number")
    pr_number = _number(raw, "pr_number")
    base_sha = _sha(raw, "base_sha")
    head_sha = _sha(raw, "head_sha")
    if branch is not None and issue_number is not None:
        branch_issue = branch.split("/", 1)[1].split("-", 1)[0]
        require(branch_issue == str(issue_number), "issue_branch_issue_mismatch")
        if agent_id is not None:
            require(
                branch.rsplit("-", 1)[-1] == agent_id.rsplit("-", 1)[-1],
                "issue_branch_must_end_with_session_nonce",
            )

    list_max = int(limits["list_items"])
    item_max = int(limits["list_item_length"])
    files = normalize_multiline(
        _input_text(raw, "files"),
        kind="file",
        max_items=list_max,
        max_length=item_max,
    )
    packages = normalize_multiline(
        _input_text(raw, "packages"),
        kind="package",
        max_items=list_max,
        max_length=item_max,
    )
    if specification.get("scope_required"):
        require(bool(files or packages), f"scope_required_for_{action}")

    claim_type_value = _input_text(raw, "claim_type").strip().casefold()
    claim_mode_value = _input_text(raw, "claim_mode").strip().casefold()
    claim_type = None if claim_type_value in EMPTY_SENTINELS else claim_type_value
    claim_mode = None if claim_mode_value in EMPTY_SENTINELS else claim_mode_value
    require(claim_type is None or claim_type in CLAIM_TYPES, "invalid_claim_type")
    require(claim_mode is None or claim_mode in CLAIM_MODES, "invalid_claim_mode")
    if action in {"start", "claim"}:
        claim_type = claim_type or "write"
        claim_mode = claim_mode or "exact"
        require(
            not (claim_type == "write" and claim_mode == "prefix"),
            "write_prefix_claim_forbidden",
        )
    else:
        require(
            claim_type is None and claim_mode is None,
            f"claim_settings_not_allowed_for_{action}",
        )

    if pr_number is None:
        require(head_sha is None, "head_sha_requires_pr_number")
    else:
        require(bool(specification.get("pr_allowed")), f"pr_number_not_allowed_for_{action}")
        require(head_sha is not None, "head_sha_required_with_pr_number")

    return Command(
        request_id=request_id,
        repository=repository,
        project=project,
        action=action,
        session_name=session_name,
        agent_id=agent_id,
        issue_number=issue_number,
        pr_number=pr_number,
        task=task,
        base_sha=base_sha,
        head_sha=head_sha,
        branch=branch,
        files=files,
        packages=packages,
        claim_type=claim_type,
        claim_mode=claim_mode,
        summary=summary,
        reason=reason,
        transport=str(specification.get("transport")),
    )


def redact_text(value: str) -> str:
    value = URL_PATTERN.sub("<redacted-url>", value)
    value = BEARER_PATTERN.sub("Bearer <redacted>", value)
    return SECRET_ASSIGNMENT_PATTERN.sub(
        lambda match: f"{match.group(1)}=<redacted>", value
    )


def bounded_value(value: Any, *, depth: int = 0) -> Any:
    if depth >= 5:
        return "<truncated>"
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, str):
        return redact_text(value)[:2000]
    if isinstance(value, list):
        return [bounded_value(item, depth=depth + 1) for item in value[:50]]
    if isinstance(value, dict):
        output: dict[str, Any] = {}
        forbidden = {
            "api_url",
            "token",
            "authorization",
            "environment",
            "response_body",
            "password",
            "secret",
            "api_key",
            "access_token",
            "refresh_token",
        }
        for key in sorted(value)[:100]:
            if isinstance(key, str) and key.casefold() not in forbidden:
                output[key] = bounded_value(value[key], depth=depth + 1)
        return output
    return redact_text(str(value))[:500]


def _accepted(action: str, result: Mapping[str, Any]) -> bool:
    if action == "resume":
        return result.get("accepted") is True and result.get("decision") == "allowed"
    if action == "reconcile_base":
        return result.get("accepted") is True and result.get("reconciled_base") is True
    field = {
        "start": "registered",
        "claim": "claimed",
        "release": "released",
        "block": "block",
        "review": "review",
        "done": "done",
        "cancel": "cancel",
    }[action]
    return result.get(field) is True and result.get("decision") not in {
        "blocked",
        "rejected",
        "retry",
    }


def sanitize_result(
    command: Command,
    result: Mapping[str, Any],
    contract: Mapping[str, Any],
) -> dict[str, Any]:
    allowed = set(contract.get("result_fields", ()))
    sanitized = {
        key: bounded_value(result[key]) for key in allowed if key in result
    }
    accepted = _accepted(command.action, result)
    decision = redact_text(
        str(result.get("decision") or ("allowed" if accepted else "rejected"))
    )
    instruction = redact_text(
        str(
            result.get("instruction")
            or (
                "agent_state_command_accepted"
                if accepted
                else "agent_state_command_rejected"
            )
        )
    )
    sanitized.update(
        {
            "accepted": accepted,
            "action": command.action,
            "agent_id": command.agent_id,
            "project": command.project,
            "repository": command.repository,
            "request_id": command.request_id,
            "decision": decision[:100],
            "instruction": instruction[:500],
        }
    )
    if command.action != "resume" and accepted:
        receipt = result.get("receipt_id")
        require(
            isinstance(receipt, str) and receipt,
            "accepted_mutation_missing_receipt",
        )
        sanitized["receipt_id"] = redact_text(receipt)[:256]
    encoded = json.dumps(sanitized, separators=(",", ":"), sort_keys=True)
    require(
        len(encoded.encode("utf-8"))
        <= int(contract["limits"]["result_json_bytes"]),
        "agent_state_result_too_large",
    )
    return sanitized


def result_for_failure(raw: Mapping[str, Any], failure: CommandFailure) -> dict[str, Any]:
    return {
        "accepted": False,
        "action": str(raw.get("action", ""))[:64],
        "agent_id": str(raw.get("agent_id", ""))[:128] or None,
        "project": str(raw.get("project", ""))[:64],
        "repository": str(raw.get("repository", ""))[:200],
        "request_id": str(raw.get("request_id", ""))[:128],
        "decision": failure.decision,
        "instruction": redact_text(failure.instruction)[:500],
        "retryable": failure.retryable,
    }
