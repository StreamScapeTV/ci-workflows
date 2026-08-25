#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from ci_workflows.ci_broker import BrokerError  # noqa: E402
from ci_workflows.ci_relay import RelayConfig  # noqa: E402
from ci_workflows.ci_relay_server import self_check, serve  # noqa: E402


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Thin Central CI webhook relay")
    result.add_argument("command", choices=("server", "self-check"))
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "server":
            serve(RelayConfig.from_environment())
            return 0
        value = self_check()
        print(json.dumps(value, sort_keys=True, separators=(",", ":")))
        return 0
    except BrokerError as error:
        print(error.code, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
