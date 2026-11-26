#!/usr/bin/env python3
"""Central execution class of a macOS CI request, resolved from the repository being built.

The class is chosen by Central only: a reviewed repository allow-list maps a repository to a
semantic class, and the class maps to fixed runner labels. No workflow input (request options,
target, ref) can select a class or a label; the caller only names the repository.

* ``macos-high-capacity``: the owner's persistent high-performance self-hosted Mac
  (``[self-hosted, macOS, ARM64, macos-latest-xl]``), for the complete Streamscape Media library
  workload (owner rule, codex-skills ``cards/media/product.md``).
* ``macos-hosted``: GitHub-hosted ``macos-latest``, every other repository.

``plan`` writes ``execution_class=<class>`` to ``$GITHUB_OUTPUT``; ``check`` fails when a stage
job was handed a class that is not the one Central resolves for its repository.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

HOSTED = "macos-hosted"
HIGH_CAPACITY = "macos-high-capacity"

# Reviewed class -> exact runs-on labels. The stage workflows hold the same mapping in one fixed
# expression (a test keeps both equal); raw labels never come from a caller.
RUNS_ON = {
    HOSTED: ["macos-latest"],
    HIGH_CAPACITY: ["self-hosted", "macOS", "ARM64", "macos-latest-xl"],
}

# Reviewed repository allow-list. Every repository not listed keeps the hosted class.
REPOSITORY_CLASS = {
    "StreamScapeTV/streamscape-media": HIGH_CAPACITY,
}


def resolve(repository: str) -> str:
    return REPOSITORY_CLASS.get(repository, HOSTED)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    plan = sub.add_parser("plan", help="write execution_class for the repository to $GITHUB_OUTPUT")
    plan.add_argument("--repository", required=True)
    check = sub.add_parser("check", help="fail unless the class is the one Central resolves for the repository")
    check.add_argument("--repository", required=True)
    check.add_argument("--execution-class", required=True)
    args = parser.parse_args(argv)

    expected = resolve(args.repository)
    if args.command == "plan":
        line = f"execution_class={expected}\n"
        output = os.environ.get("GITHUB_OUTPUT")
        if output:
            with open(output, "a", encoding="utf-8") as handle:
                handle.write(line)
        print(f"execution class {expected} runs-on {json.dumps(RUNS_ON[expected])}")
        return 0
    if args.execution_class not in RUNS_ON:
        print(f"unknown execution class {args.execution_class!r}", file=sys.stderr)
        return 2
    if args.execution_class != expected:
        print(f"execution class {args.execution_class} is not the class Central resolves for this repository ({expected})",
              file=sys.stderr)
        return 1
    print(f"execution class {expected} matches the Central allow-list")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
