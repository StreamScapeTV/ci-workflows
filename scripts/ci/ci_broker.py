#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from ci_workflows.ci_broker import BrokerConfig, BrokerError, self_check, serve  # noqa: E402
from ci_workflows.ci_broker_action import (  # noqa: E402
    BrokerActionError,
    cancel_if_active,
    cleanup,
    execute_apple_host,
)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Central CI broker runtime")
    result.add_argument(
        "command",
        choices=("server", "self-check", "execute-apple-host", "cancel-if-active", "cleanup"),
    )
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "server":
            serve(BrokerConfig.from_environment())
            return 0
        if args.command == "self-check":
            value = self_check()
            print(json.dumps(value, sort_keys=True, separators=(",", ":")))
            return 0
        if args.command == "execute-apple-host":
            execute_apple_host()
            print("Central broker-dispatched validation passed.")
            return 0
        if args.command == "cancel-if-active":
            cancel_if_active()
            return 0
        if args.command == "cleanup":
            cleanup()
            return 0
    except (BrokerError, BrokerActionError) as error:
        print(error.code, file=sys.stderr)
        return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
