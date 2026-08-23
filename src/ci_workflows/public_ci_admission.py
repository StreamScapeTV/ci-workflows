"""Public-repository self-CI event and runner-admission policy."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Mapping

from .validation_helpers import _events, _finding, _iter_jobs
from .validation_model import Finding, HarnessConfig, ParsedDocument

_FORBIDDEN_PUBLIC_ENTRY_EVENTS = frozenset(
    {"issue_comment", "pull_request_target", "repository_dispatch", "workflow_run"}
)
_BROAD_PR_TRUST_MARKERS = ("author_association",)


def _disabled_job(condition: object) -> bool:
    value = "".join(str(condition).lower().split())
    return value in {"false", "${{false}}"}


def _load_policy(root: Path, config: HarnessConfig, findings: list[Finding]) -> Mapping[str, object] | None:
    path = root / "contracts/repository-policy.json"
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        _finding(
            findings,
            config,
            "invalid-public-ci-admission-policy",
            "contracts/repository-policy.json",
            f"repository policy is not valid JSON: {error}",
        )
        return None
    admission = payload.get("workflow_admission")
    if not isinstance(admission, Mapping):
        _finding(
            findings,
            config,
            "missing-public-ci-admission-policy",
            "contracts/repository-policy.json",
            "public repository policy must define workflow_admission",
        )
        return None
    if admission.get("repository_visibility") != "public":
        _finding(
            findings,
            config,
            "invalid-public-ci-visibility",
            "contracts/repository-policy.json",
            "workflow_admission.repository_visibility must be public",
        )
    return admission


def validate_public_ci_admission(
    root: Path,
    workflow_documents: Mapping[str, ParsedDocument],
    config: HarnessConfig,
    findings: list[Finding],
) -> None:
    """Validate exact event classes and fail-closed PR runner admission."""

    admission = _load_policy(root, config, findings)
    if admission is None:
        return

    owner = admission.get("exact_owner_login")
    records = admission.get("workflows")
    if not isinstance(owner, str) or not owner.strip():
        _finding(
            findings,
            config,
            "invalid-public-ci-owner",
            "contracts/repository-policy.json",
            "workflow_admission.exact_owner_login must be a non-empty login",
        )
        return
    if not isinstance(records, Mapping):
        _finding(
            findings,
            config,
            "invalid-public-ci-workflow-inventory",
            "contracts/repository-policy.json",
            "workflow_admission.workflows must map every workflow path to its reviewed class",
        )
        return

    actual_paths = set(workflow_documents)
    reviewed_paths = {str(path) for path in records}
    for path in sorted(actual_paths - reviewed_paths):
        _finding(
            findings,
            config,
            "unclassified-public-ci-workflow",
            path,
            "workflow is missing an explicit reviewed event/trust classification",
        )
    for path in sorted(reviewed_paths - actual_paths):
        _finding(
            findings,
            config,
            "stale-public-ci-workflow-classification",
            path,
            "repository policy classifies a workflow that does not exist",
        )

    owner_fragment = f"github.event.pull_request.user.login == '{owner}'"
    repository_fragment = (
        "github.event.pull_request.head.repo.full_name == github.repository"
    )

    for path in sorted(actual_paths & reviewed_paths):
        record = records[path]
        document = workflow_documents[path]
        if not isinstance(record, Mapping):
            _finding(
                findings,
                config,
                "invalid-public-ci-workflow-classification",
                path,
                "workflow classification must be an object",
            )
            continue
        trust_class = record.get("trust_class")
        allowed_events = record.get("allowed_events")
        if not isinstance(trust_class, str) or not trust_class.strip():
            _finding(
                findings,
                config,
                "invalid-public-ci-trust-class",
                path,
                "workflow classification requires a non-empty trust_class",
            )
        if not isinstance(allowed_events, list) or not all(
            isinstance(event, str) and event for event in allowed_events
        ):
            _finding(
                findings,
                config,
                "invalid-public-ci-event-class",
                path,
                "workflow classification requires a non-empty allowed_events list",
            )
            continue
        expected_events = set(allowed_events)
        events = _events(document)
        if events != expected_events:
            _finding(
                findings,
                config,
                "public-ci-event-drift",
                path,
                f"reviewed events are {sorted(expected_events)}, found {sorted(events)}",
            )

        forbidden = events & _FORBIDDEN_PUBLIC_ENTRY_EVENTS
        if forbidden:
            _finding(
                findings,
                config,
                "forbidden-public-ci-entry-event",
                path,
                f"public repository workflow exposes forbidden entry events {sorted(forbidden)}",
            )

        name = Path(path).name
        if name.startswith("reusable-") and (
            trust_class != "reusable-call" or events != {"workflow_call"}
        ):
            _finding(
                findings,
                config,
                "invalid-public-reusable-admission-class",
                path,
                "reusable workflows must be classified reusable-call and expose workflow_call only",
            )
        if name.startswith("internal-") and (
            trust_class != "internal-call" or events != {"workflow_call"}
        ):
            _finding(
                findings,
                config,
                "invalid-internal-admission-class",
                path,
                "internal workflows must be classified internal-call and expose workflow_call only",
            )

        if "pull_request" not in events:
            continue
        if "github.event.repository.private" in document.raw:
            _finding(
                findings,
                config,
                "public-ci-private-visibility-gate",
                path,
                "public repository PR admission may not depend on repository.private",
            )
        if any(marker in document.raw for marker in _BROAD_PR_TRUST_MARKERS):
            _finding(
                findings,
                config,
                "broad-public-ci-pr-trust",
                path,
                "PR runner admission must not use author-association trust classes",
            )
        for job_id, job in _iter_jobs(document):
            condition = job.get("if", "")
            if _disabled_job(condition):
                continue
            normalized = " ".join(str(condition).split())
            if owner_fragment not in normalized or repository_fragment not in normalized:
                _finding(
                    findings,
                    config,
                    "public-ci-pr-runner-admission",
                    path,
                    f"job {job_id!r} can be reached by pull_request without exact owner and same-repository admission before runner allocation",
                )


__all__ = ("validate_public_ci_admission",)
