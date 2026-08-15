"""Stable typed command registry and dispatcher with maintenance extensions."""
from __future__ import annotations

from . import ciw_core as _core
from .ciw_maintenance import (
    configure_flux_reconcile,
    configure_maintenance_artifacts,
    configure_maintenance_branches,
    configure_maintenance_conformance,
    configure_maintenance_runner_retry,
    execute_flux_reconcile,
    execute_maintenance_artifacts,
    execute_maintenance_branches,
    execute_maintenance_conformance,
    execute_maintenance_runner_retry,
)

CommandSpec = _core.CommandSpec
_BASE_COMMAND_SPECS = _core.command_specs


def _base_command_specs() -> tuple[CommandSpec, ...]:
    specs = _BASE_COMMAND_SPECS()
    for spec in specs:
        if spec.handler.__module__ == _core.__name__:
            spec.handler.__module__ = __name__
    return specs


def command_specs() -> tuple[CommandSpec, ...]:
    """Return the existing command tree plus bounded organization operations."""

    return _base_command_specs() + (
        CommandSpec(
            "maintenance",
            "artifacts",
            execute_maintenance_artifacts,
            configure_maintenance_artifacts,
        ),
        CommandSpec(
            "maintenance",
            "branches",
            execute_maintenance_branches,
            configure_maintenance_branches,
        ),
        CommandSpec(
            "maintenance",
            "conformance",
            execute_maintenance_conformance,
            configure_maintenance_conformance,
        ),
        CommandSpec(
            "maintenance",
            "runner-retry",
            execute_maintenance_runner_retry,
            configure_maintenance_runner_retry,
        ),
        CommandSpec(
            "flux",
            "reconcile",
            execute_flux_reconcile,
            configure_flux_reconcile,
        ),
    )


# The core dispatcher resolves ``command_specs`` through its module globals, so
# replacing only that registry hook keeps current-main behavior byte-for-byte
# while making the new commands available to parser/runtime validation/main.
_core.command_specs = command_specs
runtime_command_index = _core.runtime_command_index
validate_runtime_contract = _core.validate_runtime_contract
parser = _core.parser
main = _core.main


def __getattr__(name: str):
    """Preserve the existing ``ci_workflows.ciw`` module API."""

    return getattr(_core, name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(dir(_core)))


if __name__ == "__main__":
    raise SystemExit(main())
