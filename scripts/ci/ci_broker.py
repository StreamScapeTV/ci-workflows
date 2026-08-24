#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import sys
from typing import Any, Mapping
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from ci_workflows.ci_broker import BrokerConfig, BrokerError  # noqa: E402
from ci_workflows.ci_broker_action import (  # noqa: E402
    BrokerActionError,
    _broker_url,
    _canonical,
    _request_oidc_token,
    cancel_if_active,
    cleanup,
)
from ci_workflows.ci_broker_dependencies import (  # noqa: E402
    execute_apple_host,
    self_check,
)
from ci_workflows.ci_broker_fallback import fail_if_active  # noqa: E402
from ci_workflows.ci_broker_start_guard import serve  # noqa: E402
from ci_workflows.ci_callback_http import central_urlopen  # noqa: E402

_DIAGNOSTIC_BYTES = 4096
_SAFE_REMOTE_CODE = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")


def _safe_remote_code(raw: bytes) -> str | None:
    if len(raw) > _DIAGNOSTIC_BYTES:
        return None
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(value, dict):
        return None
    code = value.get("code")
    if not isinstance(code, str) or _SAFE_REMOTE_CODE.fullmatch(code) is None:
        return None
    return code


def _diagnose_broker_callback(
    environment: Mapping[str, str] = os.environ,
    opener: Any = central_urlopen,
) -> str | None:
    dispatch_id = environment.get("CI_DISPATCH_ID", "")
    dispatch_token = environment.get("CI_DISPATCH_TOKEN", "")
    if not dispatch_id or not dispatch_token:
        return None
    try:
        oidc = _request_oidc_token(environment, opener)
        request = urllib.request.Request(
            _broker_url(environment) + "/actions/route",
            data=_canonical(
                {"dispatch_id": dispatch_id, "dispatch_token": dispatch_token}
            ),
            method="POST",
            headers={
                "Authorization": f"Bearer {oidc}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        try:
            with opener(request, timeout=30) as response:
                raw = response.read(_DIAGNOSTIC_BYTES + 1)
                status = int(getattr(response, "status", response.getcode()))
        except urllib.error.HTTPError as error:
            raw = error.read(_DIAGNOSTIC_BYTES + 1)
            code = _safe_remote_code(raw)
            if code is not None:
                return f"broker_rejection_code={code}"
            return f"broker_route_probe=http_{int(error.code)}_no_broker_code"
        if status == 200 and len(raw) <= _DIAGNOSTIC_BYTES:
            return "broker_route_probe=accepted"
        return f"broker_route_probe=http_{status}"
    except (BrokerActionError, OSError, urllib.error.URLError, ValueError, TypeError):
        return None


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Central CI broker runtime")
    result.add_argument(
        "command",
        choices=(
            "server",
            "self-check",
            "execute-apple-host",
            "fail-if-active",
            "cancel-if-active",
            "cleanup",
        ),
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
            execute_apple_host(opener=central_urlopen)
            print("Central broker-dispatched validation passed.")
            return 0
        if args.command == "fail-if-active":
            fail_if_active(opener=central_urlopen)
            return 0
        if args.command == "cancel-if-active":
            cancel_if_active(opener=central_urlopen)
            return 0
        if args.command == "cleanup":
            cleanup()
            return 0
    except (BrokerError, BrokerActionError) as error:
        print(error.code, file=sys.stderr)
        if isinstance(error, BrokerActionError) and error.code.startswith("broker_http_"):
            diagnostic = _diagnose_broker_callback()
            if diagnostic is not None:
                print(diagnostic, file=sys.stderr)
        return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
