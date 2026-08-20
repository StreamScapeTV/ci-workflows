#!/usr/bin/env python3
"""Thin CLI for public native image + Helm publication helpers."""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ci_workflows.public_native_image_chart import (  # noqa: E402
    PublicNativeReleaseError,
    authenticate,
    cleanup,
    publish,
    readback_public,
    require_unused_version,
    verify_host,
)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    commands = result.add_subparsers(dest="command", required=True)
    commands.add_parser("verify-host")

    for name in ("authenticate", "require-unused", "publish", "readback", "cleanup"):
        command = commands.add_parser(name)
        command.add_argument("--image-name", required=True)
        command.add_argument("--chart-name", required=False)
        command.add_argument("--version", required=True)
        command.add_argument("--source-root", type=Path, default=Path("source"))
        if name == "publish":
            command.add_argument("--package-path", type=Path, required=True)
        if name in {"readback", "cleanup"}:
            command.add_argument("--state-root", type=Path, required=True)
        if name == "readback":
            command.add_argument("--github-output", type=Path, required=True)
    return result


def _write_outputs(path: Path, values: dict[str, str]) -> None:
    with path.open("a", encoding="utf-8") as stream:
        for key, value in values.items():
            if "\n" in value or "\r" in value:
                raise PublicNativeReleaseError("invalid_output")
            stream.write(f"{key}={value}\n")


def main() -> int:
    args = parser().parse_args()
    environment = dict(os.environ)
    try:
        if args.command == "verify-host":
            verify_host(environment)
            return 0

        source_root = args.source_root.resolve()
        if args.command == "authenticate":
            authenticate(environment=environment, cwd=source_root)
        elif args.command == "require-unused":
            if not args.chart_name:
                raise PublicNativeReleaseError("missing_chart_name")
            require_unused_version(
                image_name=args.image_name,
                chart_name=args.chart_name,
                version=args.version,
                environment=environment,
            )
        elif args.command == "publish":
            if not args.chart_name:
                raise PublicNativeReleaseError("missing_chart_name")
            publish(
                image_name=args.image_name,
                chart_name=args.chart_name,
                version=args.version,
                package_path=args.package_path.resolve(),
                environment=environment,
                cwd=source_root,
            )
        elif args.command == "readback":
            if not args.chart_name:
                raise PublicNativeReleaseError("missing_chart_name")
            state_root = args.state_root.resolve()
            values = readback_public(
                image_name=args.image_name,
                chart_name=args.chart_name,
                version=args.version,
                anonymous_authfile=state_root / "anonymous-auth.json",
                environment=environment,
                cwd=source_root,
            )
            _write_outputs(args.github_output, values)
        elif args.command == "cleanup":
            cleanup(
                image_name=args.image_name,
                version=args.version,
                state_root=args.state_root.resolve(),
                environment=environment,
                cwd=source_root,
            )
        else:
            raise PublicNativeReleaseError("unknown_command")
    except PublicNativeReleaseError as error:
        print(str(error), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
