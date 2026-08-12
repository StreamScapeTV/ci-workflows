#!/usr/bin/env python3
"""Repository entry point for trusted OCI publication operations."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Mapping, Sequence

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from ci_workflows.oci_publish_contract import (  # noqa: E402
    OciPublishError,
    authenticate,
    cleanup,
    read_back,
    request_from_environment,
    residue,
    resolve_plan,
    publish,
    verify,
)


def _write_outputs(values: Mapping[str, str], environment: Mapping[str, str]) -> None:
    path = environment.get("GITHUB_OUTPUT")
    if not path:
        print(json.dumps(dict(values), sort_keys=True))
        return
    with Path(path).open("a", encoding="utf-8") as stream:
        for key, value in values.items():
            if "\n" in value or "\r" in value:
                raise OciPublishError("invalid_output")
            stream.write(f"{key}={value}\n")


def main(argv: Sequence[str] | None = None, environment: Mapping[str, str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", choices=("plan", "authenticate", "publish", "readback", "verify", "cleanup", "residue"), required=True)
    args = parser.parse_args(argv)
    env = dict(os.environ if environment is None else environment)
    try:
        if args.phase == "cleanup":
            cleanup(env)
            _write_outputs({"result": "success", "cleanup_result": "success", "failure_code": ""}, env)
            return 0
        if args.phase == "residue":
            residue(env)
            _write_outputs({"result": "success", "cleanup_result": "success", "failure_code": ""}, env)
            return 0
        request = request_from_environment(env)
        plan = resolve_plan(ROOT, request)
        if args.phase == "plan":
            outputs = plan.planning_outputs()
        elif args.phase == "authenticate":
            outputs = authenticate(plan, env, env.get("INPUT_REGISTRY_USERNAME", ""), env.get("INPUT_REGISTRY_TOKEN", ""))
        elif args.phase == "publish":
            outputs = publish(plan, env)
        elif args.phase == "readback":
            outputs = read_back(plan, env)
        else:
            outputs = verify(plan, env)
        _write_outputs(outputs, env)
        return 0
    except (OciPublishError, OSError) as error:
        code = error.code if isinstance(error, OciPublishError) else "invalid_request"
        try:
            _write_outputs({"result": "failure", "failure_code": code}, env)
        except (OciPublishError, OSError):
            pass
        print(f"OCI publication failed: {code}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
