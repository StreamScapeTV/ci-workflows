"""Shared semantic and source-shape helpers for workflow validation."""
from __future__ import annotations

import re
from pathlib import PurePosixPath
from typing import Any, Iterator, Mapping

from .validation_model import (
    _ACTION_RE,
    _GITHUB_EXPR_RE,
    _REMOTE_WORKFLOW_RE,
    _SEMVER_RE,
    _SHA_RE,
    _USES_LINE_RE,
    ActionLock,
    Finding,
    HarnessConfig,
    ParsedDocument,
)


def _finding(
    findings: list[Finding],
    config: HarnessConfig,
    rule: str,
    relative_path: str,
    message: str,
    line: int = 0,
) -> None:
    if not config.excepts(relative_path, rule):
        findings.append(Finding(rule=rule, path=relative_path, message=message, line=line))


def _events(document: ParsedDocument) -> set[str]:
    trigger = document.data.get("on", {})
    if isinstance(trigger, str):
        return {trigger}
    if isinstance(trigger, list):
        return {str(value) for value in trigger}
    if isinstance(trigger, Mapping):
        return {str(key) for key in trigger}
    return set()


def _iter_jobs(document: ParsedDocument) -> Iterator[tuple[str, Mapping[str, Any]]]:
    jobs = document.data.get("jobs", {})
    if isinstance(jobs, Mapping):
        for job_id, job in jobs.items():
            if isinstance(job, Mapping):
                yield str(job_id), job


def _iter_steps(job: Mapping[str, Any]) -> Iterator[tuple[int, Mapping[str, Any]]]:
    steps = job.get("steps", [])
    if isinstance(steps, list):
        for index, step in enumerate(steps, start=1):
            if isinstance(step, Mapping):
                yield index, step


def _run_line_count(run: Any) -> int:
    if not isinstance(run, str):
        return 0
    return sum(1 for line in run.splitlines() if line.strip())


def _normalize_run(run: str) -> str:
    stripped = []
    for line in run.splitlines():
        value = line.strip()
        if not value or value.startswith("#"):
            continue
        value = _GITHUB_EXPR_RE.sub("${{EXPR}}", value)
        stripped.append(re.sub(r"\s+", " ", value))
    return "\n".join(stripped)


def _is_public_workflow(relative_path: str) -> bool:
    name = PurePosixPath(relative_path).name
    return name.startswith("reusable-") and name.endswith((".yml", ".yaml"))


def _is_internal_workflow(relative_path: str) -> bool:
    name = PurePosixPath(relative_path).name
    return name.startswith("internal-") and name.endswith((".yml", ".yaml"))


def _permission_mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _matrix_size(matrix: Any) -> int | None:
    if not isinstance(matrix, Mapping):
        return None
    product = 1
    saw_axis = False
    include_count = 0
    for key, value in matrix.items():
        if str(key) == "include":
            if not isinstance(value, list):
                return None
            include_count = len(value)
            continue
        if str(key) == "exclude":
            if not isinstance(value, list):
                return None
            continue
        saw_axis = True
        if not isinstance(value, list):
            return None
        product *= len(value)
    if not saw_axis:
        return include_count
    return product + include_count


def _uses_entries(document: ParsedDocument) -> Iterator[tuple[str, str | None, int]]:
    for line_number, line in enumerate(document.raw.splitlines(), start=1):
        match = _USES_LINE_RE.match(line)
        if match:
            yield match.group("uses"), match.group("comment"), line_number


def _validate_action_reference(
    uses: str,
    comment: str | None,
    relative_path: str,
    line_number: int,
    config: HarnessConfig,
    lock: ActionLock,
    findings: list[Finding],
) -> None:
    if uses.startswith("./"):
        if uses.startswith("./.github/workflows/"):
            return
        normalized = uses[2:].rstrip("/") + "/"
        if not any(
            normalized.startswith(prefix.rstrip("/") + "/")
            for prefix in lock.approved_internal_prefixes
        ):
            _finding(
                findings,
                config,
                "unapproved-internal-action",
                relative_path,
                f"local action {uses!r} is outside approved internal action prefixes",
                line_number,
            )
        return
    remote_workflow = _REMOTE_WORKFLOW_RE.match(uses)
    if remote_workflow:
        if remote_workflow.group("owner") != "StreamScapeTV":
            _finding(
                findings,
                config,
                "unapproved-reusable-workflow",
                relative_path,
                f"reusable workflow owner is not approved: {uses}",
                line_number,
            )
        ref = remote_workflow.group("ref")
        if ref != "main" and not _SHA_RE.fullmatch(ref) and not _SEMVER_RE.fullmatch(ref):
            _finding(
                findings,
                config,
                "mutable-workflow-reference",
                relative_path,
                f"unsupported reusable workflow reference {ref!r}",
                line_number,
            )
        return
    match = _ACTION_RE.match(uses)
    if not match:
        _finding(
            findings,
            config,
            "invalid-action-reference",
            relative_path,
            f"cannot classify uses reference {uses!r}",
            line_number,
        )
        return
    action_key = f"{match.group('owner')}/{match.group('repo')}{match.group('path') or ''}"
    ref = match.group("ref")
    entry = lock.actions.get(action_key)
    if entry is None:
        _finding(
            findings,
            config,
            "unapproved-action",
            relative_path,
            f"third-party action {action_key!r} is not in contracts/action-tool-lock.json",
            line_number,
        )
        return
    if ref != entry["sha"] or not _SHA_RE.fullmatch(ref):
        _finding(
            findings,
            config,
            "unpinned-action",
            relative_path,
            f"{action_key} must use exact SHA {entry['sha']}",
            line_number,
        )
    if comment is None or entry["release"] not in comment:
        _finding(
            findings,
            config,
            "missing-action-release-comment",
            relative_path,
            f"{action_key} must retain human-readable release comment {entry['release']}",
            line_number,
        )
    if not re.fullmatch(r"node(?:20|24)|composite|docker", entry["runtime"]):
        _finding(
            findings,
            config,
            "invalid-action-runtime",
            relative_path,
            f"locked runtime generation {entry['runtime']!r} is unsupported",
            line_number,
        )


def _validate_checkout_step(
    step: Mapping[str, Any],
    document: ParsedDocument,
    config: HarnessConfig,
    findings: list[Finding],
) -> None:
    uses = str(step.get("uses", ""))
    if not uses.startswith("actions/checkout@"):
        return
    with_values = step.get("with")
    if not isinstance(with_values, Mapping):
        _finding(
            findings,
            config,
            "unsafe-checkout",
            document.relative_path,
            "checkout requires explicit with settings",
        )
        return
    if with_values.get("persist-credentials") is not False:
        _finding(
            findings,
            config,
            "persisted-checkout-credentials",
            document.relative_path,
            "actions/checkout must set persist-credentials: false",
        )
    if with_values.get("clean") is not True:
        _finding(
            findings,
            config,
            "unclean-checkout",
            document.relative_path,
            "actions/checkout must set clean: true",
        )
    if "fetch-depth" not in with_values:
        _finding(
            findings,
            config,
            "unbounded-checkout",
            document.relative_path,
            "actions/checkout must set fetch-depth",
        )
    ref = with_values.get("ref")
    if not isinstance(ref, str) or not ref.strip():
        _finding(
            findings,
            config,
            "mutable-checkout",
            document.relative_path,
            "actions/checkout must select an explicit ref",
        )


def _validate_runner(
    runner: Any,
    relative_path: str,
    config: HarnessConfig,
    findings: list[Finding],
) -> None:
    labels: list[str]
    if isinstance(runner, str):
        labels = [runner]
    elif isinstance(runner, list):
        labels = [str(value) for value in runner]
    else:
        _finding(
            findings,
            config,
            "invalid-runner",
            relative_path,
            "runs-on must be a string or bounded list",
        )
        return
    if "self-hosted" in labels:
        _finding(
            findings,
            config,
            "bare-self-hosted",
            relative_path,
            "bare self-hosted runner identity is forbidden",
        )
    for label in labels:
        if "${{" in label:
            _finding(
                findings,
                config,
                "dynamic-runner",
                relative_path,
                "runner labels may not be caller-controlled expressions",
            )
        elif label == "self-hosted":
            continue
        elif label not in config.allowed_runner_profiles:
            _finding(
                findings,
                config,
                "unknown-runner-profile",
                relative_path,
                f"unknown semantic runner profile {label!r}",
            )


def _workflow_call_contract(document: ParsedDocument) -> Mapping[str, Any]:
    trigger = document.data.get("on", {})
    if isinstance(trigger, Mapping):
        value = trigger.get("workflow_call", {})
        return value if isinstance(value, Mapping) else {}
    return {}


def _actual_names(value: Any) -> set[str]:
    return {str(key) for key in value} if isinstance(value, Mapping) else set()


def _contract_input_map(record: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    return {
        str(item["name"]): item
        for item in record.get("inputs", [])
        if isinstance(item, Mapping)
    }
