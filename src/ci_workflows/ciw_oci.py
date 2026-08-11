"""Thin CLI adapter for the versioned OCI build contract."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Mapping, Sequence

from .oci_contract import (
    MAPPING_PATH,
    load_contract,
    render_engine_mapping,
    request_from_mapping,
    resolve_plan,
    validate_generated_mapping,
)
from .oci_execution_safe import cleanup, execute_plan, residue
from .oci_types import OciBuildError

ROOT = Path(__file__).resolve().parents[2]


def _request(environment: Mapping[str, str]) -> Mapping[str, object]:
    return {
        "repository": environment.get("GITHUB_REPOSITORY", ""),
        "admitted_sha": environment.get("INPUT_ADMITTED_SHA", ""),
        "product_id": environment.get("INPUT_PRODUCT_ID", ""),
        "release_version": environment.get("INPUT_RELEASE_VERSION") or None,
        "platform_set": environment.get("INPUT_PLATFORM_SET") or None,
        "artifact_exception_id": environment.get("INPUT_ARTIFACT_EXCEPTION_ID") or None,
    }


def _write_outputs(values: Mapping[str, str], environment: Mapping[str, str]) -> None:
    path = environment.get("GITHUB_OUTPUT")
    if not path:
        print(json.dumps(dict(values), sort_keys=True))
        return
    with Path(path).open("a", encoding="utf-8") as stream:
        for key, value in values.items():
            if "\n" in value or "\r" in value:
                raise OciBuildError("invalid_request")
            stream.write(f"{key}={value}\n")


def _phase(phase: str, source_root: Path, environment: Mapping[str, str]) -> int:
    request = request_from_mapping(_request(environment), environment)
    plan = resolve_plan(ROOT, request)
    if phase == "plan":
        validate_generated_mapping(ROOT)
        _write_outputs(plan.planning_outputs(), environment)
        return 0
    if phase == "execute":
        result = execute_plan(ROOT, source_root, plan, environment)
        _write_outputs(result.output_values(), environment)
        return 0
    if phase == "cleanup":
        cleanup(environment, plan.storage_driver)
        _write_outputs({"result": "success", "cleanup_result": "success", "failure_code": ""}, environment)
        return 0
    if phase == "residue":
        residue(environment)
        _write_outputs({"result": "success", "cleanup_result": "success", "failure_code": ""}, environment)
        return 0
    raise OciBuildError("invalid_request")


def generate_mapping(*, check: bool) -> int:
    contract = load_contract(ROOT)
    rendered = json.dumps(render_engine_mapping(contract), indent=2, sort_keys=True) + "\n"
    path = ROOT / MAPPING_PATH
    if check:
        if not path.exists() or path.read_text(encoding="utf-8") != rendered:
            raise OciBuildError("generated_mapping_stale")
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(rendered, encoding="utf-8")
    return 0


def main(argv: Sequence[str] | None = None, environment: Mapping[str, str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    validate = sub.add_parser("validate")
    validate.add_argument("--phase", choices=("plan", "execute", "cleanup", "residue"), required=True)
    validate.add_argument("--source-root", type=Path, default=Path("source"))
    mapping = sub.add_parser("mapping")
    mapping.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    env = dict(os.environ if environment is None else environment)
    try:
        if args.command == "mapping":
            return generate_mapping(check=args.check)
        return _phase(args.phase, args.source_root, env)
    except (OciBuildError, OSError) as error:
        code = error.code if isinstance(error, OciBuildError) else "invalid_request"
        try:
            _write_outputs({"result": "failure", "failure_code": code}, env)
        except (OciBuildError, OSError):
            pass
        print(f"OCI build failed: {code}", file=os.sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
