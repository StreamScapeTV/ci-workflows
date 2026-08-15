#!/usr/bin/env python3
"""Compatibility adapter for the registered trusted Flux reconciliation command."""
from __future__ import annotations

import argparse
import io
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ci_workflows.ciw import main  # noqa: E402
from ci_workflows.maintenance_contract import MaintenanceError, load_contract  # noqa: E402


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser()
    result.add_argument("--source-root", required=True)
    result.add_argument("--source-repository", required=True)
    result.add_argument("--admitted-sha", required=True)
    result.add_argument("--target-id", required=True)
    result.add_argument("--product-id", required=True)
    result.add_argument("--operation", required=True)
    result.add_argument("--policy-path", required=True)
    result.add_argument("--allowlist-path", required=True)
    result.add_argument("--request-id", required=True)
    result.add_argument("--dry-run", required=True)
    return result


def compatibility_argv(args: argparse.Namespace) -> list[str]:
    # Preserve the legacy security boundary for valid requests: the compatibility
    # adapter may execute only an exact checkout of StreamScapeTV/flux at source.
    # Invalid request identifiers are deliberately allowed through this adapter
    # first so the canonical CIW maintenance contract projects the historical
    # invalid_request_id result before any source or state-path handling.
    request_is_valid = True
    try:
        load_contract(ROOT).validate_request_id(args.request_id)
    except MaintenanceError:
        request_is_valid = False
    if request_is_valid and (
        args.source_root != "source"
        or args.source_repository != "StreamScapeTV/flux"
    ):
        parser().error(
            "Flux compatibility source must be exact checked-out "
            "StreamScapeTV/flux at source"
        )
    return [
        "--root",
        str(ROOT),
        "flux",
        "reconcile",
        "--admitted-sha",
        args.admitted_sha,
        "--target-id",
        args.target_id,
        "--product-id",
        args.product_id,
        "--operation",
        args.operation,
        "--policy-path",
        args.policy_path,
        "--allowlist-path",
        args.allowlist_path,
        "--request-id",
        args.request_id,
        "--dry-run",
        args.dry_run,
    ]


def _read_outputs(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        name, separator, value = line.partition("=")
        if separator and name:
            values[name] = value
    return values


def run(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    environment = os.environ.copy()
    captured_stdout = io.StringIO()
    captured_stderr = io.StringIO()
    with tempfile.TemporaryDirectory(prefix="ciw-flux-compat-") as directory:
        output_path = Path(directory) / "github-output"
        environment["GITHUB_OUTPUT"] = str(output_path)
        status = main(
            compatibility_argv(args),
            environment=environment,
            stdout=captured_stdout,
            stderr=captured_stderr,
        )
        values = _read_outputs(output_path)
    if values:
        print(json.dumps(values, sort_keys=True))
    elif captured_stdout.getvalue():
        print(captured_stdout.getvalue(), end="")
    # The historical compatibility entry point returned 1 for a typed domain
    # failure even though CIW itself standardizes projected failures on exit 2.
    return 0 if status == 0 else 1


if __name__ == "__main__":
    raise SystemExit(run())
