"""Typed exact-source contracts and bounded input validation."""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Mapping, Protocol

FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
BRANCH = re.compile(r"^[A-Za-z0-9._/-]{1,255}$")
TAG = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,254}$")
SLUG = re.compile(r"^[a-z][a-z0-9-]{1,63}$")
SAFE_ID = re.compile(r"^[a-z][a-z0-9-]{7,63}$")
WRITE_PERMISSIONS = frozenset({"admin", "maintain", "write"})
TRUSTED_METADATA_EVENTS = frozenset(
    {"workflow_run", "issue_comment", "pull_request_target"}
)


class TrustMode(str, Enum):
    UNTRUSTED_VALIDATION = "untrusted-validation"
    TRUSTED_VALIDATION = "trusted-validation"
    TAG_RELEASE = "tag-release"
    TRUSTED_MAINTENANCE = "trusted-maintenance"


class SourceMode(str, Enum):
    AUTO = "auto"
    PR_HEAD = "pr-head"
    PR_MERGE = "pr-merge"
    PUSH = "push"
    MANUAL = "manual"
    WORKFLOW_CALL = "workflow-call"
    TAG = "tag"
    TRUSTED_MAINTENANCE = "trusted-maintenance"


class SourceAdmissionError(RuntimeError):
    """Fail-closed error with a stable, non-secret instruction code."""

    def __init__(self, instruction: str) -> None:
        if not re.fullmatch(r"[a-z][a-z0-9_]{2,95}", instruction):
            raise ValueError("source admission instruction must be a safe code")
        self.instruction = instruction
        super().__init__(instruction)


@dataclass(frozen=True)
class SourceInputs:
    source_mode: SourceMode
    requested_sha: str | None
    expected_branch: str | None
    release_contract: str | None
    history_depth: int
    caller_repository: str | None
    caller_default_branch: str | None
    caller_integration_branch: str | None
    pr_number: int | None
    expected_pr_head_sha: str | None
    expected_pr_base_sha: str | None
    expected_pr_merge_sha: str | None


@dataclass(frozen=True)
class EventContext:
    event_name: str
    repository: str
    sha: str
    ref: str
    ref_name: str
    ref_type: str
    actor: str
    triggering_actor: str
    workflow_sha: str
    payload: Mapping[str, Any]


@dataclass(frozen=True)
class PullRequestEvidence:
    number: int
    head_repository: str
    head_sha: str
    base_branch: str
    base_sha: str
    merge_sha: str | None


@dataclass(frozen=True)
class AdmissionResult:
    caller_repository: str
    caller_default_branch: str
    caller_integration_branch: str
    trust_mode: TrustMode
    source_repository: str
    source_sha: str
    requested_sha: str | None
    resolved_sha: str
    pr_number: int | None
    pr_head_repository: str | None
    pr_head_sha: str | None
    pr_base_branch: str | None
    pr_base_sha: str | None
    pr_merge_sha: str | None
    tag_name: str | None
    tag_object_sha: str | None
    tag_commit_sha: str | None
    requires_freshness: bool
    history_depth: int
    request_id: str
    evidence_id: str

    def output_values(self) -> dict[str, str]:
        raw = asdict(self)
        values: dict[str, str] = {}
        for key, value in raw.items():
            if isinstance(value, Enum):
                values[key] = str(value.value)
            elif isinstance(value, bool):
                values[key] = "true" if value else "false"
            elif value is None:
                values[key] = ""
            else:
                values[key] = str(value)
        return values


class SourceProvider(Protocol):
    def repository(self, repository: str) -> Mapping[str, Any]: ...

    def collaborator_permission(self, repository: str, actor: str) -> str: ...

    def pull_request(self, repository: str, number: int) -> Mapping[str, Any]: ...

    def commit(self, repository: str, sha: str) -> Mapping[str, Any]: ...

    def branch_sha(self, repository: str, branch: str) -> str: ...

    def tag_ref(self, repository: str, tag_name: str) -> Mapping[str, Any]: ...

    def tag_object(self, repository: str, sha: str) -> Mapping[str, Any]: ...


def _require(condition: bool, instruction: str) -> None:
    if not condition:
        raise SourceAdmissionError(instruction)


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise SourceAdmissionError("invalid_input_type")
    stripped = value.strip()
    return stripped or None


def _full_sha(value: Any, instruction: str) -> str:
    _require(isinstance(value, str) and FULL_SHA.fullmatch(value) is not None, instruction)
    return value


def _optional_sha(value: Any, instruction: str) -> str | None:
    candidate = _optional_string(value)
    return None if candidate is None else _full_sha(candidate, instruction)


def _positive_int(value: Any, instruction: str, *, maximum: int = 1000) -> int:
    if isinstance(value, bool):
        raise SourceAdmissionError(instruction)
    try:
        parsed = int(value)
    except (TypeError, ValueError) as error:
        raise SourceAdmissionError(instruction) from error
    _require(1 <= parsed <= maximum, instruction)
    return parsed


def load_contract(root: Path) -> Mapping[str, Any]:
    path = root / "contracts/source-admission.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise SourceAdmissionError("source_contract_unavailable") from error
    _require(isinstance(data, dict), "source_contract_invalid")
    _require(data.get("schema_version") == 1, "source_contract_schema_unsupported")
    _require(
        data.get("organization") == "StreamScapeTV",
        "source_contract_organization_mismatch",
    )
    return data


def validate_inputs(raw: Mapping[str, Any], contract: Mapping[str, Any]) -> SourceInputs:
    allowed_inputs = contract.get("allowed_inputs")
    _require(isinstance(allowed_inputs, list), "source_contract_invalid")
    unknown = sorted(set(raw) - set(str(item) for item in allowed_inputs))
    _require(not unknown, "unsupported_source_input")

    raw_mode = _optional_string(raw.get("source_mode")) or SourceMode.AUTO.value
    try:
        source_mode = SourceMode(raw_mode)
    except ValueError as error:
        raise SourceAdmissionError("unsupported_source_mode") from error

    requested_sha = _optional_sha(
        raw.get("requested_sha"), "requested_sha_must_be_full_sha"
    )
    expected_branch = _optional_string(raw.get("expected_branch"))
    if expected_branch is not None:
        _require(
            BRANCH.fullmatch(expected_branch) is not None
            and not expected_branch.startswith("refs/"),
            "invalid_expected_branch",
        )
    release_contract = _optional_string(raw.get("release_contract"))
    if release_contract is not None:
        _require(
            SLUG.fullmatch(release_contract) is not None,
            "invalid_release_contract",
        )
    history_depth = _positive_int(
        raw.get("history_depth", 1), "invalid_history_depth"
    )

    caller_repository = _optional_string(raw.get("caller_repository"))
    if caller_repository is not None:
        _require(
            REPOSITORY.fullmatch(caller_repository) is not None,
            "invalid_caller_repository",
        )
    caller_default_branch = _optional_string(raw.get("caller_default_branch"))
    caller_integration_branch = _optional_string(raw.get("caller_integration_branch"))
    for branch in (caller_default_branch, caller_integration_branch):
        if branch is not None:
            _require(BRANCH.fullmatch(branch) is not None, "invalid_caller_branch")

    pr_number_raw = raw.get("pr_number")
    pr_number = None
    if pr_number_raw not in (None, "", 0, "0"):
        pr_number = _positive_int(
            pr_number_raw,
            "invalid_pr_number",
            maximum=2_147_483_647,
        )

    expected_pr_head_sha = _optional_sha(
        raw.get("expected_pr_head_sha"),
        "expected_pr_head_sha_must_be_full_sha",
    )
    expected_pr_base_sha = _optional_sha(
        raw.get("expected_pr_base_sha"),
        "expected_pr_base_sha_must_be_full_sha",
    )
    expected_pr_merge_sha = _optional_sha(
        raw.get("expected_pr_merge_sha"),
        "expected_pr_merge_sha_must_be_full_sha",
    )
    expected_pr_values = (
        expected_pr_head_sha,
        expected_pr_base_sha,
        expected_pr_merge_sha,
    )
    if any(value is not None for value in expected_pr_values):
        _require(pr_number is not None, "pr_number_required_for_freshness")
        _require(
            expected_pr_head_sha is not None and expected_pr_base_sha is not None,
            "pr_head_and_base_required_for_freshness",
        )

    return SourceInputs(
        source_mode=source_mode,
        requested_sha=requested_sha,
        expected_branch=expected_branch,
        release_contract=release_contract,
        history_depth=history_depth,
        caller_repository=caller_repository,
        caller_default_branch=caller_default_branch,
        caller_integration_branch=caller_integration_branch,
        pr_number=pr_number,
        expected_pr_head_sha=expected_pr_head_sha,
        expected_pr_base_sha=expected_pr_base_sha,
        expected_pr_merge_sha=expected_pr_merge_sha,
    )


def load_event_context(
    environment: Mapping[str, str], payload: Mapping[str, Any]
) -> EventContext:
    repository = environment.get("GITHUB_REPOSITORY", "")
    _require(REPOSITORY.fullmatch(repository) is not None, "invalid_event_repository")
    sha = _full_sha(environment.get("GITHUB_SHA", ""), "event_sha_must_be_full_sha")
    workflow_sha = environment.get("GITHUB_WORKFLOW_SHA", sha)
    workflow_sha = _full_sha(workflow_sha, "workflow_sha_must_be_full_sha")
    event_name = environment.get("GITHUB_EVENT_NAME", "").strip()
    _require(bool(event_name), "missing_event_name")
    actor = environment.get("GITHUB_ACTOR", "").strip()
    triggering_actor = environment.get("GITHUB_TRIGGERING_ACTOR", actor).strip()
    _require(bool(actor) and bool(triggering_actor), "missing_event_actor")
    return EventContext(
        event_name=event_name,
        repository=repository,
        sha=sha,
        ref=environment.get("GITHUB_REF", ""),
        ref_name=environment.get("GITHUB_REF_NAME", ""),
        ref_type=environment.get("GITHUB_REF_TYPE", ""),
        actor=actor,
        triggering_actor=triggering_actor,
        workflow_sha=workflow_sha,
        payload=payload,
    )
