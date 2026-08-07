"""Checked-in readability policy, deterministic docs, and static source-shape checks."""
from __future__ import annotations

import ast
import json
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

from .ciw_docs import load_command_contract, render_ciw_docs
from .validation_graph import _action_edges, _longest_depth
from .validation_helpers import _is_public_workflow, _iter_jobs
from .validation_model import ParsedDocument

CONTRACT_PATH = Path("contracts/readability-policy.json")
OUTPUT_PATH = Path("docs/architecture/readability-and-functions.md")
_SAFE_ID = re.compile(r"^[a-z][a-z0-9-]{1,63}$")
_READABILITY_RULES = {
    "callback-like-input",
    "caller-cancelling-concurrency",
    "complex-yaml-logic",
    "composite-action-depth",
    "duplicated-implementation",
    "duplicated-inline-run",
    "opaque-function-name",
    "opaque-generic-job-id",
    "oversized-inline-run",
    "public-api-doc-drift",
    "public-workflow-job-count",
    "reusable-workflow-cycle",
    "reusable-workflow-depth",
    "unbounded-matrix",
}


class ReadabilityError(RuntimeError):
    """One stable checked-in readability contract failure."""

    def __init__(self, code: str, path: str, message: str) -> None:
        self.code = code
        self.path = path
        self.message = message
        super().__init__(code)


@dataclass(frozen=True)
class ReadabilityException:
    identifier: str
    issue: int
    path: str
    rules: frozenset[str]
    reason: str
    removal_condition: str
    tests: tuple[str, ...]


@dataclass(frozen=True)
class ReadabilityPolicy:
    public_reusable_workflow_depth: int
    internal_leaf_reusable_children: int
    composite_action_depth: int
    public_workflow_jobs: int
    inline_run_lines: int
    duplicate_block_min_lines: int
    complex_loop_min_lines: int
    matrix_jobs: int
    shell_functions_forbidden: bool
    opaque_identifiers: frozenset[str]
    forbidden_generic_inputs: frozenset[str]
    exceptions: Mapping[str, ReadabilityException]

    def excepts(self, path: str, rule: str) -> bool:
        exception = self.exceptions.get(path)
        return exception is not None and rule in exception.rules


@dataclass(frozen=True, order=True)
class ReadabilityFinding:
    rule: str
    path: str
    message: str
    line: int = 0


def _require(condition: bool, code: str, path: str, message: str) -> None:
    if not condition:
        raise ReadabilityError(code, path, message)


def _read_json(root: Path, relative: Path) -> Mapping[str, Any]:
    path = root / relative
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ReadabilityError(
            "readability-contract-unavailable",
            relative.as_posix(),
            "cannot read deterministic JSON contract",
        ) from error
    _require(
        isinstance(value, Mapping),
        "readability-contract-invalid",
        relative.as_posix(),
        "contract root must be an object",
    )
    return value


def _safe_relative(value: Any, *, code: str) -> str:
    _require(isinstance(value, str) and bool(value), code, CONTRACT_PATH.as_posix(), "path is required")
    path = PurePosixPath(value)
    _require(
        not path.is_absolute()
        and ".." not in path.parts
        and "\\" not in value
        and all(part not in {"", "."} for part in path.parts),
        code,
        CONTRACT_PATH.as_posix(),
        "path must remain repository relative",
    )
    return path.as_posix()


def _string_list(value: Any, *, code: str, allow_empty: bool = False) -> list[str]:
    _require(
        isinstance(value, list)
        and all(isinstance(item, str) and bool(item) for item in value)
        and (allow_empty or bool(value)),
        code,
        CONTRACT_PATH.as_posix(),
        "expected a bounded string list",
    )
    return list(value)


def load_readability_policy(root: Path) -> ReadabilityPolicy:
    payload = _read_json(root, CONTRACT_PATH)
    _require(
        payload.get("schema_version") == 1,
        "readability-schema-unsupported",
        CONTRACT_PATH.as_posix(),
        "schema_version must be 1",
    )
    _require(
        payload.get("policy_version") == "1.0.0",
        "readability-version-invalid",
        CONTRACT_PATH.as_posix(),
        "policy_version must be 1.0.0",
    )
    limits = payload.get("limits")
    expected_limits = {
        "public_reusable_workflow_depth": 1,
        "internal_leaf_reusable_children": 0,
        "composite_action_depth": 1,
        "public_workflow_jobs": 7,
        "inline_run_lines": 40,
        "duplicate_block_min_lines": 8,
        "complex_loop_min_lines": 12,
        "matrix_jobs": 16,
    }
    _require(
        isinstance(limits, Mapping) and dict(limits) == expected_limits,
        "readability-limits-invalid",
        CONTRACT_PATH.as_posix(),
        "readability limits must match the reviewed foundation contract",
    )
    _require(
        payload.get("shell_functions_forbidden") is True,
        "readability-shell-policy-invalid",
        CONTRACT_PATH.as_posix(),
        "shell function definitions must remain forbidden",
    )
    opaque = frozenset(
        _string_list(payload.get("opaque_identifiers"), code="readability-opaque-identifiers-invalid")
    )
    _require(
        {"job", "task", "step", "run", "work", "misc"} <= opaque,
        "readability-opaque-identifiers-invalid",
        CONTRACT_PATH.as_posix(),
        "generic intent-free identifiers are incomplete",
    )
    forbidden = frozenset(
        _string_list(payload.get("forbidden_generic_inputs"), code="readability-generic-inputs-invalid")
    )
    _require(
        {
            "arbitrary_command",
            "shell",
            "callback",
            "handler",
            "function_name",
            "module_name",
            "runner",
            "runs_on",
            "runner_labels",
            "container_engine",
            "secret_name",
            "deletion_path",
        }
        <= forbidden,
        "readability-generic-inputs-invalid",
        CONTRACT_PATH.as_posix(),
        "callback-like generic inputs are incomplete",
    )
    required_fields = frozenset(
        _string_list(payload.get("exception_required_fields"), code="readability-exception-fields-invalid")
    )
    expected_fields = {
        "id",
        "issue",
        "path",
        "rules",
        "reason",
        "removal_condition",
        "tests",
    }
    _require(
        required_fields == expected_fields,
        "readability-exception-fields-invalid",
        CONTRACT_PATH.as_posix(),
        "exception fields do not match the reviewed format",
    )
    raw_exceptions = payload.get("exceptions")
    _require(
        isinstance(raw_exceptions, list),
        "readability-exceptions-invalid",
        CONTRACT_PATH.as_posix(),
        "exceptions must be a list",
    )
    exceptions: dict[str, ReadabilityException] = {}
    identifiers: set[str] = set()
    for raw in raw_exceptions:
        _require(
            isinstance(raw, Mapping) and set(raw) == expected_fields,
            "readability-exception-invalid",
            CONTRACT_PATH.as_posix(),
            "exception fields are incomplete or unexpected",
        )
        identifier = raw.get("id")
        issue = raw.get("issue")
        path = _safe_relative(raw.get("path"), code="readability-exception-path-invalid")
        rules = frozenset(
            _string_list(raw.get("rules"), code="readability-exception-rules-invalid")
        )
        reason = raw.get("reason")
        removal = raw.get("removal_condition")
        tests = tuple(
            _safe_relative(item, code="readability-exception-test-invalid")
            for item in _string_list(raw.get("tests"), code="readability-exception-tests-invalid")
        )
        _require(
            isinstance(identifier, str)
            and _SAFE_ID.fullmatch(identifier) is not None
            and identifier not in identifiers,
            "readability-exception-id-invalid",
            CONTRACT_PATH.as_posix(),
            "exception id must be unique and bounded",
        )
        _require(
            isinstance(issue, int) and not isinstance(issue, bool) and issue > 0,
            "readability-exception-issue-invalid",
            CONTRACT_PATH.as_posix(),
            "exception issue must be a positive integer",
        )
        _require(
            rules <= _READABILITY_RULES,
            "readability-exception-rules-invalid",
            CONTRACT_PATH.as_posix(),
            "exception contains an unknown readability rule",
        )
        _require(
            isinstance(reason, str)
            and bool(reason.strip())
            and isinstance(removal, str)
            and bool(removal.strip()),
            "readability-exception-rationale-invalid",
            CONTRACT_PATH.as_posix(),
            "exception reason and removal condition are required",
        )
        _require(
            path not in exceptions and (root / path).is_file(),
            "readability-exception-path-invalid",
            path,
            "exception path must be a checked-in file",
        )
        for test in tests:
            _require(
                test.startswith("tests/test_")
                and test.endswith(".py")
                and (root / test).is_file(),
                "readability-exception-test-invalid",
                test,
                "exception regression test must be checked in",
            )
        exception = ReadabilityException(
            identifier=identifier,
            issue=issue,
            path=path,
            rules=rules,
            reason=reason.strip(),
            removal_condition=removal.strip(),
            tests=tests,
        )
        exceptions[path] = exception
        identifiers.add(identifier)

    return ReadabilityPolicy(
        public_reusable_workflow_depth=1,
        internal_leaf_reusable_children=0,
        composite_action_depth=1,
        public_workflow_jobs=7,
        inline_run_lines=40,
        duplicate_block_min_lines=8,
        complex_loop_min_lines=12,
        matrix_jobs=16,
        shell_functions_forbidden=True,
        opaque_identifiers=opaque,
        forbidden_generic_inputs=forbidden,
        exceptions=exceptions,
    )


def _input_names(document: ParsedDocument) -> set[str]:
    if document.relative_path.startswith("actions/"):
        raw = document.data.get("inputs", {})
    else:
        on = document.data.get("on", {})
        workflow_call = on.get("workflow_call", {}) if isinstance(on, Mapping) else {}
        raw = workflow_call.get("inputs", {}) if isinstance(workflow_call, Mapping) else {}
    if not isinstance(raw, Mapping):
        return set()
    return {str(name).replace("-", "_") for name in raw}


def _add(
    findings: list[ReadabilityFinding],
    policy: ReadabilityPolicy,
    rule: str,
    path: str,
    message: str,
    *,
    line: int = 0,
) -> None:
    if not policy.excepts(path, rule):
        findings.append(ReadabilityFinding(rule, path, message, line))


def validate_repository_readability(
    root: Path,
    workflow_documents: Mapping[str, ParsedDocument],
    action_documents: Mapping[str, ParsedDocument],
) -> tuple[ReadabilityFinding, ...]:
    """Run the #31 contract and source-shape checks not duplicated elsewhere."""

    try:
        policy = load_readability_policy(root)
        harness = _read_json(root, Path("contracts/validation-harness.json"))
        public_types = _read_json(root, Path("contracts/public-workflow-types.json"))
        defaults = public_types.get("defaults", {})
        _require(
            isinstance(defaults, Mapping)
            and defaults.get("max_reusable_workflow_depth")
            == policy.public_reusable_workflow_depth,
            "readability-public-depth-drift",
            "contracts/public-workflow-types.json",
            "public workflow depth disagrees with readability policy",
        )
        _require(
            harness.get("max_inline_run_lines") == policy.inline_run_lines
            and harness.get("max_matrix_jobs") == policy.matrix_jobs,
            "readability-harness-limit-drift",
            "contracts/validation-harness.json",
            "canonical harness limits disagree with readability policy",
        )
        load_command_contract(root)
        expected_ciw_docs = render_ciw_docs(contract_root=root)
        actual_ciw_docs = (root / "docs/reference/ciw.md").read_text(encoding="utf-8")
        _require(
            actual_ciw_docs == expected_ciw_docs,
            "ciw-doc-drift",
            "docs/reference/ciw.md",
            "generated ciw documentation is stale",
        )
        expected_readability_docs = render_readability_docs(contract_root=root)
        _require(
            (root / OUTPUT_PATH).read_text(encoding="utf-8")
            == expected_readability_docs,
            "readability-doc-drift",
            OUTPUT_PATH.as_posix(),
            "generated readability documentation is stale",
        )
    except ReadabilityError as error:
        return (
            ReadabilityFinding(error.code, error.path, error.message),
        )
    except OSError:
        return (
            ReadabilityFinding(
                "readability-doc-unavailable",
                OUTPUT_PATH.as_posix(),
                "generated readability documentation is unavailable",
            ),
        )
    except RuntimeError as error:
        code = getattr(error, "code", "ciw-contract-invalid")
        return (
            ReadabilityFinding(
                str(code),
                "contracts/ciw-commands.json",
                "ciw command contract or documentation is invalid",
            ),
        )

    findings: list[ReadabilityFinding] = []
    for path, document in workflow_documents.items():
        jobs = list(_iter_jobs(document))
        if _is_public_workflow(path) and len(jobs) > policy.public_workflow_jobs:
            _add(
                findings,
                policy,
                "public-workflow-job-count",
                path,
                (
                    f"public workflow has {len(jobs)} jobs; reviewed guidance "
                    f"allows {policy.public_workflow_jobs}"
                ),
            )
        for job_id, _job in jobs:
            if job_id in policy.opaque_identifiers:
                _add(
                    findings,
                    policy,
                    "opaque-generic-job-id",
                    path,
                    f"job id {job_id!r} does not describe intent",
                )
        unsafe = sorted(_input_names(document) & policy.forbidden_generic_inputs)
        if unsafe:
            _add(
                findings,
                policy,
                "callback-like-input",
                path,
                f"workflow exposes callback-like generic inputs {unsafe}",
            )

    for path, document in action_documents.items():
        unsafe = sorted(_input_names(document) & policy.forbidden_generic_inputs)
        if unsafe:
            _add(
                findings,
                policy,
                "callback-like-input",
                path,
                f"composite action exposes callback-like generic inputs {unsafe}",
            )

    action_graph = {
        str(PurePosixPath(path).parent): _action_edges(document)
        for path, document in action_documents.items()
    }
    for node in sorted(action_graph):
        depth = _longest_depth(action_graph, node)
        if depth > policy.composite_action_depth:
            action_file = f"{node}/action.yml"
            if action_file not in action_documents:
                action_file = f"{node}/action.yaml"
            _add(
                findings,
                policy,
                "composite-action-depth",
                action_file,
                (
                    f"composite action depth {depth} exceeds reviewed maximum "
                    f"{policy.composite_action_depth}"
                ),
            )

    for source in sorted((root / "src/ci_workflows").glob("**/*.py")):
        try:
            tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
        except (OSError, SyntaxError):
            continue
        relative = source.relative_to(root).as_posix()
        for node in tree.body:
            if (
                isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and not node.name.startswith("_")
                and node.name in policy.opaque_identifiers
            ):
                _add(
                    findings,
                    policy,
                    "opaque-function-name",
                    relative,
                    f"public function {node.name!r} does not describe intent",
                    line=node.lineno,
                )

    return tuple(sorted(set(findings)))


def render_readability_docs(*, contract_root: Path) -> str:
    policy = load_readability_policy(contract_root)
    limits = [
        ("Public reusable-workflow depth", policy.public_reusable_workflow_depth),
        ("Internal leaf reusable children", policy.internal_leaf_reusable_children),
        ("Local composite-action depth", policy.composite_action_depth),
        ("Public workflow jobs", policy.public_workflow_jobs),
        ("Inline non-empty `run:` lines", policy.inline_run_lines),
        ("Duplicate-block threshold lines", policy.duplicate_block_min_lines),
        ("Complex-loop threshold lines", policy.complex_loop_min_lines),
        ("Matrix jobs", policy.matrix_jobs),
    ]
    lines = [
        "# Readability and named functions",
        "",
        "Generated from `contracts/readability-policy.json`. Do not edit directly.",
        "",
        "## Reviewed limits",
        "",
        "| Constraint | Maximum |",
        "|---|---:|",
    ]
    for label, value in limits:
        lines.append(f"| {label} | {value} |")
    lines.extend(
        [
            "",
            "Shell function definitions in workflow YAML are forbidden. "
            "Non-trivial behavior belongs in typed, tested Python functions.",
            "",
            "## Stable execution shape",
            "",
            "`consumer trigger → public reusable workflow → named composite action or ciw function`",
            "",
            "Public reusable-workflow depth remains one. Internal leaf workflows "
            "may not call another reusable workflow. Local composite actions do "
            "not nest another local composite action.",
            "",
            "## Before and after",
            "",
            "Avoid opaque embedded programs:",
            "",
            "```yaml",
            "- name: Run",
            "  run: |",
            "    # dozens of lines of parsing, branching, cleanup and output logic",
            "```",
            "",
            "Prefer an intent-named adapter:",
            "",
            "```yaml",
            "- name: Verify deterministic repository policy",
            "  uses: StreamScapeTV/ci-workflows/actions/verify-repository-policy@<immutable-ref>",
            "```",
            "",
            "## Rejected generic control surfaces",
            "",
            ", ".join(f"`{name}`" for name in sorted(policy.forbidden_generic_inputs)) + ".",
            "",
            "## Reviewed exceptions",
            "",
        ]
    )
    if not policy.exceptions:
        lines.append("None.")
    else:
        for exception in sorted(policy.exceptions.values(), key=lambda item: item.identifier):
            lines.extend(
                [
                    f"### `{exception.identifier}`",
                    "",
                    f"- Issue: #{exception.issue}",
                    f"- Path: `{exception.path}`",
                    "- Rules: " + ", ".join(f"`{rule}`" for rule in sorted(exception.rules)),
                    f"- Reason: {exception.reason}",
                    f"- Removal condition: {exception.removal_condition}",
                    "- Regression tests: " + ", ".join(f"`{test}`" for test in exception.tests),
                    "",
                ]
            )
    return "\n".join(lines)


def write_readability_docs(*, contract_root: Path, check: bool = False) -> Path:
    output = contract_root / OUTPUT_PATH
    rendered = render_readability_docs(contract_root=contract_root)
    if check:
        try:
            current = output.read_text(encoding="utf-8")
        except OSError as error:
            raise ReadabilityError(
                "readability-doc-unavailable",
                OUTPUT_PATH.as_posix(),
                "generated readability documentation is unavailable",
            ) from error
        _require(
            current == rendered,
            "readability-doc-drift",
            OUTPUT_PATH.as_posix(),
            "generated readability documentation is stale",
        )
        return output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(rendered, encoding="utf-8", newline="\n")
    return output
