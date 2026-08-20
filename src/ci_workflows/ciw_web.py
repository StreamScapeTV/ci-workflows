"""Bounded product-neutral static-web build and verification adapter."""
from __future__ import annotations

import argparse
import json
import os
import re
import stat
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

from .ciw_types import CIWContext, CIWResult, input_value, project_error, write_command_file
from .runtime_primitives import (
    ProcessResult,
    RuntimePrimitiveError,
    finalize_temporary_path,
    run_process,
)
from .web_primitives import (
    ProcessOutcome,
    WebPrimitiveError,
    inspect_static_output,
    run_static_verification,
)

_EXACT_SHA = re.compile(r"^[0-9a-f]{40}$")
_ERROR_CODE = re.compile(r"^[a-z][a-z0-9_]{2,95}$")
_SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b(token|password|authorization|secret|api[_-]?key)\s*[:=]\s*\S+"
)
_MAX_PLAN_BYTES = 16 * 1024
_MAX_ARGUMENTS = 32
_MAX_EXPECTED_FILES = 256
_MAX_ARGUMENT_BYTES = 2048
_MAX_DIAGNOSTIC_BYTES = 16 * 1024
_ALLOWED_PLAN_KEYS = {
    "build_script_path",
    "build_arguments",
    "static_output_directory",
    "expected_files",
    "verification_script_path",
    "verification_arguments",
    "build_timeout_seconds",
    "verification_timeout_seconds",
}
_REQUIRED_PLAN_KEYS = {"build_script_path", "static_output_directory"}
_SAFE_ENVIRONMENT = (
    "PATH",
    "HOME",
    "LANG",
    "LC_ALL",
    "TZ",
    "TMPDIR",
    "CI",
    "GITHUB_ACTIONS",
    "GITHUB_REPOSITORY",
    "GITHUB_REF",
    "GITHUB_EVENT_NAME",
    "RUNNER_OS",
    "RUNNER_ARCH",
)


class StaticWebValidationError(RuntimeError):
    """Stable static-web adapter failure with optional cleanup detail."""

    def __init__(self, code: str, *, cleanup_code: str = "") -> None:
        if _ERROR_CODE.fullmatch(code) is None:
            raise ValueError("static-web error code must be a safe identifier")
        if cleanup_code and _ERROR_CODE.fullmatch(cleanup_code) is None:
            raise ValueError("static-web cleanup code must be a safe identifier")
        self.code = code
        self.cleanup_code = cleanup_code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class StaticWebPlan:
    build_script_path: str
    build_arguments: tuple[str, ...]
    static_output_directory: str
    expected_files: tuple[str, ...]
    verification_script_path: str | None
    verification_arguments: tuple[str, ...]
    build_timeout_seconds: int
    verification_timeout_seconds: int


def configure_static_web_validate(_parser: argparse.ArgumentParser) -> None:
    """The reusable workflow supplies its bounded request through INPUT_* values."""


def _fail(code: str) -> None:
    raise StaticWebValidationError(code)


def _plain(value: object, *, code: str, maximum_bytes: int = _MAX_ARGUMENT_BYTES) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value.encode("utf-8")) > maximum_bytes
        or any(token in value for token in ("\x00", "\r", "\n"))
    ):
        _fail(code)
    return value


def _relative(value: object, *, code: str, allow_dot: bool = False) -> Path:
    text = _plain(value, code=code)
    if "\\" in text:
        _fail(code)
    if allow_dot and text == ".":
        return Path(".")
    path = Path(text)
    if path.is_absolute() or any(part in ("", ".", "..") for part in path.parts):
        _fail(code)
    return path


def _arguments(value: object, *, code: str) -> tuple[str, ...]:
    if not isinstance(value, list) or len(value) > _MAX_ARGUMENTS:
        _fail(code)
    return tuple(_plain(item, code=code) for item in value)


def _expected_files(value: object) -> tuple[str, ...]:
    if not isinstance(value, list) or len(value) > _MAX_EXPECTED_FILES:
        _fail("static_web_expected_files_invalid")
    result = tuple(
        _relative(item, code="static_web_expected_files_invalid").as_posix()
        for item in value
    )
    if len(result) != len(set(result)):
        _fail("static_web_expected_files_invalid")
    return result


def _timeout(value: object, *, code: str, default: int) -> int:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 3600:
        _fail(code)
    return value


def _parse_plan(environment: Mapping[str, str]) -> StaticWebPlan:
    raw = input_value(environment, "validation_plan_json")
    if not raw or len(raw.encode("utf-8")) > _MAX_PLAN_BYTES:
        _fail("static_web_plan_invalid")
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as error:
        raise StaticWebValidationError("static_web_plan_invalid") from error
    if not isinstance(value, dict):
        _fail("static_web_plan_invalid")
    if not _REQUIRED_PLAN_KEYS <= set(value) or not set(value) <= _ALLOWED_PLAN_KEYS:
        _fail("static_web_plan_invalid")

    build_script = _relative(
        value.get("build_script_path"),
        code="static_web_build_script_invalid",
    ).as_posix()
    output_directory = _relative(
        value.get("static_output_directory"),
        code="static_web_output_directory_invalid",
    ).as_posix()
    verifier_raw = value.get("verification_script_path")
    verifier = None
    if verifier_raw is not None:
        verifier = _relative(
            verifier_raw,
            code="static_web_verification_script_invalid",
        ).as_posix()

    return StaticWebPlan(
        build_script_path=build_script,
        build_arguments=_arguments(
            value.get("build_arguments", []),
            code="static_web_build_arguments_invalid",
        ),
        static_output_directory=output_directory,
        expected_files=_expected_files(value.get("expected_files", [])),
        verification_script_path=verifier,
        verification_arguments=_arguments(
            value.get("verification_arguments", []),
            code="static_web_verification_arguments_invalid",
        ),
        build_timeout_seconds=_timeout(
            value.get("build_timeout_seconds"),
            code="static_web_build_timeout_invalid",
            default=1200,
        ),
        verification_timeout_seconds=_timeout(
            value.get("verification_timeout_seconds"),
            code="static_web_verification_timeout_invalid",
            default=600,
        ),
    )


def _workspace(environment: Mapping[str, str]) -> Path:
    raw = environment.get("GITHUB_WORKSPACE", "")
    if not raw:
        _fail("static_web_workspace_invalid")
    candidate = Path(raw)
    if not candidate.is_absolute() or candidate.is_symlink() or not candidate.is_dir():
        _fail("static_web_workspace_invalid")
    try:
        return candidate.resolve(strict=True)
    except OSError as error:
        raise StaticWebValidationError("static_web_workspace_invalid") from error


def _bounded_directory(workspace: Path, value: str) -> Path:
    relative = _relative(
        value,
        code="static_web_working_directory_invalid",
        allow_dot=True,
    )
    cursor = workspace
    for part in relative.parts:
        if part == ".":
            continue
        cursor = cursor / part
        if cursor.is_symlink():
            _fail("static_web_working_directory_invalid")
    try:
        resolved = (workspace / relative).resolve(strict=True)
    except OSError as error:
        raise StaticWebValidationError("static_web_working_directory_invalid") from error
    if not resolved.is_dir() or (resolved != workspace and workspace not in resolved.parents):
        _fail("static_web_working_directory_invalid")
    return resolved


def _bounded_executable(root: Path, value: str, *, code: str) -> Path:
    relative = _relative(value, code=code)
    cursor = root
    for part in relative.parts[:-1]:
        cursor = cursor / part
        if cursor.is_symlink():
            _fail(code)
    candidate = root / relative
    if candidate.is_symlink():
        _fail(code)
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as error:
        raise StaticWebValidationError(code) from error
    if not resolved.is_file() or root not in resolved.parents or not os.access(resolved, os.X_OK):
        _fail(code)
    return resolved


def _output_candidate(root: Path, value: str) -> Path:
    relative = _relative(value, code="static_web_output_directory_invalid")
    candidate = root / relative
    cursor = root
    for part in relative.parts[:-1]:
        cursor = cursor / part
        if cursor.is_symlink():
            _fail("static_web_output_directory_invalid")
        if not cursor.exists():
            break
    if candidate.exists() or candidate.is_symlink():
        _fail("static_web_output_preexisting")
    return candidate


def _selected_environment(
    environment: Mapping[str, str],
    *,
    admitted_sha: str,
    output: Path,
    output_relative: str,
) -> dict[str, str]:
    result: dict[str, str] = {}
    for name in _SAFE_ENVIRONMENT:
        value = environment.get(name, "")
        if isinstance(value, str) and value and "\x00" not in value:
            result[name] = value
    if not result.get("PATH"):
        _fail("static_web_runtime_environment_invalid")
    result.update(
        {
            "CIW_ADMITTED_SHA": admitted_sha,
            "CIW_STATIC_OUTPUT_DIRECTORY": str(output),
            "CIW_STATIC_OUTPUT_RELATIVE": output_relative,
        }
    )
    return result


def _sanitize_diagnostic(text: str, *, root: Path) -> str:
    rendered = text.replace(str(root), "<project>")
    rendered = _SECRET_ASSIGNMENT.sub(
        lambda match: f"{match.group(1)}=<redacted>",
        rendered,
    )
    encoded = rendered.encode("utf-8", errors="replace")
    if len(encoded) > _MAX_DIAGNOSTIC_BYTES:
        rendered = encoded[-_MAX_DIAGNOSTIC_BYTES:].decode("utf-8", errors="replace")
    return rendered


def _emit_build_diagnostic(
    context: CIWContext,
    outcome: ProcessResult,
    *,
    root: Path,
) -> None:
    raw = "\n".join(part for part in (outcome.stdout, outcome.stderr) if part)
    if not raw:
        return
    context.stderr.write("static-web-build-diagnostic-begin\n")
    context.stderr.write(_sanitize_diagnostic(raw, root=root))
    if not raw.endswith("\n"):
        context.stderr.write("\n")
    context.stderr.write("static-web-build-diagnostic-end\n")


class _RuntimeVerificationRunner:
    def run(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        env: Mapping[str, str],
        timeout_seconds: int,
    ) -> ProcessOutcome:
        result = run_process(
            tuple(argv),
            cwd=cwd,
            environment=env,
            timeout_seconds=timeout_seconds,
        )
        return ProcessOutcome(
            124 if result.timed_out else int(result.returncode or 0),
            result.stdout,
            result.stderr,
        )


def _verify_expected_files(output: Path, expected: Sequence[str]) -> None:
    for value in expected:
        candidate = output / Path(value)
        try:
            metadata = candidate.lstat()
        except OSError as error:
            raise StaticWebValidationError("static_web_expected_file_missing") from error
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
            _fail("static_web_expected_file_missing")


def _summary(
    *,
    status: str,
    build_result: str,
    output_digest: str = "",
    output_file_count: int = 0,
    expected_file_count: int = 0,
    verification_result: str = "skipped",
) -> str:
    return json.dumps(
        {
            "build_result": build_result,
            "expected_file_count": expected_file_count,
            "output_digest": output_digest,
            "output_file_count": output_file_count,
            "status": status,
            "verification_result": verification_result,
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def _failure_outputs(
    context: CIWContext,
    *,
    code: str,
    cleanup_result: str,
    cleanup_code: str,
    build_result: str,
) -> None:
    output_path = context.environment.get("GITHUB_OUTPUT", "")
    if not output_path:
        return
    write_command_file(
        Path(output_path),
        {
            "result": "failure",
            "build_result": build_result,
            "output_verified": "false",
            "output_digest": "",
            "output_file_count": "0",
            "test_summary": _summary(status="failed", build_result=build_result),
            "cleanup_result": cleanup_result,
            "failure_code": code,
            "cleanup_code": cleanup_code,
        },
    )


def _adapt_error(error: BaseException) -> StaticWebValidationError:
    if isinstance(error, StaticWebValidationError):
        return error
    if isinstance(error, (WebPrimitiveError, RuntimePrimitiveError)):
        return StaticWebValidationError(error.code)
    return StaticWebValidationError("static_web_unexpected_failure")


def execute_static_web_validate(
    _args: argparse.Namespace,
    context: CIWContext,
) -> CIWResult:
    """Build, inspect and optionally verify one caller-owned static web export."""

    plan: StaticWebPlan | None = None
    working_directory: Path | None = None
    output: Path | None = None
    output_owned = False
    primary: StaticWebValidationError | None = None
    build_result = "not-run"
    cleanup_result = "success"
    cleanup_code = ""
    manifest = None
    verification_result = "skipped"

    try:
        plan = _parse_plan(context.environment)
        admitted_sha = input_value(context.environment, "admitted_sha")
        if _EXACT_SHA.fullmatch(admitted_sha) is None:
            _fail("static_web_source_sha_invalid")
        working_directory = _bounded_directory(
            _workspace(context.environment),
            input_value(context.environment, "working_directory", "."),
        )
        build_script = _bounded_executable(
            working_directory,
            plan.build_script_path,
            code="static_web_build_script_invalid",
        )
        verifier = None
        if plan.verification_script_path is not None:
            verifier = _bounded_executable(
                working_directory,
                plan.verification_script_path,
                code="static_web_verification_script_invalid",
            )
        output = _output_candidate(working_directory, plan.static_output_directory)
        environment = _selected_environment(
            context.environment,
            admitted_sha=admitted_sha,
            output=output,
            output_relative=plan.static_output_directory,
        )
        output_owned = True
        try:
            outcome = run_process(
                (str(build_script), *plan.build_arguments),
                cwd=working_directory,
                environment=environment,
                timeout_seconds=plan.build_timeout_seconds,
            )
        except RuntimePrimitiveError as error:
            raise StaticWebValidationError("static_web_build_start_failed") from error
        if outcome.timed_out:
            build_result = "timeout"
            _emit_build_diagnostic(context, outcome, root=working_directory)
            _fail("static_web_build_timeout")
        if not outcome.ok:
            build_result = "failure"
            _emit_build_diagnostic(context, outcome, root=working_directory)
            _fail("static_web_build_failed")
        build_result = "success"

        if not output.exists() and not output.is_symlink():
            _fail("static_web_output_missing")
        try:
            manifest = inspect_static_output(output)
        except WebPrimitiveError as error:
            raise StaticWebValidationError(error.code) from error
        _verify_expected_files(output, plan.expected_files)

        if verifier is not None:
            try:
                run_static_verification(
                    output,
                    (str(verifier), *plan.verification_arguments),
                    _RuntimeVerificationRunner(),
                    environment=environment,
                    timeout_seconds=plan.verification_timeout_seconds,
                )
            except WebPrimitiveError as error:
                raise StaticWebValidationError(error.code) from error
            verification_result = "success"
    except BaseException as error:
        if isinstance(error, (KeyboardInterrupt, SystemExit)):
            raise
        primary = _adapt_error(error)
    finally:
        if output_owned and output is not None and working_directory is not None:
            try:
                finalize_temporary_path(output, root=working_directory)
            except (RuntimePrimitiveError, OSError):
                cleanup_result = "failure"
                cleanup_code = "static_web_cleanup_failed"
                if primary is None:
                    primary = StaticWebValidationError(cleanup_code)

    if primary is not None:
        if cleanup_code and not primary.cleanup_code:
            primary.cleanup_code = cleanup_code
        _failure_outputs(
            context,
            code=primary.code,
            cleanup_result=cleanup_result,
            cleanup_code=cleanup_code,
            build_result=build_result,
        )
        raise primary

    if plan is None or manifest is None:
        _fail("static_web_unexpected_failure")
    outputs = {
        "result": "success",
        "build_result": build_result,
        "output_verified": "true",
        "output_digest": manifest.sha256,
        "output_file_count": str(manifest.file_count),
        "test_summary": _summary(
            status="success",
            build_result=build_result,
            output_digest=manifest.sha256,
            output_file_count=manifest.file_count,
            expected_file_count=len(plan.expected_files),
            verification_result=verification_result,
        ),
        "cleanup_result": cleanup_result,
        "failure_code": "",
        "cleanup_code": "",
    }
    return CIWResult("static-web", "validate", outputs=outputs)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ciw-static-web")
    parser.add_argument("--root", type=Path, required=True)
    parsed = parser.parse_args(argv)
    context = CIWContext(
        root=parsed.root.resolve(),
        environment=dict(os.environ),
        stdout=sys.stdout,
        stderr=sys.stderr,
    )
    try:
        result = execute_static_web_validate(argparse.Namespace(), context)
        result.emit(context)
    except Exception as error:
        projected = project_error(error, domain="static-web")
        print(f"static-web validation failed: {projected.code}", file=sys.stderr)
        return projected.exit_code
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
