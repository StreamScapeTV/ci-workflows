"""Reusable workflow and composite-action call graph validation."""
from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import Mapping

from .validation_helpers import _finding, _is_internal_workflow, _iter_jobs
from .validation_model import (
    _LOCAL_WORKFLOW_RE,
    Finding,
    HarnessConfig,
    ParsedDocument,
    PublicContract,
)


def _workflow_edges(document: ParsedDocument) -> set[str]:
    edges: set[str] = set()
    for _, job in _iter_jobs(document):
        uses = job.get("uses")
        if isinstance(uses, str) and _LOCAL_WORKFLOW_RE.fullmatch(uses):
            edges.add(uses[2:])
    return edges


def _action_edges(document: ParsedDocument) -> set[str]:
    edges: set[str] = set()
    runs = document.data.get("runs", {})
    steps = runs.get("steps", []) if isinstance(runs, Mapping) else []
    if not isinstance(steps, list):
        return edges
    for step in steps:
        if not isinstance(step, Mapping):
            continue
        uses = step.get("uses")
        if (
            isinstance(uses, str)
            and uses.startswith("./")
            and not uses.startswith("./.github/workflows/")
        ):
            edges.add(uses[2:].rstrip("/"))
    return edges


def _find_cycles(graph: Mapping[str, set[str]]) -> list[list[str]]:
    cycles: list[list[str]] = []
    visiting: list[str] = []
    active: set[str] = set()
    visited: set[str] = set()

    def visit(node: str) -> None:
        if node in active:
            index = visiting.index(node)
            cycles.append(visiting[index:] + [node])
            return
        if node in visited:
            return
        visited.add(node)
        active.add(node)
        visiting.append(node)
        for target in sorted(graph.get(node, set())):
            visit(target)
        visiting.pop()
        active.remove(node)

    for node in sorted(graph):
        visit(node)
    return cycles


def _longest_depth(graph: Mapping[str, set[str]], start: str) -> int:
    memo: dict[str, int] = {}

    def depth(node: str, active: set[str]) -> int:
        if node in memo:
            return memo[node]
        if node in active:
            return 1_000_000
        next_active = set(active)
        next_active.add(node)
        result = 1
        for target in graph.get(node, set()):
            result = max(result, 1 + depth(target, next_active))
        memo[node] = result
        return result

    return depth(start, set())


def _validate_call_graphs(
    root: Path,
    workflow_documents: Mapping[str, ParsedDocument],
    action_documents: Mapping[str, ParsedDocument],
    public_contract: PublicContract,
    config: HarnessConfig,
    findings: list[Finding],
) -> None:
    workflow_graph = {
        path: _workflow_edges(document)
        for path, document in workflow_documents.items()
    }
    for path, targets in workflow_graph.items():
        for target in sorted(targets):
            if target not in workflow_documents:
                _finding(
                    findings,
                    config,
                    "missing-workflow-dependency",
                    path,
                    f"local reusable workflow dependency {target!r} does not exist",
                )
        if _is_internal_workflow(path) and targets:
            _finding(
                findings,
                config,
                "nested-internal-workflow",
                path,
                "internal leaf workflows may not call another reusable workflow",
            )
    for cycle in _find_cycles(workflow_graph):
        _finding(
            findings,
            config,
            "reusable-workflow-cycle",
            cycle[0],
            " -> ".join(cycle),
        )
    for path in sorted(workflow_graph):
        depth = _longest_depth(workflow_graph, path)
        if depth > public_contract.max_depth:
            _finding(
                findings,
                config,
                "reusable-workflow-depth",
                path,
                f"reusable workflow depth {depth} exceeds reviewed maximum {public_contract.max_depth}",
            )

    action_graph: dict[str, set[str]] = {}
    for path, document in action_documents.items():
        node = str(PurePosixPath(path).parent)
        action_graph[node] = _action_edges(document)
        for target in sorted(action_graph[node]):
            action_file_yml = root / target / "action.yml"
            action_file_yaml = root / target / "action.yaml"
            if not action_file_yml.exists() and not action_file_yaml.exists():
                _finding(
                    findings,
                    config,
                    "missing-action-dependency",
                    path,
                    f"local composite dependency {target!r} does not exist",
                )
    for cycle in _find_cycles(action_graph):
        _finding(
            findings,
            config,
            "composite-action-cycle",
            cycle[0],
            " -> ".join(cycle),
        )
