#!/usr/bin/env python3
"""Keep the user keychain context of a persistent macOS runner as the job found it.

A GitHub-hosted Mac is thrown away after the job; the high-capacity Mac is not. A repository
script (for example ``.ci/release.sh``) may create a keychain, put it on the user search list or
make it the default. ``snapshot`` (first step) records the user search list and default keychain
in the private per-run temp directory; ``restore`` (``always()``) deletes every keychain that was
added to the search list during the job and lives under the per-run temp directory or the job
workspace, then puts the search list and the default keychain back exactly as recorded. A keychain
added elsewhere is taken off the search list and reported, never deleted.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

STATE_NAME = "central-keychain-state.json"


def _security(*args: str) -> str:
    return subprocess.run(["security", *args], check=True, capture_output=True, text=True, timeout=60).stdout


def parse(output: str) -> list:
    return [line.strip().strip('"') for line in output.splitlines() if line.strip().strip('"')]


def _inside(path: str, roots: list) -> bool:
    resolved = os.path.realpath(path)
    for root in roots:
        base = os.path.realpath(root)
        if os.path.commonpath((resolved, base)) == base and resolved != base:
            return True
    return False


def snapshot(state: Path, call=_security) -> dict:
    record = {"search": parse(call("list-keychains", "-d", "user")),
              "default": (parse(call("default-keychain", "-d", "user")) or [""])[0]}
    with state.open("x", encoding="utf-8") as handle:
        json.dump(record, handle)
    os.chmod(state, 0o600)
    return record


def restore(state: Path, owned_roots: list, call=_security) -> list:
    """Return the keychains added during the job outside the owned roots (reported, not deleted)."""
    if not state.exists():
        return []
    record = json.loads(state.read_text(encoding="utf-8"))
    before = list(record["search"])
    added = [path for path in parse(call("list-keychains", "-d", "user")) if path not in before]
    foreign = []
    for path in added:
        if _inside(path, owned_roots):
            if os.path.lexists(path):
                call("delete-keychain", path)
        else:
            foreign.append(path)
    call("list-keychains", "-d", "user", "-s", *before)
    if record["default"]:
        call("default-keychain", "-d", "user", "-s", record["default"])
    if parse(call("list-keychains", "-d", "user")) != before:
        raise RuntimeError("user keychain search list was not restored")
    state.unlink()
    return foreign


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    temp = os.environ["RUNNER_TEMP"]
    state = Path(temp) / STATE_NAME
    if argv == ["snapshot"]:
        snapshot(state)
        print("user keychain context recorded")
        return 0
    if argv == ["restore"]:
        roots = [temp] + ([os.environ["GITHUB_WORKSPACE"]] if os.environ.get("GITHUB_WORKSPACE") else [])
        foreign = restore(state, roots)
        for _path in foreign:
            print("::warning::a keychain added during the job outside the run directories was taken off the search list",
                  file=sys.stderr)
        print("user keychain context restored")
        return 0
    raise SystemExit("expected snapshot or restore")


if __name__ == "__main__":
    raise SystemExit(main())
