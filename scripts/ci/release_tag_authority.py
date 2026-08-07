#!/usr/bin/env python3
"""Resolve or revalidate one exact immutable release tag."""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Sequence

from ci_workflows.release_tag_authority import (
    GitHubTagProvider,
    ReleaseInputs,
    ReleaseTagError,
    authority_from_expected,
    event_from_environment,
    revalidate_release_authority,
    resolve_release_authority,
    write_outputs,
)


def _required(environment: dict[str, str], name: str) -> str:
    value = environment.get(name, "")
    if not value:
        raise ReleaseTagError(f"{name.lower()}_required")
    return value


def main(argv: Sequence[str] | None = None) -> int:
    args = list(argv or sys.argv[1:])
    if args:
        print("release tag authority accepts no positional arguments", file=sys.stderr)
        return 2
    environment = dict(os.environ)
    try:
        event = event_from_environment(environment)
        provider = GitHubTagProvider(
            api_url=_required(environment, "GITHUB_API_URL"),
            token=_required(environment, "GITHUB_TOKEN"),
        )
        phase = environment.get("INPUT_PHASE", "resolve")
        if phase == "resolve":
            authority = resolve_release_authority(
                ReleaseInputs(
                    release_mode=environment.get(
                        "INPUT_RELEASE_MODE",
                        "tag-push",
                    ),
                    release_version=environment.get(
                        "INPUT_RELEASE_VERSION",
                        "",
                    ),
                    release_source_sha=environment.get(
                        "INPUT_RELEASE_SOURCE_SHA",
                        "",
                    ),
                ),
                event,
                provider,
            )
        elif phase == "revalidate":
            authority = authority_from_expected(
                release_mode=_required(
                    environment,
                    "INPUT_RELEASE_MODE",
                ),
                release_version=_required(
                    environment,
                    "INPUT_RELEASE_VERSION",
                ),
                release_source_sha=_required(
                    environment,
                    "INPUT_RELEASE_SOURCE_SHA",
                ),
                tag_object_sha=_required(
                    environment,
                    "INPUT_EXPECTED_TAG_OBJECT_SHA",
                ),
                tag_commit_sha=_required(
                    environment,
                    "INPUT_EXPECTED_TAG_COMMIT_SHA",
                ),
            )
            authority = revalidate_release_authority(
                authority,
                event,
                provider,
            )
        else:
            raise ReleaseTagError("unknown_release_authority_phase")
        output_path = Path(_required(environment, "GITHUB_OUTPUT"))
        write_outputs(output_path, authority)
    except ReleaseTagError as error:
        print(
            f"release tag authority rejected: {error.code}",
            file=sys.stderr,
        )
        return 2
    print(
        "release tag authority accepted: "
        f"mode={authority.release_mode} "
        f"version={authority.release_version} "
        f"source={authority.release_source_sha}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
