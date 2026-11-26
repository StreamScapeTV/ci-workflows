#!/usr/bin/env python3
"""Keep the user keychain context of a persistent macOS runner as the job found it.

A GitHub-hosted Mac is thrown away after the job; the high-capacity Mac is not. A repository
script (for example ``.ci/release.sh``) may create a keychain, put it on the user search list or
make it the default.

* ``snapshot`` (before the source setup) records the user search list and default keychain in a
  per-runner state file in the native home (it must outlive ``RUNNER_TEMP``, which the runner wipes).
  If that file still exists, the previous job on this runner was killed before its cleanup: its
  saved context is restored and its keychains deleted first (stale-state recovery).
* ``restore`` (``always()`` cleanup) deletes every keychain the job added to the search list or made
  the default, wherever it lives, puts the search list and default keychain back exactly as recorded
  and removes the state file. Without a state file it does nothing.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import pwd
import re
import subprocess
import sys

STATE_DIR = "Library/Application Support/central-ci"


def _security(*args: str) -> str:
    return subprocess.run(["security", *args], check=True, capture_output=True, text=True, timeout=60).stdout


def parse(output: str) -> list:
    return [line.strip().strip('"') for line in output.splitlines() if line.strip().strip('"')]


def state_path(home: Path, runner_name: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", runner_name or "runner")[:100]
    return home / STATE_DIR / f"keychain-state-{safe}.json"


def restore(state: Path, call=_security) -> list:
    """Restore the recorded context; return the keychains the job had added (all of them are deleted)."""
    if not state.exists():
        return []
    if state.is_symlink() or not state.is_file():
        raise RuntimeError("keychain state must be a regular file")
    record = json.loads(state.read_text(encoding="utf-8"))
    before = list(record["search"])
    current = parse(call("list-keychains", "-d", "user"))
    current_default = (parse(call("default-keychain", "-d", "user")) or [""])[0]
    kept = set(before) | {record["default"]}  # never delete a keychain that was there before the job
    added = [path for path in current if path not in kept]
    if current_default and current_default not in kept and current_default not in added:
        added.append(current_default)
    # Put the recorded context back first, so no deletion can leave the user without a default.
    call("list-keychains", "-d", "user", "-s", *before)
    if record["default"]:
        call("default-keychain", "-d", "user", "-s", record["default"])
    for path in added:
        if os.path.lexists(path):
            call("delete-keychain", path)
    if parse(call("list-keychains", "-d", "user")) != before:
        raise RuntimeError("user keychain search list was not restored")
    state.unlink()
    return added


def snapshot(state: Path, call=_security) -> dict:
    """Recover a killed job's leftovers, then record the current context. Returns {'record', 'recovered'}."""
    recovered = restore(state, call=call)
    state.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    record = {"search": parse(call("list-keychains", "-d", "user")),
              "default": (parse(call("default-keychain", "-d", "user")) or [""])[0]}
    with state.open("x", encoding="utf-8") as handle:
        json.dump(record, handle)
    os.chmod(state, 0o600)
    return {"record": record, "recovered": recovered}


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    state = state_path(Path(pwd.getpwuid(os.getuid()).pw_dir), os.environ.get("RUNNER_NAME", ""))
    if argv == ["snapshot"]:
        result = snapshot(state)
        if result["recovered"]:
            print(f"recovered a killed job's keychain context; deleted {len(result['recovered'])} keychain(s)")
        print("user keychain context recorded")
        return 0
    if argv == ["restore"]:
        added = restore(state)
        print(f"user keychain context restored; deleted {len(added)} keychain(s) the job added")
        return 0
    raise SystemExit("expected snapshot or restore")


if __name__ == "__main__":
    raise SystemExit(main())
