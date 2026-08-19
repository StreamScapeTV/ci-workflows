"""Offline GitHub Actions expression-context availability validation.

The tables mirror GitHub's documented context-availability rows for the
workflow, job, and step fields already understood by the canonical Central
validator. They validate context availability only; they do not add product or
security policy.
"""
from __future__ import annotations

import dataclasses
import re
from collections.abc import Iterator, Mapping
from typing import Any

from .validation_helpers import _finding, _iter_jobs, _iter_steps
from .validation_model import Finding, HarnessConfig, ParsedDocument

_CONTEXT_NAMES = frozenset(
    "env github inputs job jobs matrix needs runner secrets steps strategy vars".split()
)
_CONTEXT_REFERENCE_RE = re.compile(
    r"(?<![A-Za-z0-9_.])(?P<context>"
    + "|".join(sorted(_CONTEXT_NAMES, key=len, reverse=True))
    + r")\b"
)


def _contexts(value: str) -> frozenset[str]:
    return frozenset(value.split())


_PRE_RUN = _contexts("github needs strategy matrix vars inputs")
_RUNTIME = _contexts(
    "github needs strategy matrix job runner env vars secrets steps inputs"
)
_RUNTIME_IF = _RUNTIME - {"secrets"}


@dataclasses.dataclass(frozen=True)
class _ContextRule:
    github_key: str
    allowed: frozenset[str]
    implicit_expression: bool = False


def _rule(
    github_key: str,
    allowed: frozenset[str],
    *,
    implicit_expression: bool = False,
) -> _ContextRule:
    return _ContextRule(
        github_key=github_key,
        allowed=allowed,
        implicit_expression=implicit_expression,
    )


_WORKFLOW_RULES = {
    "run-name": _rule("run-name", _contexts("github inputs vars")),
    "concurrency": _rule("concurrency", _contexts("github inputs vars")),
    "env": _rule("env", _contexts("github secrets inputs vars")),
}
_JOB_RULES = {
    "concurrency": _rule("jobs.<job_id>.concurrency", _PRE_RUN),
    "continue-on-error": _rule("jobs.<job_id>.continue-on-error", _PRE_RUN),
    "env": _rule(
        "jobs.<job_id>.env",
        _contexts("github needs strategy matrix vars secrets inputs"),
    ),
    "if": _rule(
        "jobs.<job_id>.if",
        _contexts("github needs vars inputs"),
        implicit_expression=True,
    ),
    "name": _rule("jobs.<job_id>.name", _PRE_RUN),
    "outputs": _rule(
        "jobs.<job_id>.outputs.<output_id>",
        _RUNTIME,
    ),
    "runs-on": _rule("jobs.<job_id>.runs-on", _PRE_RUN),
    "secrets": _rule(
        "jobs.<job_id>.secrets.<secrets_id>",
        _contexts("github needs strategy matrix secrets inputs vars"),
    ),
    "strategy": _rule(
        "jobs.<job_id>.strategy",
        _contexts("github needs vars inputs"),
    ),
    "timeout-minutes": _rule("jobs.<job_id>.timeout-minutes", _PRE_RUN),
    "with": _rule("jobs.<job_id>.with.<with_id>", _PRE_RUN),
}
_JOB_DEFAULTS_RUN_RULE = _rule(
    "jobs.<job_id>.defaults.run",
    _contexts("github needs strategy matrix env vars inputs"),
)
_JOB_ENVIRONMENT_RULE = _rule("jobs.<job_id>.environment", _PRE_RUN)
_JOB_ENVIRONMENT_URL_RULE = _rule(
    "jobs.<job_id>.environment.url",
    _contexts("github needs strategy matrix job runner env vars steps inputs"),
)
_STEP_RULES = {
    "continue-on-error": _rule(
        "jobs.<job_id>.steps.continue-on-error",
        _RUNTIME,
    ),
    "env": _rule("jobs.<job_id>.steps.env", _RUNTIME),
    "if": _rule(
        "jobs.<job_id>.steps.if",
        _RUNTIME_IF,
        implicit_expression=True,
    ),
    "name": _rule("jobs.<job_id>.steps.name", _RUNTIME),
    "run": _rule("jobs.<job_id>.steps.run", _RUNTIME),
    "timeout-minutes": _rule(
        "jobs.<job_id>.steps.timeout-minutes",
        _RUNTIME,
    ),
    "with": _rule("jobs.<job_id>.steps.with", _RUNTIME),
    "working-directory": _rule(
        "jobs.<job_id>.steps.working-directory",
        _RUNTIME,
    ),
}


def _expression_bodies(value: str) -> Iterator[str]:
    """Yield ``${{ ... }}`` bodies without closing inside quoted literals."""

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
    """Blank GitHub expression string literals before detecting context roots."""

    visible: list[str] = []
    index = 0
    in_string = False
    while index < len(expression):
        character = expression[index]
        if character == "'":
            visible.append(" ")
            if in_string and index + 1 < len(expression) and expression[index + 1] == "'":
                visible.append(" ")
                index += 2
                continue
            in_string = not in_string
            index += 1
            continue
        visible.append(" " if in_string else character)
        index += 1
    return "".join(visible)


def _contexts_in_expression(expression: str) -> set[str]:
    visible = _without_single_quoted_literals(expression)
    return {
        match.group("context")
        for match in _CONTEXT_REFERENCE_RE.finditer(visible)
    }


def _contexts_in_value(value: str, *, implicit_expression: bool) -> frozenset[str]:
    bodies = tuple(_expression_bodies(value))
    if bodies:
        return frozenset(
            context
            for body in bodies
            for context in _contexts_in_expression(body)
        )
    if implicit_expression:
        return frozenset(_contexts_in_expression(value))
    return frozenset()


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
        used = _contexts_in_value(
            leaf,
            implicit_expression=rule.implicit_expression,
        )
        for context in sorted(used - rule.allowed):
            _finding(
                findings,
                config,
                "invalid-expression-context",
                document.relative_path,
                f"{leaf_location} uses unavailable GitHub Actions context "
                f"{context!r}; {rule.github_key} allows only: "
                f"{', '.join(sorted(rule.allowed))}",
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
    """Reject context roots unavailable at their documented YAML location."""

    data = document.data
    for field, rule in _WORKFLOW_RULES.items():
        if field in data:
            _validate_value(
                document,
                config,
                findings,
                rule,
                data[field],
                f"workflow field {field!r}" if field != "env" else "workflow env",
            )

    for job_id, job in _iter_jobs(document):
        for field, rule in _JOB_RULES.items():
            if field in job:
                location = (
                    f"job {job_id!r} {field}"
                    if field in {"env", "outputs", "strategy", "with", "secrets"}
                    else f"job {job_id!r} field {field!r}"
                )
                _validate_value(
                    document,
                    config,
                    findings,
                    rule,
                    job[field],
                    location,
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
                if field in step:
                    _validate_value(
                        document,
                        config,
                        findings,
                        rule,
                        step[field],
                        f"job {job_id!r} step {step_index} field {field!r}",
                    )


__all__ = ("_validate_workflow_expression_contexts",)
