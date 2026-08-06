"""Workflow, action, readability, source, trust, and cleanup policy checks."""
from __future__ import annotations

import collections
import re
from pathlib import Path
from typing import Any, Mapping

from .validation_contracts import _validate_public_compatibility
from .validation_helpers import (
    _events,
    _finding,
    _is_public_workflow,
    _iter_jobs,
    _iter_steps,
    _matrix_size,
    _normalize_run,
    _permission_mapping,
    _run_line_count,
    _uses_entries,
    _validate_action_reference,
    _validate_checkout_step,
    _validate_runner,
)
from .validation_model import (
    _CREDENTIAL_WORDS,
    _HIGH_RISK_EVENTS,
    _PUBLICATION_WORDS,
    _READBACK_WORDS,
    _TEMP_STATE_WORDS,
    _JOB_ID_RE,
    ActionLock,
    Finding,
    HarnessConfig,
    ParsedDocument,
    PublicContract,
)


def _contains_mutable_publication_tag(raw: str) -> bool:
    """Return true only when a publication command itself selects ``latest``."""

    latest = re.compile(r"(?:^|[^A-Za-z0-9_.-])latest(?:[^A-Za-z0-9_.-]|$)")
    for line in raw.lower().splitlines():
        if any(word in line for word in _PUBLICATION_WORDS) and latest.search(line):
            return True
    return False


def _is_always_cleanup_step(step: Mapping[str, object]) -> bool:
    """Recognize explicit unconditional cleanup steps by condition and name."""

    condition = str(step.get("if", "")).replace(" ", "")
    name = str(step.get("name", ""))
    return condition in {"always()", "${{always()}}"} and re.search(
        r"clean(?:up)?|remove|purge|erase", name, re.IGNORECASE
    ) is not None


def _validate_workflow(
    document: ParsedDocument,
    root: Path,
    config: HarnessConfig,
    lock: ActionLock,
    public_contract: PublicContract,
    functions: Mapping[str, tuple[str, int]],
    docs_text: str,
    findings: list[Finding],
    duplicated_runs: dict[str, list[str]],
) -> None:
    path = document.relative_path
    data = document.data
    if not isinstance(data.get("name"), str) or not str(data.get("name")).strip():
        _finding(findings, config, "opaque-workflow-name", path, "workflow requires a human-readable name")
    if "on" not in data:
        _finding(findings, config, "missing-trigger", path, "workflow must contain the literal on key")
    if "permissions" not in data or data.get("permissions") in (None, "write-all"):
        _finding(findings, config, "implicit-permissions", path, "workflow must declare explicit least-privilege permissions")
    permissions = _permission_mapping(data.get("permissions"))
    for permission, value in permissions.items():
        if value not in {"read", "write", "none"}:
            _finding(findings, config, "invalid-permission", path, f"permission {permission!r} has invalid value {value!r}")
    events = _events(document)
    if _is_public_workflow(path) and events != {"workflow_call"}:
        _finding(findings, config, "public-workflow-trigger", path, "public reusable workflows must expose workflow_call only")
    raw_lower = document.raw.lower()
    has_checkout = "actions/checkout@" in raw_lower
    if has_checkout and "git rev-parse head" not in raw_lower and "./actions/exact-checkout" not in raw_lower:
        _finding(findings, config, "missing-exact-head-assertion", path, "checkout workflows must assert the exact checked-out HEAD")
    if events & _HIGH_RISK_EVENTS and has_checkout:
        _finding(
            findings,
            config,
            "untrusted-privileged-checkout",
            path,
            f"high-risk events {sorted(events & _HIGH_RISK_EVENTS)} may not checkout or execute source",
        )
    if "secrets: inherit" in raw_lower:
        _finding(findings, config, "secrets-inherit", path, "secrets: inherit is forbidden")
    if "pull_request" in events and any(word in raw_lower for word in _PUBLICATION_WORDS):
        _finding(findings, config, "publication-from-pr", path, "pull-request workflows may not publish images or charts")
    if any(word in raw_lower for word in _PUBLICATION_WORDS):
        if _contains_mutable_publication_tag(document.raw):
            _finding(findings, config, "mutable-release-tag", path, "publication may not use latest")
        if not any(word in raw_lower for word in _READBACK_WORDS):
            _finding(findings, config, "missing-publication-readback", path, "publication requires independent remote read-back")
    if any(word in raw_lower for word in _CREDENTIAL_WORDS + _TEMP_STATE_WORDS):
        cleanup_steps = [
            step
            for _, job in _iter_jobs(document)
            for _, step in _iter_steps(job)
            if _is_always_cleanup_step(step)
        ]
        if not cleanup_steps:
            _finding(findings, config, "missing-always-cleanup", path, "credential or temporary state requires an if: always() cleanup step")

    run_occurrences: dict[str, int] = collections.Counter()
    for job_id, job in _iter_jobs(document):
        if not _JOB_ID_RE.fullmatch(job_id):
            _finding(findings, config, "opaque-job-id", path, f"job id {job_id!r} is not readable snake_case")
        if not isinstance(job.get("name"), str) or not str(job.get("name")).strip():
            _finding(findings, config, "opaque-job-name", path, f"job {job_id!r} requires a human-readable name")
        if job.get("secrets") == "inherit":
            _finding(findings, config, "secrets-inherit", path, f"job {job_id!r} uses secrets: inherit")
        if "uses" in job:
            if "runs-on" in job or "steps" in job:
                _finding(findings, config, "invalid-reusable-job", path, f"job {job_id!r} mixes uses with runs-on/steps")
        else:
            if "runs-on" not in job:
                _finding(findings, config, "missing-runner", path, f"job {job_id!r} requires runs-on")
            else:
                _validate_runner(job.get("runs-on"), path, config, findings)
        timeout = job.get("timeout-minutes")
        if not isinstance(timeout, int) or timeout <= 0:
            _finding(findings, config, "missing-timeout", path, f"job {job_id!r} requires a positive timeout-minutes")
        elif timeout > config.max_timeout_minutes:
            _finding(findings, config, "excessive-timeout", path, f"job {job_id!r} timeout {timeout} exceeds {config.max_timeout_minutes}")
        strategy = job.get("strategy", {})
        if isinstance(strategy, Mapping) and "matrix" in strategy:
            matrix = strategy.get("matrix")
            if isinstance(matrix, str) and "${{" in matrix:
                _finding(findings, config, "opaque-matrix", path, f"job {job_id!r} uses an opaque dynamic matrix")
            else:
                size = _matrix_size(matrix)
                if size is None:
                    _finding(findings, config, "unbounded-matrix", path, f"job {job_id!r} matrix cannot be bounded statically")
                else:
                    contract_limit = config.max_matrix_jobs
                    record = public_contract.records_by_file.get(path)
                    if record is not None:
                        contract_limit = min(contract_limit, int(record.get("matrix_max_jobs", contract_limit)))
                    if size > contract_limit:
                        _finding(findings, config, "unbounded-matrix", path, f"job {job_id!r} matrix size {size} exceeds {contract_limit}")
        for step_index, step in _iter_steps(job):
            if not isinstance(step.get("name"), str) or not str(step.get("name")).strip():
                _finding(findings, config, "opaque-step-name", path, f"job {job_id!r} step {step_index} requires a name")
            _validate_checkout_step(step, document, config, findings)
            uses = step.get("uses")
            if isinstance(uses, str) and uses.startswith("actions/upload-artifact@"):
                retention = step.get("with", {}).get("retention-days") if isinstance(step.get("with"), Mapping) else None
                _finding(findings, config, "unregistered-artifact", path, "routine workflow artifact upload is forbidden")
                if not isinstance(retention, int) or retention > 7:
                    _finding(findings, config, "excessive-artifact-retention", path, "artifact retention must be explicit and no more than seven days")
            run = step.get("run")
            if isinstance(run, str):
                line_count = _run_line_count(run)
                if line_count > config.max_inline_run_lines:
                    _finding(
                        findings,
                        config,
                        "oversized-inline-run",
                        path,
                        f"job {job_id!r} step {step_index} has {line_count} non-empty run lines; move logic to a named function/action",
                    )
                normalized = _normalize_run(run)
                if _run_line_count(run) >= 8 and normalized:
                    run_occurrences[normalized] += 1
                    duplicated_runs.setdefault(normalized, []).append(path)
                if re.search(r"(^|\n)\s*(?:for|while)\s+.+;\s*do\b", run) and line_count > 12:
                    _finding(findings, config, "complex-yaml-logic", path, "non-trivial loops belong in named tested functions")
                if re.search(r"(^|\n)\s*(?:function\s+)?[A-Za-z_][A-Za-z0-9_]*\s*\(\)\s*\{", run):
                    _finding(findings, config, "complex-yaml-logic", path, "shell function definitions belong in named tested functions")
    for normalized, count in run_occurrences.items():
        if count > 1:
            _finding(findings, config, "duplicated-inline-run", path, f"same implementation block is repeated {count} times in one workflow")

    concurrency = data.get("concurrency")
    record = public_contract.records_by_file.get(path)
    if _is_public_workflow(path) and concurrency is not None:
        policy = str(record.get("concurrency_policy", "caller-owned")) if record else "caller-owned"
        if policy == "caller-owned":
            _finding(findings, config, "caller-cancelling-concurrency", path, "public workflow must not own caller cancellation/concurrency")
    if _is_public_workflow(path):
        _validate_public_compatibility(document, public_contract, config, findings, functions, docs_text)

    for uses, comment, line_number in _uses_entries(document):
        _validate_action_reference(uses, comment, path, line_number, config, lock, findings)


def _validate_action(
    document: ParsedDocument,
    config: HarnessConfig,
    lock: ActionLock,
    findings: list[Finding],
    duplicated_runs: dict[str, list[str]],
) -> None:
    path = document.relative_path
    data = document.data
    if not isinstance(data.get("name"), str) or not str(data.get("name")).strip():
        _finding(findings, config, "opaque-action-name", path, "composite action requires a name")
    runs = data.get("runs", {})
    if not isinstance(runs, Mapping) or runs.get("using") != "composite":
        _finding(findings, config, "invalid-internal-action", path, "approved internal actions must be composite")
        return
    steps = runs.get("steps", [])
    if not isinstance(steps, list):
        _finding(findings, config, "invalid-internal-action", path, "composite action steps must be a list")
        return
    local_runs: collections.Counter[str] = collections.Counter()
    for index, step in enumerate(steps, start=1):
        if not isinstance(step, Mapping):
            continue
        if not isinstance(step.get("name"), str) or not str(step.get("name")).strip():
            _finding(findings, config, "opaque-step-name", path, f"composite step {index} requires a name")
        run = step.get("run")
        if isinstance(run, str):
            line_count = _run_line_count(run)
            if line_count > config.max_inline_run_lines:
                _finding(findings, config, "oversized-inline-run", path, f"composite step {index} has {line_count} run lines")
            normalized = _normalize_run(run)
            if line_count >= 8 and normalized:
                local_runs[normalized] += 1
                duplicated_runs.setdefault(normalized, []).append(path)
    for normalized, count in local_runs.items():
        if count > 1:
            _finding(findings, config, "duplicated-inline-run", path, f"same implementation block is repeated {count} times in one composite action")
    for uses, comment, line_number in _uses_entries(document):
        _validate_action_reference(uses, comment, path, line_number, config, lock, findings)


def _validate_global_duplicate_blocks(
    duplicated_runs: Mapping[str, list[str]],
    config: HarnessConfig,
    findings: list[Finding],
) -> None:
    for paths in duplicated_runs.values():
        unique_paths = sorted(set(paths))
        if len(unique_paths) > 1:
            for path in unique_paths:
                _finding(findings, config, "duplicated-implementation", path, f"implementation block is duplicated across {unique_paths}")
