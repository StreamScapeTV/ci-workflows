"""Current GitHub metadata evidence and stable admission result construction."""
from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

from .source_types import (
    BRANCH,
    REPOSITORY,
    SAFE_ID,
    WRITE_PERMISSIONS,
    AdmissionResult,
    EventContext,
    PullRequestEvidence,
    SourceInputs,
    SourceProvider,
    TrustMode,
    _full_sha,
    _optional_sha,
    _positive_int,
    _require,
)


def _mapping(value: Any, instruction: str) -> Mapping[str, Any]:
    _require(isinstance(value, Mapping), instruction)
    return value


def _string(value: Any, instruction: str) -> str:
    _require(isinstance(value, str) and bool(value), instruction)
    return value


def _pr_evidence(repository: str, raw: Mapping[str, Any]) -> PullRequestEvidence:
    head = _mapping(raw.get("head"), "invalid_pull_request_metadata")
    base = _mapping(raw.get("base"), "invalid_pull_request_metadata")
    head_repo = _mapping(head.get("repo"), "invalid_pull_request_metadata")
    head_repository = _string(
        head_repo.get("full_name"), "invalid_pull_request_metadata"
    )
    _require(
        REPOSITORY.fullmatch(head_repository) is not None,
        "invalid_pull_request_metadata",
    )
    head_sha = _full_sha(head.get("sha"), "invalid_pull_request_head_sha")
    base_sha = _full_sha(base.get("sha"), "invalid_pull_request_base_sha")
    base_branch = _string(base.get("ref"), "invalid_pull_request_base_branch")
    _require(
        BRANCH.fullmatch(base_branch) is not None,
        "invalid_pull_request_base_branch",
    )
    base_repo = _mapping(base.get("repo"), "invalid_pull_request_metadata")
    _require(
        base_repo.get("full_name") == repository,
        "pull_request_base_repository_mismatch",
    )
    merge_sha = _optional_sha(
        raw.get("merge_commit_sha"), "invalid_pull_request_merge_sha"
    )
    number = _positive_int(
        raw.get("number"), "invalid_pr_number", maximum=2_147_483_647
    )
    return PullRequestEvidence(
        number,
        head_repository,
        head_sha,
        base_branch,
        base_sha,
        merge_sha,
    )


def _event_pull(event: EventContext) -> Mapping[str, Any]:
    return _mapping(
        event.payload.get("pull_request"),
        "missing_pull_request_metadata",
    )


def _repo_branches(
    event: EventContext,
    inputs: SourceInputs,
    provider: SourceProvider,
) -> tuple[str, str, str]:
    repository = inputs.caller_repository or event.repository
    _require(repository == event.repository, "caller_repository_mismatch")
    metadata = provider.repository(repository)
    default_branch = _string(
        metadata.get("default_branch"),
        "repository_default_branch_missing",
    )
    _require(
        BRANCH.fullmatch(default_branch) is not None,
        "repository_default_branch_invalid",
    )
    if inputs.caller_default_branch is not None:
        _require(
            inputs.caller_default_branch == default_branch,
            "caller_default_branch_mismatch",
        )
    integration_branch = (
        inputs.caller_integration_branch
        or inputs.expected_branch
        or default_branch
    )
    _require(
        BRANCH.fullmatch(integration_branch) is not None,
        "caller_integration_branch_invalid",
    )
    return repository, default_branch, integration_branch


def _assert_commit(
    provider: SourceProvider,
    repository: str,
    sha: str,
) -> None:
    metadata = provider.commit(repository, sha)
    _require(metadata.get("sha") == sha, "resolved_commit_mismatch")


def _assert_actor_write(
    provider: SourceProvider,
    repository: str,
    event: EventContext,
) -> None:
    _require(event.actor == event.triggering_actor, "triggering_actor_mismatch")
    permission = provider.collaborator_permission(repository, event.actor)
    _require(
        permission in WRITE_PERMISSIONS,
        "manual_actor_not_authorized",
    )


def _expected_pr_freshness(
    inputs: SourceInputs,
    evidence: PullRequestEvidence,
) -> None:
    if inputs.pr_number is not None:
        _require(inputs.pr_number == evidence.number, "pr_number_mismatch")
    if inputs.expected_pr_head_sha is not None:
        _require(
            inputs.expected_pr_head_sha == evidence.head_sha,
            "stale_pr_head",
        )
    if inputs.expected_pr_base_sha is not None:
        _require(
            inputs.expected_pr_base_sha == evidence.base_sha,
            "stale_pr_base",
        )
    if inputs.expected_pr_merge_sha is not None:
        _require(
            inputs.expected_pr_merge_sha == evidence.merge_sha,
            "stale_pr_merge",
        )


def _stable_id(prefix: str, evidence: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        evidence,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    digest = hashlib.sha256(encoded).hexdigest()[:24]
    result = f"{prefix}-{digest}"
    _require(
        SAFE_ID.fullmatch(result) is not None,
        "stable_identifier_failure",
    )
    return result


def _result(
    *,
    repository: str,
    default_branch: str,
    integration_branch: str,
    trust_mode: TrustMode,
    source_repository: str,
    source_sha: str,
    inputs: SourceInputs,
    pr: PullRequestEvidence | None = None,
    tag_name: str | None = None,
    tag_object_sha: str | None = None,
    tag_commit_sha: str | None = None,
    requires_freshness: bool,
) -> AdmissionResult:
    base_evidence: dict[str, Any] = {
        "repository": repository,
        "default_branch": default_branch,
        "integration_branch": integration_branch,
        "trust_mode": trust_mode.value,
        "source_repository": source_repository,
        "source_sha": source_sha,
        "requested_sha": inputs.requested_sha,
        "pr_number": None if pr is None else pr.number,
        "pr_head_repository": None if pr is None else pr.head_repository,
        "pr_head_sha": None if pr is None else pr.head_sha,
        "pr_base_branch": None if pr is None else pr.base_branch,
        "pr_base_sha": None if pr is None else pr.base_sha,
        "pr_merge_sha": None if pr is None else pr.merge_sha,
        "tag_name": tag_name,
        "tag_object_sha": tag_object_sha,
        "tag_commit_sha": tag_commit_sha,
        "history_depth": inputs.history_depth,
    }
    request_id = _stable_id(
        "source",
        {
            "repository": repository,
            "trust_mode": trust_mode.value,
            "source_sha": source_sha,
            "pr_number": None if pr is None else pr.number,
            "tag_name": tag_name,
        },
    )
    evidence_id = _stable_id("evidence", base_evidence)
    return AdmissionResult(
        caller_repository=repository,
        caller_default_branch=default_branch,
        caller_integration_branch=integration_branch,
        trust_mode=trust_mode,
        source_repository=source_repository,
        source_sha=source_sha,
        requested_sha=inputs.requested_sha,
        resolved_sha=source_sha,
        pr_number=None if pr is None else pr.number,
        pr_head_repository=None if pr is None else pr.head_repository,
        pr_head_sha=None if pr is None else pr.head_sha,
        pr_base_branch=None if pr is None else pr.base_branch,
        pr_base_sha=None if pr is None else pr.base_sha,
        pr_merge_sha=None if pr is None else pr.merge_sha,
        tag_name=tag_name,
        tag_object_sha=tag_object_sha,
        tag_commit_sha=tag_commit_sha,
        requires_freshness=requires_freshness,
        history_depth=inputs.history_depth,
        request_id=request_id,
        evidence_id=evidence_id,
    )
