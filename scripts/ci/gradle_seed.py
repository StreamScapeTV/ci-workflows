#!/usr/bin/env python3
"""Thin fixed CLI adapter for trusted Gradle dependency-seed promotion."""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Mapping, Sequence

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ci_workflows.ciw_types import CIWError, write_command_file
from ci_workflows.gradle_seed import GradleSeedError, promote_gradle_seed


def _require_registered_gradle_home(environment: Mapping[str, str]) -> None:
    """Bind collection to ``prepare-workspace profile: gradle`` registered state."""

    raw_root = environment.get("CI_WORKFLOW_ROOT", "")
    raw_home = environment.get("GRADLE_USER_HOME", "")
    if not raw_root or not raw_home:
        raise GradleSeedError("gradle_seed_home_rejected")
    root = Path(raw_root)
    home = Path(raw_home)
    if not root.is_absolute() or not home.is_absolute() or home != root / "gradle":
        raise GradleSeedError("gradle_seed_home_rejected")


def _source_sha(args: argparse.Namespace, environment: Mapping[str, str]) -> str:
    raw = args.source_sha if args.source_sha is not None else environment.get("INPUT_SOURCE_SHA", "")
    value = str(raw).strip()
    if not value:
        raise GradleSeedError("gradle_seed_source_sha_required")
    return value


def main(
    argv: Sequence[str] | None = None,
    *,
    environment: Mapping[str, str] | None = None,
) -> int:
    parser = argparse.ArgumentParser(prog="gradle-seed")
    parser.add_argument("--source-sha")
    args = parser.parse_args(argv)
    env = dict(os.environ if environment is None else environment)
    output_path = env.get("GITHUB_OUTPUT", "")
    if not output_path:
        print("gradle-seed: github_output_missing", file=sys.stderr)
        return 2

    try:
        _require_registered_gradle_home(env)
        result = promote_gradle_seed(
            source_sha=_source_sha(args, env),
            environment=env,
        )
        write_command_file(Path(output_path), result.output_values())
    except GradleSeedError as error:
        print(f"gradle-seed: {error.code}", file=sys.stderr)
        return 2
    except CIWError as error:
        print(f"gradle-seed: {error.code}", file=sys.stderr)
        return error.exit_code
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
