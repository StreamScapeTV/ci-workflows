"""Offline GitHub Actions expression-context availability validation.

The tables in this module mirror the documented GitHub Actions context
availability rows for the workflow, job, and step locations that the canonical
Central validator already understands.  They intentionally validate context
*availability*, not expression semantics or product policy.
"""
from __future__ import annotations

import dataclasses
import re
from collections.abc import Iterator, Mapping
from typing import Any

from .validation_helpers import _finding, _iter_jobs, _iter_steps
from .validation_model import Finding, HarnessConfig, ParsedDocument

_CONTEXT_NAMES = frozenset(
    {
        "env",
        "github",
        "inputs",
        "job",
        "jobs",
        "matrix",
        "needs",
        "runner",
        "secrets",
        "steps",
        "strategy",
        "vars",
    }
)
_CONTEXT_REFERENCE_RE = re.compile(
    r"(?<![A-Za-z0-9_.])(?P<context>"
    + "|".join(sorted(_CONTEXT_NAMES, key=len, reverse=True))
    + r")\b"
)


@dataclasses.dataclass(frozen=True)
class _ContextRule:
    github_key: str
    allowed: frozenset[str]


def _rule(github_key: str, *allowed: str) -> _ContextRule:
    return _ContextRule(github_key=github_key, allowed=frozenset(allowed))


_WORKFLOW_SCALAR_RULES = {
    "run-name": _rule("run-name", "github", "inputs", "vars"),
    "concurrency": _rule("concurrency", "github", "inputs", "vars"),
}
_WORKFLOW_ENV_RULE = _rule(
    "env",
    "github",
    "secrets",
    "inputs",
    "vars",
)

_JOB_SCALAR_RULES = {
    "concurrency": _rule(
        "jobs.<job_id>.concurrency",
        "github",
        "needs",
        "strategy",
        "matrix",
        "inputs",
        "vars",
    ),
    "continue-on-error": _rule(
        "jobs.<job_id>.continue-on-error",
        "github",
        "needs",
        "strategy",
        "matrix",
        "inputs",
        "vars",
    ),
    "if": _rule(
        "jobs.<job_id>.if",
        "github",
        "needs",
        "vars",
        "inputs",
    ),
    "name": _rule(
        "jobs.<job_id>.name",
        "github",
        "needs",
        "strategy",
        "matrix",
        "vars",
        "inputs",
    ),
    "runs-on": _rule(
        "jobs.<job_id>.runs-on",
        "github",
        "needs",
        "strategy",
        "matrix",
        "vars",
        "inputs",
    ),
    "timeout-minutes": _rule(
        "jobs.<job_id>.timeout-minutes",
        "github",
        "needs",
        "strategy",
        "matrix",
        "vars",
        "inputs",
    ),
}
_JOB_ENV_RULE = _rule(
    "jobs.<job_id>.env",
    "github",
    "needs",
    "strategy",
    "matrix",
    "vars",
    "secrets",
    "inputs",
)
_JOB_OUTPUT_RULE = _rule(
    "jobs.<job_id>.outputs.<output_id>",
    "github",
    "needs",
    "strategy",
    "matrix",
    "job",
    "runner",
    "env",
    "vars",
    "secrets",
    "steps",
    "inputs",
)
_JOB_STRATEGY_RULE = _rule(
    "jobs.<job_id>.strategy",
    "github",
    "needs",
    "vars",
    "inputs",
)
_JOB_WITH_RULE = _rule(
    "jobs.<job_id>.with.<with_id>",
    "github",
    "needs",
    "strategy",
    "matrix",
    "inputs",
    "vars",
)
_JOB_SECRET_RULE = _rule(
    "jobs.<job_id>.secrets.<secrets_id>",
    "github",
    "needs",
    "strategy",
    "matrix",
    "secrets",
    "inputs",
    "vars",
)
_JOB_DEFAULTS_RUN_RULE = _rule(
    "jobs.<job_id>.defaults.run",
    "github",
    "needs",
    "strategy",
    "matrix",
    "env",
    "vars",
    "inputs",
)
_JOB_ENVIRONMENT_RULE = _rule(
    "jobs.<job_id>.environment",
    "github",
    "needs",
    "strategy",
    "matrix",
    "vars",
    "inputs",
)
_JOB_ENVIRONMENT_URL_RULE = _rule(
    "jobs.<job_id>.environment.url",
    "github",
    "needs",
    "strategy",
    "matrix",
    "job",
    "runner",
    "env",
    "vars",
    "steps",
    "inputs",
)

_STEP_FULL_RULE = _rule(
    "jobs.<job_id>.steps",
    "github",
    "needs",
    "strategy",
    "matrix",
    "job",
    "runner",
    "env",
    "vars",
    "secrets",
    "steps",
    "inputs",
)
_STEP_IF_RULE = _rule(
    "jobs.<job_id>.steps.if",
    "github",
    "needs",
    "strategy",
    "matrix",
    "job",
    "runner",
    "env",
    "vars",
    "steps",
    "inputs",
)
_STEP_RULES = {
    "continue-on-error": dataclasses.replace(
        _STEP_FULL_RULE,
        github_key="jobs.<job_id>.steps.continue-on-error",
    ),
    "env": dataclasses.replace(
        _STEP_FULL_RULE,
        github_key="jobs.<job_id>.steps.env",
    ),
    "if": _STEP_IF_RULE,
    "name": dataclasses.replace(
        _STEP_FULL_RULE,
        github_key="jobs.<job_id>.steps.name",
    ),
    "run": dataclasses.replace(
        _STEP_FULL_RULE,
        github_key="jobs.<job_id>.steps.run",
    ),
    "timeout-minutes": dataclasses.replace(
        _STEP_FULL_RULE,
        github_key="jobs.<job_id>.steps.timeout-minutes",
    ),
    "with": dataclasses.replace(
        _STEP_FULL_RULE,
        github_key="jobs.<job_id>.steps.with",
    ),
    "working-directory": dataclasses.replace(
        _STEP_FULL_RULE,
        github_key="jobs.<job_id>.steps.working-directory",
    ),
}


def _expression_bodies(value: str) -> Iterator[str]:
    """Yield expression bodies while respecting GitHub single-quoted strings."""

    offset = 0
    while True:
        start = value.find("${{", offset)
        if start < 0:
            return
        index = start + 3
        body_start = index
        in_string = False
        while index < len(value):
            character = value[index]
            if character == "'":
                if in_string and index + 1 < len(value) and value[index + 1] == "'":
                    index += 2
                    continue
                in_string = not in_string
                index += 1
                continue
            if not in_string and value.startswith("}}", index):
                yield value[body_start:index]
                offset = index + 2
                break
            index += 1
        else:
            return


def _without_single_quoted_literals(expression: str) -> str:
    """Blank expression string literals so their text is not a context use."""

    result: list[str] = []
    index = 0
    in_string = False
    while index < len(expression):
        character = expression[index]
        if character == "'":
            result.append(" ")
            if in_string and index + 1 < len(expression) and expression[index + 1] == "'":
                result.append(" ")
                index += 2
                continue
            in_string = not in_string
            index += 1
            continue
        result.append(" " if in_string else character)
        index += 1
    return "".join(result)


def _contexts_in_string(value: str) -> frozenset[str]:
    contexts: set[str] = set()
    for body in _expression_bodies(value):
        visible = _without_single_quoted_literals(body)
        contexts.update(
            match.group("context") for match in _CONTEXT_REFERENCE_RE.finditer(visible)
        )
    return frozenset(contexts)


def _scalar_leaves(value: Any, location: str) -> Iterator[tuple[str, Any]]:
    if isinstance(value, Mapping):
        for key, child in value.items():
            yield from _scalar_leaves(child, f"{location}.{key}")
        return
    if isinstance(value, list):
        for index, child in enumerate(value):
            yield from _scalar_leaves(child, f"{location}[{index}]")
        return
    yield location, value


def _validate_value(
    document: ParsedDocument,
    config: HarnessConfig,
    findings: list[Finding],
    rule: _ContextRule,
    value: Any,
    location: str,
) -> None:
    for leaf_location, leaf in _scalar_leaves(value, location):
        if not isinstance(leaf, str):
            continue
        unavailable = sorted(_contexts_in_string(leaf) - rule.allowed)
        for context in unavailable:
            allowed = ", ".join(sorted(rule.allowed))
            _finding(
                findings,
                config,
                "invalid-expression-context",
                document.relative_path,
                f"{leaf_location} uses unavailable GitHub Actions context "
                f"{context!r}; {rule.github_key} allows only: {allowed}",
            )


def _validate_environment(
    document: ParsedDocument,
    config: HarnessConfig,
    findings: list[Finding],
    job_id: str,
    value: Any,
) -> None:
    location = f"job {job_id!r} environment"
    if not isinstance(value, Mapping):
        _validate_value(
            document,
            config,
            findings,
            _JOB_ENVIRONMENT_RULE,
            value,
            location,
        )
        return
    for key, child in value.items():
        rule = (
            _JOB_ENVIRONMENT_URL_RULE
            if str(key) == "url"
            else _JOB_ENVIRONMENT_RULE
        )
        _validate_value(
            document,
            config,
            findings,
            rule,
            child,
            f"{location}.{key}",
        )


def _validate_workflow_expression_contexts(
    document: ParsedDocument,
    config: HarnessConfig,
    findings: list[Finding],
) -> None:
    """Reject contexts unavailable at the documented workflow YAML location."""

    data = document.data
    for field, rule in _WORKFLOW_SCALAR_RULES.items():
        if field in data:
            _validate_value(
                document,
                config,
                findings,
                rule,
                data[field],
                f"workflow field {field!r}",
            )
    if "env" in data:
        _validate_value(
            document,
            config,
            findings,
            _WORKFLOW_ENV_RULE,
            data["env"],
            "workflow env",
        )

    for job_id, job in _iter_jobs(document):
        for field, rule in _JOB_SCALAR_RULES.items():
            if field in job:
                _validate_value(
                    document,
                    config,
                    findings,
                    rule,
                    job[field],
                    f"job {job_id!r} field {field!r}",
                )
        if "env" in job:
            _validate_value(
                document,
                config,
                findings,
                _JOB_ENV_RULE,
                job["env"],
                f"job {job_id!r} env",
            )
        if "outputs" in job:
            _validate_value(
                document,
                config,
                findings,
                _JOB_OUTPUT_RULE,
                job["outputs"],
                f"job {job_id!r} outputs",
            )
        if "strategy" in job:
            _validate_value(
                document,
                config,
                findings,
                _JOB_STRATEGY_RULE,
                job["strategy"],
                f"job {job_id!r} strategy",
            )
        if isinstance(job.get("with"), Mapping):
            _validate_value(
                document,
                config,
                findings,
                _JOB_WITH_RULE,
                job["with"],
                f"job {job_id!r} with",
            )
        if isinstance(job.get("secrets"), Mapping):
            _validate_value(
                document,
                config,
                findings,
                _JOB_SECRET_RULE,
                job["secrets"],
                f"job {job_id!r} secrets",
            )
        defaults = job.get("defaults")
        if isinstance(defaults, Mapping) and "run" in defaults:
            _validate_value(
                document,
                config,
                findings,
                _JOB_DEFAULTS_RUN_RULE,
                defaults["run"],
                f"job {job_id!r} defaults.run",
            )
        if "environment" in job:
            _validate_environment(
                document,
                config,
                findings,
                job_id,
                job["environment"],
            )

        for step_index, step in _iter_steps(job):
            for field, rule in _STEP_RULES.items():
                if field not in step:
                    continue
                _validate_value(
                    document,
                    config,
                    findings,
                    rule,
                    step[field],
                    f"job {job_id!r} step {step_index} field {field!r}",
                )


__all__ = ("_validate_workflow_expression_contexts",)
