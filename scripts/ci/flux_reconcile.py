#!/usr/bin/env python3
"""Compatibility adapter for the registered trusted Flux reconciliation command."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ci_workflows.ciw import main  # noqa: E402


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


def compatibility_argv(argv: list[str] | None = None) -> list[str]:
    args = parser().parse_args(argv)
    if args.source_root != "source" or args.source_repository != "StreamScapeTV/flux":
        parser().error("Flux compatibility source must be exact checked-out StreamScapeTV/flux at source")
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


if __name__ == "__main__":
    raise SystemExit(main(compatibility_argv()))
