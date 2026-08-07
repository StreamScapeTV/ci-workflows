"""Compatibility adapter for shared non-language foundation commands."""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Sequence

_OPERATIONS = (
    "prepare-workspace",
    "verify-toolchain",
    "checkout-private-dependency",
    "verify-repository-policy",
    "render-evidence",
    "cleanup-workspace",
)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--root", type=Path, required=True)
    result.add_argument("operation", choices=_OPERATIONS)
    return result


def _ciw_arguments(arguments: argparse.Namespace) -> list[str] | None:
    prefix = ["--root", str(arguments.root)]
    if arguments.operation == "prepare-workspace":
        return [*prefix, "workspace", "prepare"]
    if arguments.operation == "cleanup-workspace":
        return [*prefix, "workspace", "cleanup"]
    if arguments.operation == "checkout-private-dependency":
        return [*prefix, "dependencies", "checkout-private"]
    if arguments.operation == "verify-repository-policy":
        return [*prefix, "policy", "verify-repository"]
    if arguments.operation == "render-evidence":
        return [*prefix, "evidence", "render"]
    tool_operation = os.environ.get("INPUT_OPERATION", "verify-set").strip()
    if tool_operation == "verify-set":
        return [*prefix, "tooling", "verify"]
    if tool_operation == "install-asset":
        return [*prefix, "tooling", "install-asset"]
    return None


def main(argv: Sequence[str] | None = None) -> int:
    """Preserve the existing flat CLI while delegating to ``ciw``."""

    arguments = parser().parse_args(argv)
    translated = _ciw_arguments(arguments)
    if translated is None:
        print("unsupported_tool_operation", file=sys.stderr)
        return 2
    from .ciw import main as ciw_main

    return ciw_main(translated)


if __name__ == "__main__":
    raise SystemExit(main())
