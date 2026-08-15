"""Stable typed command dispatcher with bounded maintenance extensions."""
from __future__ import annotations

import os
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
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

# Preserve the long-standing module patch surface used by focused tests and
# compatibility callers even though the released core dispatcher lives in a
# separate module. These aliases are intentionally patchable at this facade.
execute_android_validate = _core.execute_android_validate
execute_apple_validate = _core.execute_apple_validate
exact_checkout = _core.exact_checkout
event_from_environment = _core.event_from_environment
resolve_release_authority = _core.resolve_release_authority
revalidate_release_authority = _core.revalidate_release_authority
authority_from_expected = _core.authority_from_expected
_release_provider = _core._release_provider


def handle_android_validate(args, context):
    return execute_android_validate(args, context)


def handle_apple_validate(args, context):
    return execute_apple_validate(args, context)


def handle_source_exact_checkout(args, context):
    _core._require_output(context, domain="source")
    outputs = exact_checkout(
        repository=args.repository,
        admitted_sha=args.admitted_sha,
        path=args.path,
        fetch_depth=args.fetch_depth,
        token=context.environment.get("CHECKOUT_TOKEN", ""),
        workspace=Path(context.environment.get("GITHUB_WORKSPACE", ".")),
    )
    return _core.CIWResult("source", "exact-checkout", outputs=dict(outputs))


def handle_release_tag_resolve(_args, context):
    _core._require_output(context, domain="release-tag")
    authority = resolve_release_authority(
        _core.ReleaseInputs(
            release_mode=_core.input_value(
                context.environment,
                "release_mode",
                "tag-push",
            ),
            release_version=_core.input_value(
                context.environment,
                "release_version",
            ),
            release_source_sha=_core.input_value(
                context.environment,
                "release_source_sha",
            ),
        ),
        event_from_environment(context.environment),
        _release_provider(context),
    )
    return _core.CIWResult(
        "release-tag",
        "resolve",
        outputs=_core._release_outputs(authority),
        stdout_text=(
            "release tag authority accepted: "
            f"mode={authority.release_mode} "
            f"version={authority.release_version} "
            f"source={authority.release_source_sha}"
        ),
    )


def handle_release_tag_revalidate(_args, context):
    _core._require_output(context, domain="release-tag")
    authority = authority_from_expected(
        release_mode=_core.required_environment(
            context.environment,
            "INPUT_RELEASE_MODE",
            domain="release-tag",
        ),
        release_version=_core.required_environment(
            context.environment,
            "INPUT_RELEASE_VERSION",
            domain="release-tag",
        ),
        release_source_sha=_core.required_environment(
            context.environment,
            "INPUT_RELEASE_SOURCE_SHA",
            domain="release-tag",
        ),
        tag_object_sha=_core.required_environment(
            context.environment,
            "INPUT_EXPECTED_TAG_OBJECT_SHA",
            domain="release-tag",
        ),
        tag_commit_sha=_core.required_environment(
            context.environment,
            "INPUT_EXPECTED_TAG_COMMIT_SHA",
            domain="release-tag",
        ),
    )
    authority = revalidate_release_authority(
        authority,
        event_from_environment(context.environment),
        _release_provider(context),
    )
    return _core.CIWResult(
        "release-tag",
        "revalidate",
        outputs=_core._release_outputs(authority),
        stdout_text=(
            "release tag authority accepted: "
            f"mode={authority.release_mode} "
            f"version={authority.release_version} "
            f"source={authority.release_source_sha}"
        ),
    )


_FACADE_HANDLERS = {
    "source exact-checkout": handle_source_exact_checkout,
    "android validate": handle_android_validate,
    "apple validate": handle_apple_validate,
    "release-tag resolve": handle_release_tag_resolve,
    "release-tag revalidate": handle_release_tag_revalidate,
}


def _base_command_specs() -> tuple[CommandSpec, ...]:
    specs: list[CommandSpec] = []
    for spec in _BASE_COMMAND_SPECS():
        # The checked-in contract has always exposed ci_workflows.ciw as the
        # stable handler module. Keep that identity for the preserved core.
        if spec.handler.__module__ == _core.__name__:
            spec.handler.__module__ = __name__
        specs.append(
            CommandSpec(
                spec.domain,
                spec.operation,
                _FACADE_HANDLERS.get(spec.key, spec.handler),
                spec.configure,
            )
        )
    return tuple(specs)


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
