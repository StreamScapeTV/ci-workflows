"""Thin ``ciw gradle-seed upload`` adapter."""
from __future__ import annotations

import argparse

from .ciw_types import CIWContext, CIWError, CIWResult, input_value
from .gradle_seed import GradleSeedError, promote_gradle_seed


def configure_gradle_seed_upload(parser: argparse.ArgumentParser) -> None:
    """Register the optional exact source-SHA CLI override."""

    parser.add_argument("--source-sha")


def execute_gradle_seed_upload(
    args: argparse.Namespace,
    context: CIWContext,
) -> CIWResult:
    if not context.environment.get("GITHUB_OUTPUT", ""):
        raise CIWError("gradle-seed", "github_output_missing")
    source_sha = (
        str(args.source_sha).strip()
        if args.source_sha is not None
        else input_value(context.environment, "source_sha")
    )
    if not source_sha:
        raise CIWError("gradle-seed", "gradle_seed_source_sha_required")
    try:
        result = promote_gradle_seed(
            source_sha=source_sha,
            environment=context.environment,
        )
    except GradleSeedError as error:
        raise CIWError("gradle-seed", error.code) from None
    return CIWResult(
        "gradle-seed",
        "upload",
        outputs=result.output_values(),
    )
