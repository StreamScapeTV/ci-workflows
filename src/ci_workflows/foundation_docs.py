"""Deterministic documentation renderer for shared foundation primitives."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from .foundation_types import FoundationError, load_contract, require

CONTRACT = "contracts/foundation-primitives.json"
OUTPUT = "docs/architecture/foundation-primitives.md"


def _inline(values: Any) -> str:
    require(isinstance(values, list), "foundation_docs_contract_invalid")
    return ", ".join(f"`{value}`" for value in values) if values else "none"


def render_foundation_docs(*, contract_root: Path) -> str:
    contract = load_contract(contract_root, CONTRACT)
    modules = contract.get("modules")
    actions = contract.get("actions")
    constraints = contract.get("integration_constraints")
    require(
        isinstance(modules, list)
        and isinstance(actions, list)
        and isinstance(constraints, list),
        "foundation_docs_contract_invalid",
    )
    lines = [
        "# Shared foundation primitives",
        "",
        "Generated from `contracts/foundation-primitives.json`. Do not edit directly.",
        "",
        f"Architecture: `{contract.get('architecture')}`.",
        "",
        "## Named modules",
        "",
    ]
    for module in modules:
        require(isinstance(module, Mapping), "foundation_docs_contract_invalid")
        lines.extend(
            [
                f"### `{module.get('module')}`",
                "",
                f"Trust class: `{module.get('trust_class')}`.",
                "",
                "| Function | Inputs | Outputs | Side effects | Cleanup duty |",
                "|---|---|---|---|---|",
            ]
        )
        functions = module.get("functions")
        require(isinstance(functions, list), "foundation_docs_contract_invalid")
        for function in functions:
            require(isinstance(function, Mapping), "foundation_docs_contract_invalid")
            lines.append(
                "| "
                f"`{function.get('name')}` | {_inline(function.get('inputs'))} | "
                f"{_inline(function.get('outputs'))} | {_inline(function.get('side_effects'))} | "
                f"{function.get('cleanup')} |"
            )
        lines.append("")
    lines.extend(
        [
            "## Composite actions",
            "",
            "| Action | Named function boundary | Inputs | Outputs | Cleanup duty |",
            "|---|---|---|---|---|",
        ]
    )
    for action in actions:
        require(isinstance(action, Mapping), "foundation_docs_contract_invalid")
        lines.append(
            "| "
            f"`{action.get('path')}` | `{action.get('function')}` | "
            f"{_inline(action.get('inputs'))} | {_inline(action.get('outputs'))} | "
            f"{action.get('cleanup')} |"
        )
    lines.extend(["", "## Integration constraints", ""])
    for constraint in constraints:
        require(isinstance(constraint, str) and constraint, "foundation_docs_contract_invalid")
        lines.append(f"- {constraint}")
    lines.extend(
        [
            "",
            "## Bootstrap basis",
            "",
            "Issue #8 was implemented under explicit repository-owner bootstrap authorization during the Agent State-to-Supabase transition. No legacy Agent State receipt, issue-comment transport, manual workflow dispatch, or `agentctl` result is claimed.",
            "",
        ]
    )
    return "\n".join(lines)


def write_foundation_docs(*, contract_root: Path, check: bool = False) -> Path:
    output = contract_root / OUTPUT
    rendered = render_foundation_docs(contract_root=contract_root)
    if check:
        try:
            current = output.read_text(encoding="utf-8")
        except OSError as error:
            raise FoundationError("foundation_docs_missing") from error
        require(current == rendered, "foundation_docs_drift")
        return output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(rendered, encoding="utf-8", newline="\n")
    return output
