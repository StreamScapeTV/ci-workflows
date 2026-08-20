"""Thin CLI adapter for internal Gradle dependency-cache synchronization."""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

from .ciw_types import CIWContext, CIWError, CIWResult, input_value, project_error
from .gradle_seed import GradleSeedError
from .gradle_seed_internal import sync_gradle_seed


def configure_gradle_seed_upload(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--source-sha")


def _require_registered_gradle_home(environment: Mapping[str, str]) -> None:
    """Bind synchronization to ``prepare-workspace profile: gradle`` state."""

    raw_root = environment.get("CI_WORKFLOW_ROOT", "")
    raw_home = environment.get("GRADLE_USER_HOME", "")
    raw_state = environment.get("CI_WORKFLOW_STATE_ID", "")
    if not raw_root or not raw_home or not raw_state:
        raise CIWError("gradle-seed", "gradle_seed_home_rejected")
    root = Path(raw_root)
    home = Path(raw_home)
    if (
        not root.is_absolute()
        or not home.is_absolute()
        or home != root / "gradle"
        or root.name != raw_state
    ):
        raise CIWError("gradle-seed", "gradle_seed_home_rejected")


def execute_gradle_seed_upload(
    args: argparse.Namespace,
    context: CIWContext,
) -> CIWResult:
    if not context.environment.get("GITHUB_OUTPUT", ""):
        raise CIWError("gradle-seed", "github_output_missing")
    _require_registered_gradle_home(context.environment)
    source_sha = (
        str(args.source_sha).strip()
        if args.source_sha is not None
        else input_value(context.environment, "source_sha")
    )
    if not source_sha:
        raise CIWError("gradle-seed", "gradle_seed_source_sha_required")

    def report_selection(file_count: int, total_bytes: int) -> None:
        context.stdout.write(
            f"gradle-seed delta file_count={file_count} total_bytes={total_bytes}\n"
        )

    try:
        result = sync_gradle_seed(
            source_sha=source_sha,
            environment=context.environment,
            report_selection=report_selection,
        )
    except GradleSeedError as error:
        raise CIWError("gradle-seed", error.code) from None
    return CIWResult(
        "gradle-seed",
        "upload",
        outputs=result.output_values(),
    )


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        prog="gradle-seed",
        description="Sync one Gradle dependency delta to the internal cache plane.",
    )
    result.add_argument("--root", type=Path, default=Path.cwd())
    configure_gradle_seed_upload(result)
    return result


def main(
    argv: Sequence[str] | None = None,
    *,
    environment: Mapping[str, str] | None = None,
    stdout: Any | None = None,
    stderr: Any | None = None,
) -> int:
    args = parser().parse_args(argv)
    output = sys.stdout if stdout is None else stdout
    errors = sys.stderr if stderr is None else stderr
    context = CIWContext(
        root=args.root.resolve(),
        environment=dict(os.environ if environment is None else environment),
        stdout=output,
        stderr=errors,
    )
    try:
        execute_gradle_seed_upload(args, context).emit(context)
    except BaseException as error:
        projected = project_error(error, domain="gradle-seed")
        errors.write(f"gradle-seed upload failed: {projected.code}\n")
        return projected.exit_code
    return 0
