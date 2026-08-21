#!/usr/bin/env python3
"""Resolve the fixed ci-workflows runner-image contract for GitHub Actions."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ci_workflows.runner_images import (  # noqa: E402
    RunnerImageError,
    build_plan,
    cleanup_runner_state,
    plan_outputs,
    release_outputs,
    write_github_outputs,
)


def _output(values: dict[str, str], output_path: str | None) -> None:
    if output_path:
        with Path(output_path).open("a", encoding="utf-8") as handle:
            write_github_outputs(handle, values)
    else:
        print(json.dumps(values, sort_keys=True, separators=(",", ":")))


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser()
    subparsers = result.add_subparsers(dest="command", required=True)

    plan = subparsers.add_parser("plan")
    plan.add_argument("--image", required=True)
    plan.add_argument("--source-sha", required=True)
    plan.add_argument("--source-root", default=str(ROOT))
    plan.add_argument("--release-tag", default="")
    plan.add_argument("--github-output")

    release = subparsers.add_parser("release")
    release.add_argument("--tag", required=True)
    release.add_argument("--source-sha", required=True)
    release.add_argument("--github-output")

    cleanup = subparsers.add_parser("cleanup")
    cleanup.add_argument("--image", required=True)
    cleanup.add_argument("--workspace", type=Path, required=True)
    cleanup.add_argument("--runner-temp", type=Path, required=True)
    cleanup.add_argument("--run-id", required=True)
    cleanup.add_argument("--run-attempt", required=True)
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "plan":
            plan = build_plan(
                Path(args.source_root),
                image_id=args.image,
                source_sha=args.source_sha,
                release_tag=args.release_tag or None,
            )
            _output(plan_outputs(plan), args.github_output)
        elif args.command == "release":
            _output(release_outputs(args.tag, args.source_sha), args.github_output)
        else:
            cleanup_runner_state(
                image_id=args.image,
                workspace=args.workspace,
                runner_temp=args.runner_temp,
                run_id=args.run_id,
                run_attempt=args.run_attempt,
            )
    except RunnerImageError as error:
        print(f"runner-image contract error: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
