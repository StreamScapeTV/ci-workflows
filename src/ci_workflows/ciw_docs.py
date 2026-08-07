"""Deterministic contract validation and documentation for the ``ciw`` command tree."""
from __future__ import annotations

import json
import re
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence

from .ciw_types import CIWError

CONTRACT_PATH = Path("contracts/ciw-commands.json")
OUTPUT_PATH = Path("docs/reference/ciw.md")
_COMMAND_KEY = re.compile(r"^[a-z][a-z0-9-]{1,63} [a-z][a-z0-9-]{1,63}$")
_QUALIFIED_FUNCTION = re.compile(
    r"^ci_workflows\.[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)*$"
)


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise CIWError("ciw", code)


def _safe_path(value: Any, code: str) -> str:
    _require(isinstance(value, str) and bool(value), code)
    path = PurePosixPath(value)
    _require(
        not path.is_absolute()
        and ".." not in path.parts
        and "\\" not in value
        and all(part not in {"", "."} for part in path.parts),
        code,
    )
    return path.as_posix()


def _strings(value: Any, code: str, *, allow_empty: bool = True) -> list[str]:
    _require(isinstance(value, list), code)
    _require(all(isinstance(item, str) and bool(item) for item in value), code)
    _require(allow_empty or bool(value), code)
    return list(value)


def _command_key(command: Mapping[str, Any]) -> str:
    domain = command.get("domain")
    operation = command.get("operation")
    _require(
        isinstance(domain, str)
        and isinstance(operation, str)
        and _COMMAND_KEY.fullmatch(f"{domain} {operation}") is not None,
        "ciw_command_name_invalid",
    )
    return f"{domain} {operation}"


def _validate_aliases(
    aliases: Mapping[str, str],
    commands: set[str],
) -> None:
    for alias, target in aliases.items():
        _require(
            isinstance(alias, str)
            and isinstance(target, str)
            and _COMMAND_KEY.fullmatch(alias) is not None
            and _COMMAND_KEY.fullmatch(target) is not None,
            "ciw_alias_invalid",
        )
        _require(alias not in commands, "ciw_alias_shadows_command")
    for alias in sorted(aliases):
        visited: set[str] = set()
        current = alias
        while current in aliases:
            _require(current not in visited, "ciw_alias_cycle")
            visited.add(current)
            current = aliases[current]
        _require(current in commands, "ciw_alias_target_missing")


def validate_command_contract(payload: Mapping[str, Any]) -> tuple[str, ...]:
    """Validate the checked-in command registry and return stable command keys."""

    _require(payload.get("schema_version") == 1, "ciw_contract_schema_unsupported")
    _require(payload.get("command_version") == "1.0.0", "ciw_contract_version_invalid")
    _require(payload.get("program") == "ciw", "ciw_contract_program_invalid")
    _require(
        _safe_path(payload.get("entry_point"), "ciw_contract_entry_point_invalid")
        == "scripts/ci/ciw.py",
        "ciw_contract_entry_point_invalid",
    )
    raw_commands = payload.get("commands")
    _require(isinstance(raw_commands, list) and raw_commands, "ciw_commands_missing")
    commands: dict[str, Mapping[str, Any]] = {}
    required_fields = {
        "domain",
        "operation",
        "handler",
        "trust_class",
        "inputs",
        "outputs",
        "side_effects",
        "cleanup",
        "failure",
    }
    for raw in raw_commands:
        _require(isinstance(raw, Mapping), "ciw_command_invalid")
        _require(set(raw) == required_fields, "ciw_command_fields_invalid")
        key = _command_key(raw)
        _require(key not in commands, "ciw_command_duplicate")
        handler = raw.get("handler")
        _require(
            isinstance(handler, str)
            and _QUALIFIED_FUNCTION.fullmatch(handler) is not None,
            "ciw_handler_invalid",
        )
        trust_class = raw.get("trust_class")
        cleanup = raw.get("cleanup")
        failure = raw.get("failure")
        _require(
            isinstance(trust_class, str)
            and bool(trust_class)
            and isinstance(cleanup, str)
            and bool(cleanup)
            and isinstance(failure, str)
            and bool(failure),
            "ciw_command_metadata_invalid",
        )
        _strings(raw.get("inputs"), "ciw_command_inputs_invalid")
        _strings(raw.get("outputs"), "ciw_command_outputs_invalid")
        _strings(raw.get("side_effects"), "ciw_command_side_effects_invalid")
        commands[key] = raw

    aliases = payload.get("aliases", {})
    _require(
        isinstance(aliases, Mapping)
        and all(isinstance(key, str) and isinstance(value, str) for key, value in aliases.items()),
        "ciw_aliases_invalid",
    )
    _validate_aliases(dict(aliases), set(commands))

    wrappers = payload.get("compatibility_wrappers")
    _require(isinstance(wrappers, list) and wrappers, "ciw_wrappers_missing")
    seen_wrappers: set[str] = set()
    for wrapper in wrappers:
        _require(
            isinstance(wrapper, Mapping)
            and set(wrapper) == {"path", "commands"},
            "ciw_wrapper_invalid",
        )
        path = _safe_path(wrapper.get("path"), "ciw_wrapper_path_invalid")
        _require(path not in seen_wrappers, "ciw_wrapper_duplicate")
        seen_wrappers.add(path)
        wrapper_commands = _strings(
            wrapper.get("commands"),
            "ciw_wrapper_commands_invalid",
            allow_empty=False,
        )
        _require(set(wrapper_commands) <= set(commands), "ciw_wrapper_command_missing")

    forbidden = set(
        _strings(
            payload.get("forbidden_dispatch_inputs"),
            "ciw_forbidden_inputs_invalid",
            allow_empty=False,
        )
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
        "ciw_forbidden_inputs_incomplete",
    )
    future = _strings(
        payload.get("future_namespaces"),
        "ciw_future_namespaces_invalid",
        allow_empty=False,
    )
    active_domains = {key.split(" ", 1)[0] for key in commands}
    _require(not (set(future) & active_domains), "ciw_future_namespace_active")
    return tuple(sorted(commands))


def load_command_contract(root: Path) -> Mapping[str, Any]:
    try:
        payload = json.loads((root / CONTRACT_PATH).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise CIWError("ciw", "ciw_contract_unavailable") from error
    _require(isinstance(payload, Mapping), "ciw_contract_invalid")
    validate_command_contract(payload)
    return payload


def _inline(values: Sequence[str]) -> str:
    return ", ".join(f"`{value}`" for value in values) if values else "none"


def render_ciw_docs(*, contract_root: Path) -> str:
    contract = load_command_contract(contract_root)
    commands = sorted(
        contract["commands"],
        key=lambda item: (str(item["domain"]), str(item["operation"])),
    )
    lines = [
        "# `ciw` named command library",
        "",
        "Generated from `contracts/ciw-commands.json`. Do not edit directly.",
        "",
        f"Command contract version: `{contract['command_version']}`.",
        "",
        "The registry is checked in, typed, and fail closed. It dispatches only to "
        "explicit named handlers and never imports a caller-selected module, "
        "function, callback, runner, engine, secret, or deletion target.",
        "",
        "## Commands",
        "",
        "| Command | Named handler | Trust class | Inputs | Outputs | Side effects | Cleanup | Failure code |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for command in commands:
        lines.append(
            "| "
            f"`ciw {command['domain']} {command['operation']}` | "
            f"`{command['handler']}` | `{command['trust_class']}` | "
            f"{_inline(command['inputs'])} | {_inline(command['outputs'])} | "
            f"{_inline(command['side_effects'])} | {command['cleanup']} | "
            f"`{command['failure']}` |"
        )
    lines.extend(
        [
            "",
            "## Compatibility wrappers",
            "",
            "Existing entry points remain supported while delegating to the same "
            "registered commands:",
            "",
        ]
    )
    for wrapper in sorted(contract["compatibility_wrappers"], key=lambda item: item["path"]):
        commands_text = ", ".join(f"`ciw {value}`" for value in wrapper["commands"])
        lines.append(f"- `{wrapper['path']}` → {commands_text}.")
    lines.extend(
        [
            "",
            "## Shared result and error projection",
            "",
            "- `CIWResult` carries stable string outputs, bounded environment updates, "
            "an optional redacted summary, and optional deterministic stdout.",
            "- `CIWError` carries only a safe domain, stable code, and bounded exit code.",
            "- Existing source, runner, foundation, and release-tag error codes remain "
            "unchanged at the projection boundary.",
            "- GitHub command-file names and values reject invalid names and CR/LF injection.",
            "",
            "## Future extension points",
            "",
            "The following namespaces are reserved but not implemented by this issue:",
            "",
            _inline(contract["future_namespaces"]) + ".",
            "",
        ]
    )
    return "\n".join(lines)


def write_ciw_docs(*, contract_root: Path, check: bool = False) -> Path:
    output = contract_root / OUTPUT_PATH
    rendered = render_ciw_docs(contract_root=contract_root)
    if check:
        try:
            current = output.read_text(encoding="utf-8")
        except OSError as error:
            raise CIWError("ciw", "ciw_docs_missing") from error
        _require(current == rendered, "ciw_docs_drift")
        return output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(rendered, encoding="utf-8", newline="\n")
    return output
