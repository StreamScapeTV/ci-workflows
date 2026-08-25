#!/usr/bin/env python3
"""Invoke the checked-in typed ``ciw`` command registry and bounded adapters."""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Sequence

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ci_workflows.central_profile import main as central_profile_main  # noqa: E402
from ci_workflows.ci_lifecycle import main as ci_lifecycle_main  # noqa: E402
from ci_workflows.ciw import main as registry_main  # noqa: E402
from ci_workflows.ciw_gradle_seed import main as gradle_seed_main  # noqa: E402
from ci_workflows.github_app_token import main as github_app_token_main  # noqa: E402
from ci_workflows.gradle_dependency_warm import main as gradle_dependency_warm_main  # noqa: E402
from ci_workflows.gradle_maven_publish import main as gradle_maven_publish_main  # noqa: E402


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments[:1] == ["lifecycle"]:
        return ci_lifecycle_main(arguments[1:])
    if arguments[:1] == ["central-profile"]:
        return central_profile_main(arguments[1:])
    if arguments[:1] == ["github-app"]:
        return github_app_token_main(arguments[1:])
    if arguments[:2] == ["gradle-seed", "upload"]:
        return gradle_seed_main(["--root", str(ROOT), *arguments[2:]])
    if arguments[:2] == ["gradle", "warm-dependencies"]:
        return gradle_dependency_warm_main(arguments[2:])
    if arguments == ["gradle-maven", "publish"]:
        return gradle_maven_publish_main()
    return registry_main(["--root", str(ROOT), *arguments])


if __name__ == "__main__":
    raise SystemExit(main())
