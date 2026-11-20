#!/usr/bin/env python3
"""Bounded GitHub concurrency lane for per-purpose CI workflows.

Semantics mirror the legacy dispatcher so supersession behaves the same: ordinary moving-branch
validation of one repository/ref supersedes older runs of the same lane; cheap ``lint``/``build``
runs have their own lane; tags and everything else serialize without cancelling. The key prefix
differs from the legacy lanes on purpose, so old and new workflows never cancel each other
while both exist.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re

ORDINARY = frozenset(("lint", "build", "test", "full", "ui-test"))
REPOSITORY = re.compile(r"[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}")
LANE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")


def lane(repository: str, ref: str, is_tag: str, workflow_file: str, profile: str):
    if REPOSITORY.fullmatch(repository) is None:
        raise SystemExit("lane repository is outside the reviewed bound")
    if not 1 <= len(ref) <= 255 or any(ord(c) < 32 or ord(c) == 127 for c in ref):
        raise SystemExit("lane ref is outside the reviewed bound")
    if LANE.fullmatch(workflow_file) is None or LANE.fullmatch(profile) is None:
        raise SystemExit("lane workflow/profile is outside the reviewed bound")
    if is_tag not in ("true", "false"):
        raise SystemExit("lane source kind is invalid")
    cancel = is_tag == "false" and profile in ORDINARY
    if cancel and profile in ("lint", "build"):
        material = ("ci-wf-lightweight-branch-v1", repository, ref, profile)
    elif cancel:
        material = ("ci-wf-ordinary-branch-v1", repository, ref)
    else:
        material = ("ci-wf-serial-v1", repository, ref, is_tag, workflow_file, profile)
    return hashlib.sha256("\0".join(material).encode()).hexdigest(), cancel


def main() -> int:
    key, cancel = lane(
        os.environ["SOURCE_REPOSITORY"], os.environ["SOURCE_REF"], os.environ["SOURCE_IS_TAG"],
        os.environ["WORKFLOW_FILE"], os.environ["TEST_PROFILE"],
    )
    with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as output:
        output.write(f"concurrency_key={key}\n")
        output.write(f"cancel_in_progress={'true' if cancel else 'false'}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
