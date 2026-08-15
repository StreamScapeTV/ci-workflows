"""Stable typed command dispatcher with bounded maintenance extensions."""
from __future__ import annotations

import os
import sys
from collections.abc import Mapping, Sequence
from typing import Any

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


def _extended_command_specs() -> tuple[CommandSpec, ...]:
    return _base_command_specs() + (
        CommandSpec("maintenance", "artifacts", execute_maintenance_artifacts, configure_maintenance_artifacts),
        CommandSpec("maintenance", "branches", execute_maintenance_branches, configure_maintenance_branches),
        CommandSpec("maintenance", "conformance", execute_maintenance_conformance, configure_maintenance_conformance),
        CommandSpec("maintenance", "runner-retry", execute_maintenance_runner_retry, configure_maintenance_runner_retry),
        CommandSpec("flux", "reconcile", execute_flux_reconcile, configure_flux_reconcile),
    )


def command_specs() -> tuple[CommandSpec, ...]:
    """Return the released current-main registry used by shared contract checks."""

    return _base_command_specs()


def runtime_command_index() -> dict[str, CommandSpec]:
    """Return the released current-main runtime index for registry validation."""

    return {spec.key: spec for spec in _base_command_specs()}


def validate_runtime_contract(root):
    return _core.validate_runtime_contract(root)


def parser():
    """Build the parser with the bounded maintenance/Flux extension enabled."""

    original = _core.command_specs
    _core.command_specs = _extended_command_specs
    try:
        return _core.parser()
    finally:
        _core.command_specs = original


def main(
    argv: Sequence[str] | None = None,
    *,
    environment: Mapping[str, str] | None = None,
    stdout: Any | None = None,
    stderr: Any | None = None,
) -> int:
    """Dispatch a released CIW command or one of issue #20's bounded operations."""

    args = parser().parse_args(argv)
    output = sys.stdout if stdout is None else stdout
    errors = sys.stderr if stderr is None else stderr
    context = _core.CIWContext(
        root=args.root.resolve(),
        environment=dict(os.environ if environment is None else environment),
        stdout=output,
        stderr=errors,
    )
    spec: CommandSpec = args._command_spec
    try:
        _core.validate_runtime_contract(context.root)
        result = spec.handler(args, context)
        if result.domain != spec.domain or result.operation != spec.operation:
            raise _core.CIWError("ciw", "ciw_result_command_mismatch")
        result.emit(context)
    except BaseException as error:
        projected = _core.project_error(error, domain=spec.domain)
        errors.write(f"ciw {spec.domain} {spec.operation} failed: {projected.code}\n")
        return projected.exit_code
    return 0


def __getattr__(name: str):
    return getattr(_core, name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(dir(_core)))


if __name__ == "__main__":
    raise SystemExit(main())
