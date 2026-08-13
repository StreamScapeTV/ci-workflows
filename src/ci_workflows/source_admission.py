"""Exact event admission and freshness revalidation state machine."""
from __future__ import annotations

from typing import Any, Mapping, Sequence

from .source_evidence import (
    _assert_actor_write,
    _assert_commit,
    _event_pull,
    _expected_pr_freshness,
    _mapping,
    _pr_evidence,
    _repo_branches,
    _result,
    _string,
)
from .source_types import (
    BRANCH,
    TAG,
    TRUSTED_METADATA_EVENTS,
    AdmissionResult,
    EventContext,
    PullRequestEvidence,
    SourceAdmissionError,
    SourceInputs,
    SourceMode,
    SourceProvider,
    TrustMode,
    _full_sha,
    _positive_int,
    _require,
)


def _resolve_tag(
    provider: SourceProvider,
    repository: str,
    tag_name: str,
) -> tuple[str, str]:
    _require(TAG.fullmatch(tag_name) is not None, "invalid_tag_name")
    ref = provider.tag_ref(repository, tag_name)
    obj = _mapping(ref.get("object"), "invalid_tag_ref")
    object_type = _string(obj.get("type"), "invalid_tag_ref")
    current_sha = _full_sha(obj.get("sha"), "invalid_tag_object_sha")
    tag_object_sha = current_sha
    visited: set[str] = set()
    for _ in range(8):
        _require(current_sha not in visited, "tag_object_cycle")
        visited.add(current_sha)
        if object_type == "commit":
            return tag_object_sha, current_sha
        _require(object_type == "tag", "tag_does_not_resolve_to_commit")
        tag = provider.tag_object(repository, current_sha)
        nested = _mapping(tag.get("object"), "invalid_tag_object")
        object_type = _string(nested.get("type"), "invalid_tag_object")
        current_sha = _full_sha(
            nested.get("sha"),
            "invalid_tag_object_sha",
        )
    raise SourceAdmissionError("tag_dereference_too_deep")


def admit_source(
    inputs: SourceInputs,
    event: EventContext,
    provider: SourceProvider,
) -> AdmissionResult:
    """Resolve one exact source SHA without executing caller source."""

    repository, default_branch, integration_branch = _repo_branches(
        event,
        inputs,
        provider,
    )
    mode = inputs.source_mode

    if mode == SourceMode.WORKFLOW_CALL:
        _require(
            inputs.requested_sha is not None,
            "workflow_call_requires_exact_sha",
        )

    if event.event_name == "pull_request":
        _require(
            mode
            in {
                SourceMode.AUTO,
                SourceMode.PR_HEAD,
                SourceMode.PR_MERGE,
                SourceMode.WORKFLOW_CALL,
            },
            "source_mode_event_mismatch",
        )
        event_pr = _pr_evidence(repository, _event_pull(event))
        current_pr = _pr_evidence(
            repository,
            provider.pull_request(repository, event_pr.number),
        )
        if mode == SourceMode.WORKFLOW_CALL:
            if inputs.requested_sha == current_pr.head_sha:
                selected_mode = SourceMode.PR_HEAD
            elif inputs.requested_sha == current_pr.merge_sha:
                selected_mode = SourceMode.PR_MERGE
            else:
                raise SourceAdmissionError("requested_sha_mismatch")
        else:
            selected_mode = (
                SourceMode.PR_HEAD
                if mode == SourceMode.AUTO
                else mode
            )
        _require(event_pr.head_sha == current_pr.head_sha, "stale_pr_head")
        _require(event_pr.base_sha == current_pr.base_sha, "stale_pr_base")
        if selected_mode == SourceMode.PR_MERGE:
            _require(
                current_pr.merge_sha is not None,
                "pull_request_merge_sha_unavailable",
            )
            _require(
                event_pr.merge_sha == current_pr.merge_sha,
                "stale_pr_merge",
            )
        _expected_pr_freshness(inputs, current_pr)
        _require(
            current_pr.base_branch == integration_branch,
            "unexpected_pr_base_branch",
        )
        source_sha = (
            current_pr.merge_sha
            if selected_mode == SourceMode.PR_MERGE
            else current_pr.head_sha
        )
        if inputs.requested_sha is not None:
            _require(
                inputs.requested_sha == source_sha,
                "requested_sha_mismatch",
            )
        source_repository = (
            current_pr.head_repository
            if selected_mode == SourceMode.PR_HEAD
            else repository
        )
        _assert_commit(provider, source_repository, source_sha)
        return _result(
            repository=repository,
            default_branch=default_branch,
            integration_branch=integration_branch,
            trust_mode=TrustMode.UNTRUSTED_VALIDATION,
            source_repository=source_repository,
            source_sha=source_sha,
            inputs=inputs,
            pr=current_pr,
            requires_freshness=True,
        )

    is_tag = event.ref_type == "tag" or event.ref.startswith("refs/tags/")
    if event.event_name == "push" and is_tag:
        _require(
            mode in {SourceMode.AUTO, SourceMode.TAG, SourceMode.WORKFLOW_CALL},
            "source_mode_event_mismatch",
        )
        _require(
            inputs.release_contract is not None,
            "release_contract_required",
        )
        tag_name = event.ref_name or event.ref.removeprefix("refs/tags/")
        tag_object_sha, commit_sha = _resolve_tag(
            provider,
            repository,
            tag_name,
        )
        _require(
            event.sha in {tag_object_sha, commit_sha},
            "tag_event_sha_mismatch",
        )
        if inputs.requested_sha is not None:
            _require(
                inputs.requested_sha == commit_sha,
                "requested_sha_mismatch",
            )
        _assert_commit(provider, repository, commit_sha)
        return _result(
            repository=repository,
            default_branch=default_branch,
            integration_branch=integration_branch,
            trust_mode=TrustMode.TAG_RELEASE,
            source_repository=repository,
            source_sha=commit_sha,
            inputs=inputs,
            tag_name=tag_name,
            tag_object_sha=tag_object_sha,
            tag_commit_sha=commit_sha,
            requires_freshness=False,
        )

    if event.event_name == "push":
        _require(
            mode in {SourceMode.AUTO, SourceMode.PUSH, SourceMode.WORKFLOW_CALL},
            "source_mode_event_mismatch",
        )
        _require(
            event.ref_type in {"", "branch"},
            "push_ref_must_be_branch",
        )
        branch = event.ref_name or event.ref.removeprefix("refs/heads/")
        _require(
            BRANCH.fullmatch(branch) is not None,
            "invalid_push_branch",
        )
        if inputs.expected_branch is not None:
            _require(
                branch == inputs.expected_branch,
                "unexpected_push_branch",
            )
        _require(
            branch == integration_branch,
            "push_branch_not_integration_branch",
        )
        current_sha = provider.branch_sha(repository, branch)
        _require(current_sha == event.sha, "stale_push_source")
        if inputs.requested_sha is not None:
            _require(
                inputs.requested_sha == event.sha,
                "requested_sha_mismatch",
            )
        _assert_commit(provider, repository, event.sha)
        return _result(
            repository=repository,
            default_branch=default_branch,
            integration_branch=integration_branch,
            trust_mode=TrustMode.TRUSTED_VALIDATION,
            source_repository=repository,
            source_sha=event.sha,
            inputs=inputs,
            requires_freshness=True,
        )

    if event.event_name == "workflow_dispatch":
        _require(
            mode
            in {
                SourceMode.AUTO,
                SourceMode.MANUAL,
                SourceMode.WORKFLOW_CALL,
                SourceMode.TRUSTED_MAINTENANCE,
            },
            "source_mode_event_mismatch",
        )
        _assert_actor_write(provider, repository, event)
        source_sha = inputs.requested_sha or event.sha
        _assert_commit(provider, repository, source_sha)
        if mode == SourceMode.TRUSTED_MAINTENANCE:
            protected_sha = provider.branch_sha(repository, default_branch)
            _require(
                event.sha == protected_sha,
                "trusted_maintenance_requires_current_default",
            )
            _require(
                source_sha == protected_sha,
                "trusted_maintenance_source_escalation",
            )
            trust = TrustMode.TRUSTED_MAINTENANCE
        else:
            trust = TrustMode.TRUSTED_VALIDATION
        return _result(
            repository=repository,
            default_branch=default_branch,
            integration_branch=integration_branch,
            trust_mode=trust,
            source_repository=repository,
            source_sha=source_sha,
            inputs=inputs,
            requires_freshness=trust == TrustMode.TRUSTED_MAINTENANCE,
        )

    if event.event_name == "workflow_call":
        _require(
            mode in {SourceMode.WORKFLOW_CALL, SourceMode.TRUSTED_MAINTENANCE},
            "source_mode_event_mismatch",
        )
        _require(
            inputs.requested_sha is not None,
            "workflow_call_requires_exact_sha",
        )
        _assert_commit(provider, repository, inputs.requested_sha)
        if mode == SourceMode.TRUSTED_MAINTENANCE:
            _assert_actor_write(provider, repository, event)
            protected_sha = provider.branch_sha(repository, default_branch)
            _require(
                inputs.requested_sha == protected_sha,
                "trusted_maintenance_source_escalation",
            )
            trust = TrustMode.TRUSTED_MAINTENANCE
        else:
            trust = TrustMode.TRUSTED_VALIDATION
        return _result(
            repository=repository,
            default_branch=default_branch,
            integration_branch=integration_branch,
            trust_mode=trust,
            source_repository=repository,
            source_sha=inputs.requested_sha,
            inputs=inputs,
            requires_freshness=trust == TrustMode.TRUSTED_MAINTENANCE,
        )

    if event.event_name in TRUSTED_METADATA_EVENTS:
        _require(
            mode == SourceMode.TRUSTED_MAINTENANCE,
            "trusted_metadata_mode_required",
        )
        _assert_actor_write(provider, repository, event)
        protected_sha = provider.branch_sha(repository, default_branch)
        _require(
            event.sha == protected_sha,
            "trusted_helper_not_current_default",
        )
        _require(
            inputs.requested_sha in {None, protected_sha},
            "trusted_metadata_source_escalation",
        )
        pr: PullRequestEvidence | None = None
        pr_number = inputs.pr_number
        if pr_number is None:
            if event.event_name == "pull_request_target":
                pr_number = _pr_evidence(
                    repository,
                    _event_pull(event),
                ).number
            elif event.event_name == "issue_comment":
                issue = _mapping(
                    event.payload.get("issue"),
                    "missing_issue_metadata",
                )
                if "pull_request" in issue:
                    pr_number = _positive_int(
                        issue.get("number"),
                        "invalid_pr_number",
                        maximum=2_147_483_647,
                    )
            elif event.event_name == "workflow_run":
                workflow_run = _mapping(
                    event.payload.get("workflow_run"),
                    "missing_workflow_run_metadata",
                )
                pulls = workflow_run.get("pull_requests")
                if isinstance(pulls, Sequence) and pulls:
                    first = _mapping(
                        pulls[0],
                        "invalid_workflow_run_pull_request",
                    )
                    pr_number = _positive_int(
                        first.get("number"),
                        "invalid_pr_number",
                        maximum=2_147_483_647,
                    )
        if pr_number is not None:
            pr = _pr_evidence(
                repository,
                provider.pull_request(repository, pr_number),
            )
            _expected_pr_freshness(inputs, pr)
            _require(
                pr.base_branch == integration_branch,
                "unexpected_pr_base_branch",
            )
        return _result(
            repository=repository,
            default_branch=default_branch,
            integration_branch=integration_branch,
            trust_mode=TrustMode.TRUSTED_MAINTENANCE,
            source_repository=repository,
            source_sha=protected_sha,
            inputs=inputs,
            pr=pr,
            requires_freshness=True,
        )

    raise SourceAdmissionError("unsupported_source_event")


def revalidate_admission(
    result: AdmissionResult,
    provider: SourceProvider,
) -> None:
    """Revalidate mutable evidence immediately before privileged publication."""

    if not result.requires_freshness:
        return
    if result.pr_number is not None:
        current = _pr_evidence(
            result.caller_repository,
            provider.pull_request(
                result.caller_repository,
                result.pr_number,
            ),
        )
        _require(current.head_sha == result.pr_head_sha, "stale_pr_head")
        _require(current.base_sha == result.pr_base_sha, "stale_pr_base")
        if result.pr_merge_sha is not None and result.source_sha == result.pr_merge_sha:
            _require(current.merge_sha == result.pr_merge_sha, "stale_pr_merge")
        return
    if result.trust_mode == TrustMode.TRUSTED_MAINTENANCE:
        current = provider.branch_sha(
            result.caller_repository,
            result.caller_default_branch,
        )
        _require(
            current == result.source_sha,
            "stale_trusted_maintenance_source",
        )
        return
    current = provider.branch_sha(
        result.caller_repository,
        result.caller_integration_branch,
    )
    _require(current == result.source_sha, "stale_push_source")
