#!/usr/bin/env python3
"""Thin CLI adapter for hosted runner-image GHCR operations."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ci_workflows.hosted_runner_images import (  # noqa: E402
    HostedRunnerImageError,
    cleanup_hosted_runner_state,
    collect_metrics,
    publish_exact_image,
    verify_anonymous_pullability,
)
from ci_workflows.runner_images import write_github_outputs  # noqa: E402


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    sub = result.add_subparsers(dest="command", required=True)

    metrics = sub.add_parser("metrics")
    metrics.add_argument("--source-sha", required=True)
    metrics.add_argument("--image-reference", required=True)
    metrics.add_argument("--workspace", type=Path, required=True)
    metrics.add_argument("--github-output", type=Path)

    publish = sub.add_parser("publish")
    publish.add_argument("--source-sha", required=True)
    publish.add_argument("--local-reference", required=True)
    publish.add_argument("--versioned-reference", required=True)
    publish.add_argument("--latest-reference", required=True)
    publish.add_argument("--github-output", type=Path)

    anonymous = sub.add_parser("anonymous-readback")
    anonymous.add_argument("--versioned-reference", required=True)
    anonymous.add_argument("--latest-reference", required=True)
    anonymous.add_argument("--expected-digest", required=True)

    cleanup = sub.add_parser("cleanup")
    cleanup.add_argument("--image", required=True)
    cleanup.add_argument("--workspace", type=Path, required=True)
    cleanup.add_argument("--runner-temp", type=Path, required=True)
    cleanup.add_argument("--run-id", required=True)
    cleanup.add_argument("--run-attempt", required=True)
    return result


def _write(path: Path | None, values: dict[str, str]) -> None:
    if path is None:
        return
    with path.open("a", encoding="utf-8") as handle:
        write_github_outputs(handle, values)


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "metrics":
            metrics = collect_metrics(
                source_sha=args.source_sha,
                image_reference=args.image_reference,
                workspace=args.workspace.resolve(),
            )
            _write(
                args.github_output,
                {
                    "hosted_metrics_json": metrics.to_json(),
                    "image_size_bytes": str(metrics.image_size_bytes),
                    "largest_layer_bytes": str(metrics.largest_layer_bytes),
                    "workspace_free_bytes": str(metrics.workspace_free_bytes),
                    "docker_root_free_bytes": str(metrics.docker_root_free_bytes),
                },
            )
        elif args.command == "publish":
            digest = publish_exact_image(
                local_reference=args.local_reference,
                versioned_reference=args.versioned_reference,
                latest_reference=args.latest_reference,
                source_sha=args.source_sha,
            )
            _write(args.github_output, {"image_digest": digest})
        elif args.command == "anonymous-readback":
            verify_anonymous_pullability(
                versioned_reference=args.versioned_reference,
                latest_reference=args.latest_reference,
                expected_digest=args.expected_digest,
            )
        else:
            cleanup_hosted_runner_state(
                image_id=args.image,
                workspace=args.workspace,
                runner_temp=args.runner_temp,
                run_id=args.run_id,
                run_attempt=args.run_attempt,
            )
    except HostedRunnerImageError as error:
        print(f"hosted runner-image error: {error.code}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
