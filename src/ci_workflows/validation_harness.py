"""Canonical static validation harness for StreamScapeTV GitHub Actions."""
from __future__ import annotations

import dataclasses
import json
from pathlib import Path

from .readability import validate_repository_readability
from .validation_contracts import (
    _function_index,
    _validate_caller_fixture,
    _validate_fixture_coverage,
    _validate_tool_lock,
)
from .validation_graph import _validate_call_graphs
from .validation_helpers import _finding
from .validation_model import (
    ActionsLoader,
    ActionLock,
    Finding,
    HarnessConfig,
    HarnessFailure,
    ParsedDocument,
    PublicContract,
    RepositoryInventory,
    ValidationResult,
    _relative,
    discover_repository,
    load_action_lock,
    load_actions_yaml,
    load_harness_config,
    load_public_contract,
)
from .validation_policy import (
    _validate_action,
    _validate_global_duplicate_blocks,
    _validate_workflow,
)


def validate_repository(
    root: Path, *, include_public_api_validator: bool = True
) -> ValidationResult:
    """Validate the complete repository and return deterministic evidence."""

    root = root.resolve()
    inventory = discover_repository(root)
    config = load_harness_config(root)
    lock = load_action_lock(root)
    public_contract = load_public_contract(root)
    findings: list[Finding] = []
    _validate_tool_lock(root, lock, config, findings)
    _validate_fixture_coverage(root, config, findings)
    functions = _function_index(root, inventory.python_sources)
    docs_text = "\n".join(
        path.read_text(encoding="utf-8") for path in inventory.docs
    )

    workflow_documents: dict[str, ParsedDocument] = {}
    action_documents: dict[str, ParsedDocument] = {}
    duplicated_runs: dict[str, list[str]] = {}
    for path in inventory.workflows:
        relative_path = _relative(root, path)
        try:
            document = load_actions_yaml(path, root)
        except ValueError as error:
            _finding(
                findings,
                config,
                "invalid-actions-yaml",
                relative_path,
                str(error),
            )
            continue
        workflow_documents[relative_path] = document
        _validate_workflow(
            document,
            root,
            config,
            lock,
            public_contract,
            functions,
            docs_text,
            findings,
            duplicated_runs,
        )
    for path in inventory.actions:
        relative_path = _relative(root, path)
        try:
            document = load_actions_yaml(path, root)
        except ValueError as error:
            _finding(
                findings,
                config,
                "invalid-actions-yaml",
                relative_path,
                str(error),
            )
            continue
        action_documents[relative_path] = document
        _validate_action(document, config, lock, findings, duplicated_runs)
    _validate_call_graphs(
        root,
        workflow_documents,
        action_documents,
        public_contract,
        config,
        findings,
    )
    _validate_global_duplicate_blocks(duplicated_runs, config, findings)
    for readability_finding in validate_repository_readability(
        root,
        workflow_documents,
        action_documents,
    ):
        _finding(
            findings,
            config,
            readability_finding.rule,
            readability_finding.path,
            readability_finding.message,
            readability_finding.line,
        )

    callers_root = root / "tests/fixtures/harness/callers"
    for path in sorted(callers_root.glob("*.y*ml")):
        try:
            document = load_actions_yaml(path, root)
        except ValueError as error:
            _finding(
                findings,
                config,
                "invalid-actions-yaml",
                _relative(root, path),
                str(error),
            )
            continue
        _validate_caller_fixture(document, public_contract, config, findings)

    # The existing validator owns registry/type/product/consumer compatibility.
    try:
        from ci_workflows.public_api import (
            ContractError,
            validate as validate_public_api,
        )
    except ImportError:
        ContractError = RuntimeError  # type: ignore[assignment,misc]
        validate_public_api = None
    if include_public_api_validator and validate_public_api is not None:
        try:
            validate_public_api(root)
        except ContractError as error:
            _finding(
                findings,
                config,
                "public-api-contract",
                "contracts/public-workflows.json",
                str(error),
            )

    return ValidationResult(
        inventory=inventory,
        findings=tuple(sorted(set(findings))),
        test_count=len(inventory.tests),
        workflow_count=len(inventory.workflows),
        action_count=len(inventory.actions),
        public_api_count=len(public_contract.records_by_file),
    )


def render_summary(result: ValidationResult) -> str:
    """Render one stable machine-readable validation summary."""

    payload = {
        "actions": result.action_count,
        "findings": [dataclasses.asdict(finding) for finding in result.findings],
        "public_apis": result.public_api_count,
        "status": "passed" if not result.findings else "failed",
        "tests_discovered": result.test_count,
        "workflows": result.workflow_count,
    }
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


__all__ = (
    "ActionsLoader",
    "ActionLock",
    "Finding",
    "HarnessConfig",
    "HarnessFailure",
    "ParsedDocument",
    "PublicContract",
    "RepositoryInventory",
    "ValidationResult",
    "discover_repository",
    "load_actions_yaml",
    "load_harness_config",
    "load_public_contract",
    "render_summary",
    "validate_repository",
)
