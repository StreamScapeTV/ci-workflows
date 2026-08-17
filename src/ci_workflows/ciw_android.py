"""Primitive-backed product-neutral ``ciw android validate`` adapter."""
from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Mapping

from . import android_execution
from .android_types import AndroidValidationError
from .ciw_types import CIWContext, CIWError, CIWResult, input_value, project_error, write_command_file
from .foundation_types import FoundationError
from .language_primitives import (
    LanguagePrimitiveError,
    android_targeted_test,
    inspect_java_runtime,
    resolve_java_executable,
    run_gradle_tasks,
    validate_java_runtime,
)
from .runtime_primitives import RuntimePrimitiveError, run_process
from .workspace import resolve_state_root

_DOMAIN = "android"
_SCOPES = (
    "protected-full",
    "compile",
    "unit",
    "assemble",
    "lint",
    "targeted-unit",
    "gradle",
    "script",
)
_FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
_REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_IDENTIFIER = re.compile(r"^[a-z][a-z0-9-]{1,63}$")
_GRADLE_TASK = re.compile(r"^:?[-A-Za-z0-9_.]+(?::[-A-Za-z0-9_.]+)*$")
_TEST_SELECTOR = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*(?:\.[A-Za-z_][A-Za-z0-9_$]*){2,31}$")
_COPY_RELATIVE = "tmp/android-source"
_MAX_PLAN_BYTES = 16 * 1024


@dataclass(frozen=True, slots=True)
class ScriptPlan:
    path: str
    arguments: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ProtectedFullPlan:
    unit_tasks: tuple[str, ...]
    lint_tasks: tuple[str, ...]
    assemble_tasks: tuple[str, ...]
    schema_mode: str
    schema_tasks: tuple[str, ...] = ()
    schema_script: ScriptPlan | None = None

    @property
    def gradle_tasks(self) -> tuple[str, ...]:
        return (
            *self.unit_tasks,
            *self.lint_tasks,
            *self.assemble_tasks,
            *self.schema_tasks,
        )


@dataclass(frozen=True, slots=True)
class AndroidPrimitiveRequest:
    admitted_sha: str
    validation_scope: str
    working_directory: str
    gradle_wrapper_path: str
    gradle_tasks: tuple[str, ...]
    targeted_test_selector: str
    script: ScriptPlan | None
    protected_full: ProtectedFullPlan | None
    private_dependency_repository: str
    private_dependency_sha: str
    private_dependency_subdirectory: str
    private_dependency_id: str

    @property
    def private_dependency_used(self) -> bool:
        return bool(self.private_dependency_repository)


def configure_android_validate(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--phase", choices=("plan", "execute", "cleanup", "residue"), default="execute")
    parser.add_argument("--source-root", default="source")
    for name in (
        "admitted-sha",
        "validation-scope",
        "working-directory",
        "gradle-wrapper-path",
        "validation-plan-json",
        "private-dependency-repository",
        "private-dependency-sha",
        "private-dependency-subdirectory",
        "private-dependency-id",
        "private-dependency-verified",
        "private-dependency-remotes-erased",
        "private-dependency-credentials-erased",
        "private-dependency-head-sha",
        "private-dependency-checkout-repository",
        "private-dependency-checkout-id",
        "private-dependency-expected-subpath",
    ):
        parser.add_argument(f"--{name}")


def _value(args: argparse.Namespace, context: CIWContext, name: str, default: str = "") -> str:
    value = getattr(args, name, None)
    if value is not None:
        return str(value).strip()
    return input_value(context.environment, name, default)


def _plain(value: object, code: str, *, allow_empty: bool = False, maximum: int = 4096) -> str:
    if (
        not isinstance(value, str)
        or len(value.encode("utf-8")) > maximum
        or any(character in value for character in ("\x00", "\r", "\n"))
        or (not allow_empty and not value)
    ):
        raise CIWError(_DOMAIN, code)
    return value


def _relative_path(value: object, code: str, *, allow_dot: bool) -> str:
    text = _plain(value, code, maximum=1024)
    if "\\" in text or text.startswith("/"):
        raise CIWError(_DOMAIN, code)
    if text == ".":
        if allow_dot:
            return "."
        raise CIWError(_DOMAIN, code)
    pure = PurePosixPath(text)
    if not pure.parts or ".." in pure.parts:
        raise CIWError(_DOMAIN, code)
    if any(part in {"", ".", ".."} for part in pure.parts):
        raise CIWError(_DOMAIN, code)
    return pure.as_posix()


def _json_object(raw: str, code: str) -> dict[str, object]:
    text = _plain(raw, code, maximum=_MAX_PLAN_BYTES)

    def pairs_hook(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if not isinstance(key, str) or key in result:
                raise CIWError(_DOMAIN, code)
            result[key] = value
        return result

    try:
        value = json.loads(text, object_pairs_hook=pairs_hook)
    except json.JSONDecodeError as error:
        raise CIWError(_DOMAIN, code) from error
    if not isinstance(value, dict):
        raise CIWError(_DOMAIN, code)
    return value


def _exact_keys(value: Mapping[str, object], keys: set[str], code: str) -> None:
    if set(value) != keys:
        raise CIWError(_DOMAIN, code)


def _strings(
    value: object,
    code: str,
    *,
    maximum_items: int,
    allow_empty: bool = False,
    maximum_item_bytes: int = 2048,
) -> tuple[str, ...]:
    if not isinstance(value, list) or len(value) > maximum_items or (not allow_empty and not value):
        raise CIWError(_DOMAIN, code)
    return tuple(
        _plain(item, code, maximum=maximum_item_bytes)
        for item in value
    )


def _tasks(value: object, code: str, *, maximum_items: int = 32) -> tuple[str, ...]:
    tasks = _strings(value, code, maximum_items=maximum_items)
    if (
        any(_GRADLE_TASK.fullmatch(task) is None or task.startswith("-") for task in tasks)
        or len(set(tasks)) != len(tasks)
    ):
        raise CIWError(_DOMAIN, code)
    return tasks


def _script_plan(value: Mapping[str, object], code: str) -> ScriptPlan:
    _exact_keys(value, {"path", "arguments"}, code)
    path = _relative_path(value["path"], code, allow_dot=False)
    arguments = _strings(
        value["arguments"],
        code,
        maximum_items=64,
        allow_empty=True,
    )
    return ScriptPlan(path, arguments)


def _protected_full_plan(value: Mapping[str, object], code: str) -> ProtectedFullPlan:
    _exact_keys(value, {"unit_tasks", "lint_tasks", "assemble_tasks", "schema"}, code)
    unit_tasks = _tasks(value["unit_tasks"], code, maximum_items=16)
    lint_tasks = _tasks(value["lint_tasks"], code, maximum_items=16)
    assemble_tasks = _tasks(value["assemble_tasks"], code, maximum_items=16)
    schema = value["schema"]
    if not isinstance(schema, dict):
        raise CIWError(_DOMAIN, code)
    mode = schema.get("mode")
    schema_tasks: tuple[str, ...] = ()
    schema_script: ScriptPlan | None = None
    if mode == "none":
        _exact_keys(schema, {"mode"}, code)
    elif mode == "gradle":
        _exact_keys(schema, {"mode", "tasks"}, code)
        schema_tasks = _tasks(schema["tasks"], code, maximum_items=16)
    elif mode == "script":
        _exact_keys(schema, {"mode", "path", "arguments"}, code)
        schema_script = _script_plan(
            {"path": schema["path"], "arguments": schema["arguments"]},
            code,
        )
    else:
        raise CIWError(_DOMAIN, code)
    combined = (*unit_tasks, *lint_tasks, *assemble_tasks, *schema_tasks)
    if len(set(combined)) != len(combined):
        raise CIWError(_DOMAIN, code)
    return ProtectedFullPlan(
        unit_tasks,
        lint_tasks,
        assemble_tasks,
        str(mode),
        schema_tasks,
        schema_script,
    )


def _validation_plan(
    scope: str,
    raw: str,
) -> tuple[tuple[str, ...], str, ScriptPlan | None, ProtectedFullPlan | None]:
    code = "validation_plan_invalid"
    value = _json_object(raw, code)
    if scope == "protected-full":
        return (), "", None, _protected_full_plan(value, code)
    if scope in {"compile", "unit", "assemble", "lint", "gradle"}:
        _exact_keys(value, {"tasks"}, code)
        return _tasks(value["tasks"], code), "", None, None
    if scope == "targeted-unit":
        _exact_keys(value, {"tasks", "test_selector"}, code)
        tasks = _tasks(value["tasks"], code, maximum_items=1)
        selector = _plain(value["test_selector"], code, maximum=1024)
        if _TEST_SELECTOR.fullmatch(selector) is None:
            raise CIWError(_DOMAIN, code)
        return tasks, selector, None, None
    if scope == "script":
        return (), "", _script_plan(value, code), None
    raise CIWError(_DOMAIN, "validation_scope_invalid")


def _request(args: argparse.Namespace, context: CIWContext) -> AndroidPrimitiveRequest:
    admitted_sha = _value(args, context, "admitted_sha")
    if _FULL_SHA.fullmatch(admitted_sha) is None:
        raise CIWError(_DOMAIN, "admitted_sha_invalid")
    scope = _value(args, context, "validation_scope")
    if scope not in _SCOPES:
        raise CIWError(_DOMAIN, "validation_scope_invalid")
    working_directory = _relative_path(
        _value(args, context, "working_directory", "."),
        "working_directory_invalid",
        allow_dot=True,
    )
    gradle_wrapper_path = _relative_path(
        _value(args, context, "gradle_wrapper_path", "gradlew"),
        "gradle_wrapper_path_invalid",
        allow_dot=False,
    )
    tasks, selector, script, protected_full = _validation_plan(
        scope,
        _value(args, context, "validation_plan_json"),
    )

    dependency_repository = _plain(
        _value(args, context, "private_dependency_repository"),
        "private_dependency_invalid",
        allow_empty=True,
        maximum=255,
    )
    dependency_sha = _plain(
        _value(args, context, "private_dependency_sha"),
        "private_dependency_invalid",
        allow_empty=True,
        maximum=40,
    )
    dependency_id = _plain(
        _value(args, context, "private_dependency_id"),
        "private_dependency_invalid",
        allow_empty=True,
        maximum=64,
    )
    dependency_fields = (dependency_repository, dependency_sha, dependency_id)
    if any(dependency_fields):
        if (
            not all(dependency_fields)
            or _REPOSITORY.fullmatch(dependency_repository) is None
            or _FULL_SHA.fullmatch(dependency_sha) is None
            or _IDENTIFIER.fullmatch(dependency_id) is None
        ):
            raise CIWError(_DOMAIN, "private_dependency_invalid")
    dependency_subdirectory = _relative_path(
        _value(args, context, "private_dependency_subdirectory", "."),
        "private_dependency_subdirectory_invalid",
        allow_dot=True,
    )
    return AndroidPrimitiveRequest(
        admitted_sha,
        scope,
        working_directory,
        gradle_wrapper_path,
        tasks,
        selector,
        script,
        protected_full,
        dependency_repository,
        dependency_sha,
        dependency_subdirectory,
        dependency_id,
    )


def _state_root(context: CIWContext) -> Path:
    try:
        return resolve_state_root(
            runner_temp=Path(context.environment["RUNNER_TEMP"]),
            state_id=context.environment["CI_WORKFLOW_STATE_ID"],
            declared_root=context.environment["CI_WORKFLOW_ROOT"],
            contract_root=context.root,
        )
    except (KeyError, FoundationError) as error:
        raise CIWError(_DOMAIN, "workspace_state_invalid") from error


def _existing_root(raw: str, code: str) -> Path:
    candidate = Path(raw)
    if not candidate.is_absolute() or candidate.is_symlink():
        raise CIWError(_DOMAIN, code)
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as error:
        raise CIWError(_DOMAIN, code) from error
    if not resolved.is_dir():
        raise CIWError(_DOMAIN, code)
    return resolved


def _runner_directory(raw: str, code: str) -> Path:
    candidate = Path(raw)
    if not candidate.is_absolute():
        raise CIWError(_DOMAIN, code)
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as error:
        raise CIWError(_DOMAIN, code) from error
    if not resolved.is_dir():
        raise CIWError(_DOMAIN, code)
    return resolved


def _bounded_existing_directory(root: Path, relative: str, code: str) -> Path:
    boundary = _existing_root(str(root), code)
    normalized = _relative_path(relative, code, allow_dot=True)
    if normalized == ".":
        return boundary
    cursor = boundary
    for part in PurePosixPath(normalized).parts:
        cursor /= part
        if cursor.is_symlink():
            raise CIWError(_DOMAIN, code)
    try:
        resolved = cursor.resolve(strict=True)
        resolved.relative_to(boundary)
    except (OSError, ValueError) as error:
        raise CIWError(_DOMAIN, code) from error
    if not resolved.is_dir():
        raise CIWError(_DOMAIN, code)
    return resolved


def _bounded_existing_file(root: Path, relative: str, code: str, *, executable: bool) -> Path:
    normalized = _relative_path(relative, code, allow_dot=False)
    boundary = _existing_root(str(root), code)
    cursor = boundary
    for part in PurePosixPath(normalized).parts:
        cursor /= part
        if cursor.is_symlink():
            raise CIWError(_DOMAIN, code)
    try:
        resolved = cursor.resolve(strict=True)
        resolved.relative_to(boundary)
    except (OSError, ValueError) as error:
        raise CIWError(_DOMAIN, code) from error
    if not resolved.is_file():
        raise CIWError(_DOMAIN, code)
    if executable and not resolved.stat().st_mode & 0o111:
        raise CIWError(_DOMAIN, code)
    return resolved


def _source_root(args: argparse.Namespace, context: CIWContext) -> Path:
    workspace = _existing_root(
        context.environment.get("GITHUB_WORKSPACE", ""),
        "workspace_invalid",
    )
    return _bounded_existing_directory(
        workspace,
        _plain(args.source_root, "source_root_invalid", maximum=255),
        "source_root_invalid",
    )


def _dependency_path(
    request: AndroidPrimitiveRequest,
    args: argparse.Namespace,
    context: CIWContext,
    state_root: Path,
) -> Path | None:
    if not request.private_dependency_used:
        return None
    verified = (
        _value(args, context, "private_dependency_verified") == "true"
        and _value(args, context, "private_dependency_remotes_erased") == "true"
        and _value(args, context, "private_dependency_credentials_erased") == "true"
        and _value(args, context, "private_dependency_head_sha") == request.private_dependency_sha
        and _value(args, context, "private_dependency_checkout_repository") == request.private_dependency_repository
        and _value(args, context, "private_dependency_checkout_id") == request.private_dependency_id
        and _value(args, context, "private_dependency_expected_subpath") == request.private_dependency_subdirectory
    )
    if not verified:
        raise CIWError(_DOMAIN, "private_dependency_unverified")
    raw = context.environment.get("CI_PRIVATE_DEPENDENCY_PATH", "")
    candidate = Path(raw)
    if not raw or not candidate.is_absolute() or candidate.is_symlink():
        raise CIWError(_DOMAIN, "private_dependency_path_invalid")
    dependency_root = _bounded_existing_directory(
        state_root,
        f"dependencies/{request.private_dependency_id}",
        "private_dependency_path_invalid",
    )
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as error:
        raise CIWError(_DOMAIN, "private_dependency_path_invalid") from error
    if resolved != dependency_root:
        raise CIWError(_DOMAIN, "private_dependency_path_invalid")
    return _bounded_existing_directory(
        dependency_root,
        request.private_dependency_subdirectory,
        "private_dependency_subdirectory_invalid",
    )


def _runtime_environment(
    context: CIWContext,
    dependency: Path | None,
) -> dict[str, str]:
    source = context.environment
    required_directories: dict[str, str] = {}
    for name in ("HOME", "GRADLE_USER_HOME", "TMPDIR"):
        required_directories[name] = str(
            _existing_root(source.get(name, ""), "runtime_environment_invalid")
        )
    sdk_raw = source.get("ANDROID_SDK_ROOT") or source.get("ANDROID_HOME") or ""
    sdk = _runner_directory(sdk_raw, "android_sdk_unavailable")
    environment = {
        "PATH": _plain(source.get("PATH", ""), "runtime_environment_invalid", maximum=32768),
        "HOME": required_directories["HOME"],
        "GRADLE_USER_HOME": required_directories["GRADLE_USER_HOME"],
        "TMPDIR": required_directories["TMPDIR"],
        "ANDROID_SDK_ROOT": str(sdk),
        "ANDROID_HOME": str(sdk),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "TZ": "UTC",
        "CI": "true",
        "GITHUB_ACTIONS": "true",
        "GRADLE_OPTS": "-Dorg.gradle.daemon=false",
    }
    java_home = source.get("JAVA_HOME", "")
    if java_home:
        environment["JAVA_HOME"] = str(_runner_directory(java_home, "java_home_invalid"))
    if dependency is not None:
        environment["CI_PRIVATE_DEPENDENCY_PATH"] = str(dependency)
    for name, value in environment.items():
        _plain(name, "runtime_environment_invalid", maximum=128)
        _plain(value, "runtime_environment_invalid", maximum=32768)
    return environment


def _plan_result(request: AndroidPrimitiveRequest) -> CIWResult:
    return CIWResult(
        _DOMAIN,
        "validate",
        outputs={
            "result": "planned",
            "source_sha": request.admitted_sha,
            "validation_scope": request.validation_scope,
            "runner_profile": "mobile",
            "runs_on_json": '["linux","amd64","mobile"]',
            "workspace_profile": "gradle",
            "execution_model": "single-executor",
            "private_dependency_used": str(request.private_dependency_used).lower(),
            "private_dependency_repository": request.private_dependency_repository,
            "private_dependency_sha": request.private_dependency_sha,
            "private_dependency_subdirectory": request.private_dependency_subdirectory,
            "private_dependency_id": request.private_dependency_id,
            "cleanup_result": "not-run",
            "failure_code": "",
        },
    )


def _execute_script(
    plan: ScriptPlan,
    *,
    project: Path,
    environment: Mapping[str, str],
) -> None:
    script = _bounded_existing_file(
        project,
        plan.path,
        "script_path_invalid",
        executable=True,
    )
    try:
        process = run_process(
            (str(script), *plan.arguments),
            cwd=project,
            environment=environment,
            timeout_seconds=120 * 60,
        )
    except RuntimePrimitiveError as error:
        raise CIWError(_DOMAIN, "script_process_failed") from error
    if process.timed_out:
        raise CIWError(_DOMAIN, "script_timeout")
    if process.returncode != 0:
        raise CIWError(_DOMAIN, "script_failed")


def _execute_request(
    request: AndroidPrimitiveRequest,
    args: argparse.Namespace,
    context: CIWContext,
) -> CIWResult:
    state_root = _state_root(context)
    source = _source_root(args, context)
    android_execution.verify_exact_source(source, request.admitted_sha, context.environment)
    copy = state_root / _COPY_RELATIVE
    if copy.exists() or copy.is_symlink():
        raise CIWError(_DOMAIN, "android_state_exists")
    android_execution.copy_source(source, copy)
    project = _bounded_existing_directory(
        copy,
        request.working_directory,
        "working_directory_invalid",
    )
    dependency = _dependency_path(request, args, context, state_root)
    environment = _runtime_environment(context, dependency)
    java = resolve_java_executable(search_path=environment["PATH"])
    runtime = inspect_java_runtime(java, working_directory=project, environment=environment)
    validate_java_runtime(runtime, expected_major=25)

    wrapper = Path(request.gradle_wrapper_path)
    common = {
        "project_directory": project,
        "options": ("--no-daemon",),
        "environment": environment,
    }
    scope = request.validation_scope
    gradle_invocations = 0
    script_invocations = 0
    schema_mode = "not-applicable"
    task_count = len(request.gradle_tasks)

    if scope == "protected-full":
        full = request.protected_full
        if full is None:
            raise CIWError(_DOMAIN, "validation_plan_invalid")
        run_gradle_tasks(
            wrapper,
            full.gradle_tasks,
            operation="android.protected_full",
            **common,
        )
        gradle_invocations = 1
        task_count = len(full.gradle_tasks)
        schema_mode = full.schema_mode
        if full.schema_script is not None:
            _execute_script(full.schema_script, project=project, environment=environment)
            script_invocations = 1
    elif scope == "targeted-unit":
        android_targeted_test(
            wrapper,
            request.gradle_tasks[0],
            request.targeted_test_selector,
            **common,
        )
        gradle_invocations = 1
    elif scope == "script":
        if request.script is None:
            raise CIWError(_DOMAIN, "validation_plan_invalid")
        _execute_script(request.script, project=project, environment=environment)
        script_invocations = 1
        task_count = 0
    else:
        run_gradle_tasks(
            wrapper,
            request.gradle_tasks,
            operation=f"android.{scope.replace('-', '_')}",
            **common,
        )
        gradle_invocations = 1

    android_execution.verify_exact_source(source, request.admitted_sha, context.environment)
    summary = json.dumps(
        {
            "execution_model": "single-executor",
            "gradle_invocations": gradle_invocations,
            "java_major": runtime.major,
            "private_dependency_used": request.private_dependency_used,
            "schema_mode": schema_mode,
            "scope": scope,
            "script_invocations": script_invocations,
            "status": "success",
            "task_count": task_count,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return CIWResult(
        _DOMAIN,
        "validate",
        outputs={
            "result": "success",
            "source_sha": request.admitted_sha,
            "validation_scope": scope,
            "test_summary": summary,
            "private_dependency_used": str(request.private_dependency_used).lower(),
            "clean_tree": "true",
            "cleanup_result": "not-run",
            "failure_code": "",
        },
    )


def _cleanup(context: CIWContext) -> CIWResult:
    state_root = _state_root(context)
    android_execution.remove_no_follow(state_root / _COPY_RELATIVE)
    return CIWResult(
        _DOMAIN,
        "validate",
        outputs={"cleanup_result": "success", "failure_code": ""},
    )


def _residue(context: CIWContext) -> CIWResult:
    state_root = _state_root(context)
    target = state_root / _COPY_RELATIVE
    if target.exists() or target.is_symlink():
        raise CIWError(_DOMAIN, "android_residue_detected")
    return CIWResult(
        _DOMAIN,
        "validate",
        outputs={"cleanup_result": "success", "failure_code": ""},
    )


def _failure_outputs(context: CIWContext, error: BaseException) -> CIWError:
    projected = project_error(error, domain=_DOMAIN)
    target = context.environment.get("GITHUB_OUTPUT", "")
    if target:
        write_command_file(
            Path(target),
            {
                "result": "failure",
                "cleanup_result": "not-run",
                "failure_code": projected.code,
            },
        )
    return projected


def execute_android_validate(args: argparse.Namespace, context: CIWContext) -> CIWResult:
    """Plan, execute, clean, or residue-check one product-neutral Android request."""

    try:
        if args.phase == "cleanup":
            return _cleanup(context)
        if args.phase == "residue":
            return _residue(context)
        request = _request(args, context)
        if args.phase == "plan":
            return _plan_result(request)
        return _execute_request(request, args, context)
    except (
        AndroidValidationError,
        CIWError,
        LanguagePrimitiveError,
        RuntimePrimitiveError,
        OSError,
        ValueError,
    ) as error:
        raise _failure_outputs(context, error) from error
