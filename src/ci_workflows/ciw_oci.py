"""Thin CIW adapters for versioned OCI validation and publication contracts."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Mapping, Sequence

from . import oci
from . import oci_publish_contract as publication
from .ciw_types import CIWContext, CIWResult, write_command_file
from .oci_contract import (
    MAPPING_PATH,
    bounded_path,
    load_contract,
    render_engine_mapping,
    request_from_mapping,
    resolve_plan,
    safe_relative,
    validate_generated_mapping,
)
from .oci_execution_safe import cleanup, residue
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


def configure_oci_validate(parser: argparse.ArgumentParser) -> None:
    """Configure the bounded ``ciw oci validate`` command."""

    parser.add_argument(
        "--phase",
        choices=("plan", "execute", "cleanup", "residue"),
        default="execute",
    )
    parser.add_argument("--source-root", default="source")


def configure_oci_publish(parser: argparse.ArgumentParser) -> None:
    """Configure the bounded ``ciw oci publish`` command."""

    parser.add_argument(
        "--phase",
        choices=("plan", "authenticate", "publish", "readback", "verify", "cleanup", "residue"),
        default="plan",
    )


def _failure_outputs(context: CIWContext, error: BaseException) -> None:
    target = context.environment.get("GITHUB_OUTPUT", "")
    code = getattr(error, "code", "invalid_request")
    if not target or not isinstance(code, str):
        return
    write_command_file(
        Path(target),
        {
            "result": "failure",
            "failure_code": code,
        },
    )


def execute_oci_validate(
    args: argparse.Namespace,
    context: CIWContext,
) -> CIWResult:
    """Plan, execute, clean, or inspect one OCI validation request."""

    try:
        request = request_from_mapping(_request(context.environment), context.environment)
        plan = resolve_plan(context.root, request)
        if args.phase == "plan":
            validate_generated_mapping(context.root)
            return CIWResult("oci", "validate", outputs=plan.planning_outputs())
        if args.phase == "cleanup":
            cleanup(context.environment, plan.storage_driver)
            return CIWResult(
                "oci",
                "validate",
                outputs={
                    "result": "success",
                    "cleanup_result": "success",
                    "failure_code": "",
                },
            )
        if args.phase == "residue":
            residue(context.environment)
            return CIWResult(
                "oci",
                "validate",
                outputs={
                    "result": "success",
                    "cleanup_result": "success",
                    "failure_code": "",
                },
            )
        workspace = Path(context.environment.get("GITHUB_WORKSPACE", "."))
        source_root = bounded_path(
            workspace,
            safe_relative(args.source_root),
        )
        result = oci.build(context.root, source_root, plan, context.environment)
        return CIWResult("oci", "validate", outputs=result.output_values())
    except OciBuildError as error:
        _failure_outputs(context, error)
        raise


def execute_oci_publish(
    args: argparse.Namespace,
    context: CIWContext,
) -> CIWResult:
    """Plan, authenticate, publish/verify, read back, or clean one OCI release."""

    try:
        if args.phase == "cleanup":
            publication.cleanup(context.environment)
            return CIWResult(
                "oci",
                "publish",
                outputs={
                    "result": "success",
                    "cleanup_result": "success",
                    "failure_code": "",
                },
            )
        if args.phase == "residue":
            publication.residue(context.environment)
            return CIWResult(
                "oci",
                "publish",
                outputs={
                    "result": "success",
                    "cleanup_result": "success",
                    "failure_code": "",
                },
            )
        request = publication.request_from_environment(context.environment)
        plan = publication.resolve_plan(context.root, request)
        if args.phase == "plan":
            outputs = plan.planning_outputs()
        elif args.phase == "authenticate":
            outputs = publication.authenticate(
                plan,
                context.environment,
                context.environment.get("INPUT_REGISTRY_USERNAME", ""),
                context.environment.get("INPUT_REGISTRY_TOKEN", ""),
            )
        elif args.phase == "publish":
            outputs = oci.publish(plan, context.environment)
        elif args.phase == "readback":
            outputs = oci.read_back(plan, context.environment)
        else:
            outputs = publication.verify(plan, context.environment)
        return CIWResult("oci", "publish", outputs=outputs)
    except publication.OciPublishError as error:
        _failure_outputs(context, error)
        raise


def _phase(phase: str, source_root: Path, environment: Mapping[str, str]) -> int:
    request = request_from_mapping(_request(environment), environment)
    plan = resolve_plan(ROOT, request)
    if phase == "plan":
        validate_generated_mapping(ROOT)
        _write_outputs(plan.planning_outputs(), environment)
        return 0
    if phase == "execute":
        result = oci.build(ROOT, source_root, plan, environment)
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
