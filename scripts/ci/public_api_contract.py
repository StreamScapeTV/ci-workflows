#!/usr/bin/env python3
"""Validate, render, and compare the StreamScapeTV public workflow API."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ci_workflows.public_api import (  # noqa: E402
    ContractError,
    compare_contracts,
    load_contract,
    read_text,
    render,
    require,
    validate,
)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--root", type=Path, default=Path.cwd())
    commands = result.add_subparsers(dest="command", required=True)
    commands.add_parser("validate")
    render_command = commands.add_parser("render")
    render_command.add_argument("--check", action="store_true")
    diff = commands.add_parser("diff")
    diff.add_argument("--baseline-root", type=Path, required=True)
    diff.add_argument("--current-root", type=Path, required=True)
    return result


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "validate":
            data = validate(args.root)
            print(
                f"validated {len(data.workflows)} public workflow APIs, "
                f"{len(data.permissions['profiles'])} permission profiles, and "
                f"{len(data.types['trust_classes'])} trust classes"
            )
        elif args.command == "render":
            data = validate(args.root)
            output = render(data)
            target = data.root / "docs/workflows/public-api-reference.md"
            if args.check:
                require(
                    target.exists() and read_text(target) == output,
                    "generated public API reference is stale",
                )
                print("public API reference is current")
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(output, encoding="utf-8")
                print(f"rendered {target}")
        else:
            baseline = load_contract(args.baseline_root)
            current = load_contract(args.current_root)
            changes = compare_contracts(baseline, current)
            print(json.dumps(changes, indent=2, sort_keys=True))
            if any(
                row["decision"] == "breaking-unacknowledged"
                for row in changes
            ):
                return 2
    except ContractError as error:
        print(f"public API contract error: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
