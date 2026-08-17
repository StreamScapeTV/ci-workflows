"""Product-neutral Android live-service and unsigned-release CIW adapters."""
from __future__ import annotations

import argparse
import fnmatch
import json
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Mapping, Sequence

from . import android_execution
from .ciw_android import (
    _FULL_SHA,
    _IDENTIFIER,
    _REPOSITORY,
    _bounded_existing_directory,
    _bounded_existing_file,
    _existing_root,
    _plain,
    _relative_path,
    _runner_directory,
    _state_root,
    _tasks,
)
from .ciw_types import CIWContext, CIWError, CIWResult, input_value, project_error, write_command_file
from .language_primitives import (
    LanguagePrimitiveError,
    inspect_java_runtime,
    resolve_java_executable,
    resolve_python_interpreter,
    run_gradle_tasks,
    run_python_script,
    validate_java_runtime,
)
from .runtime_primitives import RuntimePrimitiveError, run_process

_LIVE_DOMAIN = "android-live"
_RELEASE_DOMAIN = "android-release"
_MAX_PLAN_BYTES = 24 * 1024
_LIVE_COPY = "tmp/android-live-source"
_RELEASE_COPY = "tmp/android-release-source"
_ARTIFACT_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
_ARTIFACT_KINDS = {
    "apk": ".apk",
    "aab": ".aab",
    "json": ".json",
    "html": ".html",
    "xml": ".xml",
}


@dataclass(frozen=True, slots=True)
class DependencyPlan:
    repository: str
    sha: str
    subdirectory: str
    identifier: str

    @property
    def used(self) -> bool:
        return bool(self.repository)


@dataclass(frozen=True, slots=True)
class CommandScript:
    interpreter: str
    path: str
    arguments: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class LiveServicePlan:
    admitted_sha: str
    working_directory: str
    script: CommandScript
    dependency: DependencyPlan


@dataclass(frozen=True, slots=True)
class GradleGroup:
    tasks: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SizeBudgetPlan:
    script_path: str
    apk_glob: str
    aab_glob: str
    budget_path: str
    baseline_path: str
    output_path: str


@dataclass(frozen=True, slots=True)
class ArtifactPlan:
    path: str
    kind: str
    required: bool
    maximum_files: int


@dataclass(frozen=True, slots=True)
class ReleasePlan:
    admitted_sha: str
    working_directory: str
    gradle_wrapper_path: str
    pre_scripts: tuple[CommandScript, ...]
    gradle_groups: tuple[GradleGroup, ...]
    post_scripts: tuple[CommandScript, ...]
    size_budget: SizeBudgetPlan
    artifacts: tuple[ArtifactPlan, ...]
    artifact_name: str
    retention_days: int
    dependency: DependencyPlan


def configure_android_live_validate(parser: argparse.ArgumentParser) -> None:
    _configure(parser)


def configure_android_release_validate(parser: argparse.ArgumentParser) -> None:
    _configure(parser)
    parser.add_argument("--gradle-wrapper-path")


def _configure(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--phase", choices=("plan", "execute", "cleanup", "residue"), default="execute")
    parser.add_argument("--source-root", default="source")
    for name in (
        "admitted-sha",
        "working-directory",
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
    return str(value).strip() if value is not None else input_value(context.environment, name, default)


def _json(raw: str, domain: str) -> dict[str, object]:
    text = _plain(raw, "validation_plan_invalid", maximum=_MAX_PLAN_BYTES)
    try:
        value = json.loads(text)
    except json.JSONDecodeError as error:
        raise CIWError(domain, "validation_plan_invalid") from error
    if not isinstance(value, dict):
        raise CIWError(domain, "validation_plan_invalid")
    return value


def _exact(value: Mapping[str, object], keys: set[str], domain: str) -> None:
    if set(value) != keys:
        raise CIWError(domain, "validation_plan_invalid")


def _strings(value: object, domain: str, *, maximum: int = 32) -> tuple[str, ...]:
    if not isinstance(value, list) or len(value) > maximum:
        raise CIWError(domain, "validation_plan_invalid")
    result = tuple(_plain(item, "validation_plan_invalid", maximum=2048) for item in value)
    if len(result) != len(set(result)):
        raise CIWError(domain, "validation_plan_invalid")
    return result


def _script(value: object, domain: str) -> CommandScript:
    if not isinstance(value, dict):
        raise CIWError(domain, "validation_plan_invalid")
    _exact(value, {"interpreter", "path", "arguments"}, domain)
    interpreter = _plain(value["interpreter"], "validation_plan_invalid", maximum=16)
    if interpreter not in {"bash", "python3"}:
        raise CIWError(domain, "validation_plan_invalid")
    path = _relative_path(value["path"], "script_path_invalid", allow_dot=False)
    if interpreter == "bash" and not path.endswith(".sh"):
        raise CIWError(domain, "script_path_invalid")
    if interpreter == "python3" and not path.endswith(".py"):
        raise CIWError(domain, "script_path_invalid")
    return CommandScript(interpreter, path, _strings(value["arguments"], domain, maximum=32))


def _dependency(args: argparse.Namespace, context: CIWContext, domain: str) -> DependencyPlan:
    repository = _plain(
        _value(args, context, "private_dependency_repository"),
        "private_dependency_invalid",
        allow_empty=True,
        maximum=255,
    )
    sha = _plain(
        _value(args, context, "private_dependency_sha"),
        "private_dependency_invalid",
        allow_empty=True,
        maximum=40,
    )
    identifier = _plain(
        _value(args, context, "private_dependency_id"),
        "private_dependency_invalid",
        allow_empty=True,
        maximum=64,
    )
    if any((repository, sha, identifier)):
        if (
            not all((repository, sha, identifier))
            or _REPOSITORY.fullmatch(repository) is None
            or _FULL_SHA.fullmatch(sha) is None
            or _IDENTIFIER.fullmatch(identifier) is None
        ):
            raise CIWError(domain, "private_dependency_invalid")
    subdirectory = _relative_path(
        _value(args, context, "private_dependency_subdirectory", "."),
        "private_dependency_subdirectory_invalid",
        allow_dot=True,
    )
    return DependencyPlan(repository, sha, subdirectory, identifier)


def _base(args: argparse.Namespace, context: CIWContext, domain: str) -> tuple[str, str, DependencyPlan]:
    admitted_sha = _value(args, context, "admitted_sha")
    if _FULL_SHA.fullmatch(admitted_sha) is None:
        raise CIWError(domain, "admitted_sha_invalid")
    working = _relative_path(
        _value(args, context, "working_directory", "."),
        "working_directory_invalid",
        allow_dot=True,
    )
    return admitted_sha, working, _dependency(args, context, domain)


def _live_plan(args: argparse.Namespace, context: CIWContext) -> LiveServicePlan:
    admitted_sha, working, dependency = _base(args, context, _LIVE_DOMAIN)
    value = _json(_value(args, context, "validation_plan_json"), _LIVE_DOMAIN)
    _exact(value, {"script"}, _LIVE_DOMAIN)
    return LiveServicePlan(admitted_sha, working, _script(value["script"], _LIVE_DOMAIN), dependency)


def _artifact_pattern(value: object, kind: str, domain: str) -> str:
    text = _plain(value, "artifact_path_invalid", maximum=1024)
    if "\\" in text or text.startswith("/") or "**" in text or any(char in text for char in "?[]"):
        raise CIWError(domain, "artifact_path_invalid")
    pure = PurePosixPath(text)
    if not pure.parts or ".." in pure.parts or any("*" in part for part in pure.parts[:-1]):
        raise CIWError(domain, "artifact_path_invalid")
    if kind not in _ARTIFACT_KINDS or not pure.name.endswith(_ARTIFACT_KINDS[kind]):
        raise CIWError(domain, "artifact_path_invalid")
    return pure.as_posix()


def _release_plan(args: argparse.Namespace, context: CIWContext) -> ReleasePlan:
    admitted_sha, working, dependency = _base(args, context, _RELEASE_DOMAIN)
    wrapper = _relative_path(
        _value(args, context, "gradle_wrapper_path", "gradlew"),
        "gradle_wrapper_path_invalid",
        allow_dot=False,
    )
    value = _json(_value(args, context, "validation_plan_json"), _RELEASE_DOMAIN)
    _exact(
        value,
        {"pre_scripts", "gradle_groups", "post_scripts", "size_budget", "artifacts", "artifact_name", "retention_days"},
        _RELEASE_DOMAIN,
    )
    pre_raw = value["pre_scripts"]
    post_raw = value["post_scripts"]
    groups_raw = value["gradle_groups"]
    artifacts_raw = value["artifacts"]
    if not isinstance(pre_raw, list) or len(pre_raw) > 8 or not isinstance(post_raw, list) or len(post_raw) > 8:
        raise CIWError(_RELEASE_DOMAIN, "validation_plan_invalid")
    if not isinstance(groups_raw, list) or not 1 <= len(groups_raw) <= 8:
        raise CIWError(_RELEASE_DOMAIN, "validation_plan_invalid")
    groups: list[GradleGroup] = []
    for raw in groups_raw:
        if not isinstance(raw, dict):
            raise CIWError(_RELEASE_DOMAIN, "validation_plan_invalid")
        _exact(raw, {"tasks"}, _RELEASE_DOMAIN)
        groups.append(GradleGroup(_tasks(raw["tasks"], "validation_plan_invalid", maximum_items=16)))
    size_raw = value["size_budget"]
    if not isinstance(size_raw, dict):
        raise CIWError(_RELEASE_DOMAIN, "validation_plan_invalid")
    _exact(size_raw, {"script_path", "apk_glob", "aab_glob", "budget_path", "baseline_path", "output_path"}, _RELEASE_DOMAIN)
    size = SizeBudgetPlan(
        _relative_path(size_raw["script_path"], "script_path_invalid", allow_dot=False),
        _artifact_pattern(size_raw["apk_glob"], "apk", _RELEASE_DOMAIN),
        _artifact_pattern(size_raw["aab_glob"], "aab", _RELEASE_DOMAIN),
        _relative_path(size_raw["budget_path"], "artifact_budget_invalid", allow_dot=False),
        _relative_path(size_raw["baseline_path"], "artifact_budget_invalid", allow_dot=False),
        _relative_path(size_raw["output_path"], "artifact_budget_invalid", allow_dot=False),
    )
    if not size.script_path.endswith(".py") or not size.output_path.endswith(".json"):
        raise CIWError(_RELEASE_DOMAIN, "artifact_budget_invalid")
    if not isinstance(artifacts_raw, list) or not 1 <= len(artifacts_raw) <= 24:
        raise CIWError(_RELEASE_DOMAIN, "artifact_manifest_invalid")
    artifacts: list[ArtifactPlan] = []
    for raw in artifacts_raw:
        if not isinstance(raw, dict):
            raise CIWError(_RELEASE_DOMAIN, "artifact_manifest_invalid")
        _exact(raw, {"path", "kind", "required", "max_files"}, _RELEASE_DOMAIN)
        kind = _plain(raw["kind"], "artifact_manifest_invalid", maximum=16)
        required = raw["required"]
        maximum_files = raw["max_files"]
        if not isinstance(required, bool) or isinstance(maximum_files, bool) or not isinstance(maximum_files, int) or not 1 <= maximum_files <= 50:
            raise CIWError(_RELEASE_DOMAIN, "artifact_manifest_invalid")
        artifacts.append(ArtifactPlan(_artifact_pattern(raw["path"], kind, _RELEASE_DOMAIN), kind, required, maximum_files))
    artifact_name = _plain(value["artifact_name"], "artifact_name_invalid", maximum=80)
    if _ARTIFACT_NAME.fullmatch(artifact_name) is None:
        raise CIWError(_RELEASE_DOMAIN, "artifact_name_invalid")
    retention = value["retention_days"]
    if isinstance(retention, bool) or not isinstance(retention, int) or not 1 <= retention <= 30:
        raise CIWError(_RELEASE_DOMAIN, "artifact_retention_invalid")
    return ReleasePlan(
        admitted_sha,
        working,
        wrapper,
        tuple(_script(row, _RELEASE_DOMAIN) for row in pre_raw),
        tuple(groups),
        tuple(_script(row, _RELEASE_DOMAIN) for row in post_raw),
        size,
        tuple(artifacts),
        artifact_name,
        retention,
        dependency,
    )


def _plan_outputs(domain: str, admitted_sha: str, dependency: DependencyPlan, **extra: str) -> CIWResult:
    outputs = {
        "result": "planned",
        "source_sha": admitted_sha,
        "runner_profile": "mobile",
        "runs_on_json": '["linux","amd64","mobile"]',
        "workspace_profile": "gradle",
        "execution_model": "single-executor",
        "private_dependency_used": str(dependency.used).lower(),
        "private_dependency_repository": dependency.repository,
        "private_dependency_sha": dependency.sha,
        "private_dependency_subdirectory": dependency.subdirectory,
        "private_dependency_id": dependency.identifier,
        "cleanup_result": "not-run",
        "failure_code": "",
        **extra,
    }
    return CIWResult(domain, "validate", outputs=outputs)


def _dependency_path(dependency: DependencyPlan, args: argparse.Namespace, context: CIWContext, state_root: Path, domain: str) -> Path | None:
    if not dependency.used:
        return None
    verified = (
        _value(args, context, "private_dependency_verified") == "true"
        and _value(args, context, "private_dependency_remotes_erased") == "true"
        and _value(args, context, "private_dependency_credentials_erased") == "true"
        and _value(args, context, "private_dependency_head_sha") == dependency.sha
        and _value(args, context, "private_dependency_checkout_repository") == dependency.repository
        and _value(args, context, "private_dependency_checkout_id") == dependency.identifier
        and _value(args, context, "private_dependency_expected_subpath") == dependency.subdirectory
    )
    if not verified:
        raise CIWError(domain, "private_dependency_unverified")
    raw = context.environment.get("CI_PRIVATE_DEPENDENCY_PATH", "")
    if not raw:
        raise CIWError(domain, "private_dependency_path_invalid")
    candidate = Path(raw)
    if not candidate.is_absolute() or candidate.is_symlink():
        raise CIWError(domain, "private_dependency_path_invalid")
    expected = _bounded_existing_directory(state_root, f"dependencies/{dependency.identifier}", "private_dependency_path_invalid")
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as error:
        raise CIWError(domain, "private_dependency_path_invalid") from error
    if resolved != expected:
        raise CIWError(domain, "private_dependency_path_invalid")
    return _bounded_existing_directory(expected, dependency.subdirectory, "private_dependency_subdirectory_invalid")


def _runtime_environment(context: CIWContext, dependency: Path | None, domain: str, *, credentials: bool = False) -> dict[str, str]:
    source = context.environment
    environment = {
        "PATH": _plain(source.get("PATH", ""), "runtime_environment_invalid", maximum=32768),
        "HOME": str(_existing_root(source.get("HOME", ""), "runtime_environment_invalid")),
        "GRADLE_USER_HOME": str(_existing_root(source.get("GRADLE_USER_HOME", ""), "runtime_environment_invalid")),
        "TMPDIR": str(_existing_root(source.get("TMPDIR", ""), "runtime_environment_invalid")),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "TZ": "UTC",
        "CI": "true",
        "GITHUB_ACTIONS": "true",
        "GRADLE_OPTS": "-Dorg.gradle.daemon=false",
    }
    sdk_raw = source.get("ANDROID_SDK_ROOT") or source.get("ANDROID_HOME") or ""
    sdk = _runner_directory(sdk_raw, "android_sdk_unavailable")
    environment["ANDROID_SDK_ROOT"] = str(sdk)
    environment["ANDROID_HOME"] = str(sdk)
    java_home = source.get("JAVA_HOME", "")
    if java_home:
        environment["JAVA_HOME"] = str(_runner_directory(java_home, "java_home_invalid"))
    if dependency is not None:
        environment["CI_PRIVATE_DEPENDENCY_PATH"] = str(dependency)
    if credentials:
        username = source.get("CIW_SERVICE_USERNAME", "")
        password = source.get("CIW_SERVICE_PASSWORD", "")
        if not username or not password:
            raise CIWError(domain, "service_credentials_missing")
        environment["CIW_SERVICE_USERNAME"] = _plain(username, "service_credentials_invalid", maximum=4096)
        environment["CIW_SERVICE_PASSWORD"] = _plain(password, "service_credentials_invalid", maximum=4096)
    return environment


def _prepare_copy(admitted_sha: str, working: str, copy_relative: str, args: argparse.Namespace, context: CIWContext, domain: str) -> tuple[Path, Path, Path]:
    state_root = _state_root(context)
    workspace = _existing_root(context.environment.get("GITHUB_WORKSPACE", ""), "workspace_invalid")
    source = _bounded_existing_directory(workspace, _plain(args.source_root, "source_root_invalid", maximum=255), "source_root_invalid")
    android_execution.verify_exact_source(source, admitted_sha, context.environment)
    copy = state_root / copy_relative
    if copy.exists() or copy.is_symlink():
        raise CIWError(domain, "android_state_exists")
    android_execution.copy_source(source, copy)
    project = _bounded_existing_directory(copy, working, "working_directory_invalid")
    return state_root, source, project


def _verify_java(project: Path, environment: Mapping[str, str]) -> int:
    java = resolve_java_executable(search_path=environment["PATH"])
    runtime = inspect_java_runtime(java, working_directory=project, environment=environment)
    validate_java_runtime(runtime, expected_major=25)
    return runtime.major


def _run_script(plan: CommandScript, project: Path, environment: Mapping[str, str], domain: str) -> None:
    script = _bounded_existing_file(project, plan.path, "script_path_invalid", executable=False)
    executable = plan.interpreter
    result = run_process((executable, str(script), *plan.arguments), cwd=project, environment=environment, timeout_seconds=120 * 60)
    if result.timed_out:
        raise CIWError(domain, "script_timeout")
    if result.returncode != 0:
        raise CIWError(domain, "script_failed")


def _safe_output_path(project: Path, relative: str) -> Path:
    pure = PurePosixPath(_relative_path(relative, "artifact_budget_invalid", allow_dot=False))
    current = project
    for part in pure.parts[:-1]:
        current /= part
        if current.is_symlink():
            raise CIWError(_RELEASE_DOMAIN, "artifact_budget_invalid")
    return project.joinpath(*pure.parts)


def _resolve_pattern(project: Path, pattern: str, kind: str, *, maximum: int, required: bool) -> tuple[Path, ...]:
    pure = PurePosixPath(pattern)
    parent_rel = PurePosixPath(*pure.parts[:-1]).as_posix() if len(pure.parts) > 1 else "."
    parent = _bounded_existing_directory(project, parent_rel, "artifact_path_invalid")
    matches: list[Path] = []
    for child in parent.iterdir():
        if fnmatch.fnmatchcase(child.name, pure.name):
            if child.is_symlink() or not child.is_file() or not child.name.endswith(_ARTIFACT_KINDS[kind]):
                raise CIWError(_RELEASE_DOMAIN, "artifact_path_invalid")
            matches.append(child.resolve(strict=True))
    matches.sort(key=lambda path: str(path))
    if required and not matches:
        raise CIWError(_RELEASE_DOMAIN, "artifact_missing")
    if len(matches) > maximum:
        raise CIWError(_RELEASE_DOMAIN, "artifact_count_exceeded")
    return tuple(matches)


def _execute_live(plan: LiveServicePlan, args: argparse.Namespace, context: CIWContext) -> CIWResult:
    state_root, source, project = _prepare_copy(plan.admitted_sha, plan.working_directory, _LIVE_COPY, args, context, _LIVE_DOMAIN)
    dependency = _dependency_path(plan.dependency, args, context, state_root, _LIVE_DOMAIN)
    environment = _runtime_environment(context, dependency, _LIVE_DOMAIN, credentials=True)
    java_major = _verify_java(project, environment)
    _run_script(plan.script, project, environment, _LIVE_DOMAIN)
    android_execution.verify_exact_source(source, plan.admitted_sha, context.environment)
    summary = json.dumps({"credentialed": True, "execution_model": "single-executor", "java_major": java_major, "private_dependency_used": plan.dependency.used, "script_invocations": 1, "status": "success"}, sort_keys=True, separators=(",", ":"))
    return CIWResult(_LIVE_DOMAIN, "validate", outputs={"result": "success", "source_sha": plan.admitted_sha, "test_summary": summary, "cleanup_result": "not-run", "failure_code": ""})


def _execute_release(plan: ReleasePlan, args: argparse.Namespace, context: CIWContext) -> CIWResult:
    state_root, source, project = _prepare_copy(plan.admitted_sha, plan.working_directory, _RELEASE_COPY, args, context, _RELEASE_DOMAIN)
    dependency = _dependency_path(plan.dependency, args, context, state_root, _RELEASE_DOMAIN)
    environment = _runtime_environment(context, dependency, _RELEASE_DOMAIN)
    java_major = _verify_java(project, environment)
    for script in plan.pre_scripts:
        _run_script(script, project, environment, _RELEASE_DOMAIN)
    for group in plan.gradle_groups:
        run_gradle_tasks(Path(plan.gradle_wrapper_path), group.tasks, project_directory=project, options=("--no-daemon", "--warning-mode=all", "--stacktrace"), environment=environment, operation="android.unsigned_release")
    for script in plan.post_scripts:
        _run_script(script, project, environment, _RELEASE_DOMAIN)
    apk = _resolve_pattern(project, plan.size_budget.apk_glob, "apk", maximum=1, required=True)[0]
    aab = _resolve_pattern(project, plan.size_budget.aab_glob, "aab", maximum=1, required=True)[0]
    budget = _bounded_existing_file(project, plan.size_budget.budget_path, "artifact_budget_invalid", executable=False)
    baseline = _bounded_existing_file(project, plan.size_budget.baseline_path, "artifact_budget_invalid", executable=False)
    script = _bounded_existing_file(project, plan.size_budget.script_path, "script_path_invalid", executable=False)
    output = _safe_output_path(project, plan.size_budget.output_path)
    python = resolve_python_interpreter(search_path=environment["PATH"])
    run_python_script(python, script, project_directory=project, arguments=("--apk", str(apk), "--aab", str(aab), "--budget", str(budget), "--baseline", str(baseline), "--output", str(output)), environment=environment)
    artifacts: list[dict[str, object]] = []
    upload_paths: list[str] = []
    for artifact in plan.artifacts:
        matches = _resolve_pattern(project, artifact.path, artifact.kind, maximum=artifact.maximum_files, required=artifact.required)
        artifacts.append({"kind": artifact.kind, "count": len(matches), "required": artifact.required})
        upload_paths.extend(str(path) for path in matches)
    unique_paths = tuple(dict.fromkeys(upload_paths))
    if not unique_paths:
        raise CIWError(_RELEASE_DOMAIN, "artifact_missing")
    android_execution.verify_exact_source(source, plan.admitted_sha, context.environment)
    summary = json.dumps({"artifact_count": len(unique_paths), "execution_model": "single-executor", "gradle_invocations": len(plan.gradle_groups), "java_major": java_major, "post_scripts": len(plan.post_scripts), "pre_scripts": len(plan.pre_scripts), "private_dependency_used": plan.dependency.used, "status": "success"}, sort_keys=True, separators=(",", ":"))
    return CIWResult(_RELEASE_DOMAIN, "validate", outputs={"result": "success", "source_sha": plan.admitted_sha, "test_summary": summary, "artifact_manifest_json": json.dumps(artifacts, sort_keys=True, separators=(",", ":")), "artifact_paths_json": json.dumps(unique_paths, separators=(",", ":")), "artifact_name": plan.artifact_name, "retention_days": str(plan.retention_days), "cleanup_result": "not-run", "failure_code": ""})


def _cleanup(context: CIWContext, domain: str, relative: str) -> CIWResult:
    android_execution.remove_no_follow(_state_root(context) / relative)
    return CIWResult(domain, "validate", outputs={"cleanup_result": "success", "failure_code": ""})


def _residue(context: CIWContext, domain: str, relative: str) -> CIWResult:
    target = _state_root(context) / relative
    if target.exists() or target.is_symlink():
        raise CIWError(domain, "android_residue_detected")
    return CIWResult(domain, "validate", outputs={"cleanup_result": "success", "failure_code": ""})


def _failure(context: CIWContext, domain: str, error: BaseException) -> CIWError:
    projected = project_error(error, domain=domain)
    target = context.environment.get("GITHUB_OUTPUT", "")
    if target:
        write_command_file(Path(target), {"result": "failure", "cleanup_result": "not-run", "failure_code": projected.code})
    return projected


def execute_android_live_validate(args: argparse.Namespace, context: CIWContext) -> CIWResult:
    try:
        if args.phase == "cleanup":
            return _cleanup(context, _LIVE_DOMAIN, _LIVE_COPY)
        if args.phase == "residue":
            return _residue(context, _LIVE_DOMAIN, _LIVE_COPY)
        plan = _live_plan(args, context)
        if args.phase == "plan":
            return _plan_outputs(_LIVE_DOMAIN, plan.admitted_sha, plan.dependency)
        return _execute_live(plan, args, context)
    except (CIWError, LanguagePrimitiveError, RuntimePrimitiveError, OSError, ValueError) as error:
        raise _failure(context, _LIVE_DOMAIN, error) from error


def execute_android_release_validate(args: argparse.Namespace, context: CIWContext) -> CIWResult:
    try:
        if args.phase == "cleanup":
            return _cleanup(context, _RELEASE_DOMAIN, _RELEASE_COPY)
        if args.phase == "residue":
            return _residue(context, _RELEASE_DOMAIN, _RELEASE_COPY)
        plan = _release_plan(args, context)
        if args.phase == "plan":
            return _plan_outputs(_RELEASE_DOMAIN, plan.admitted_sha, plan.dependency, artifact_name=plan.artifact_name, retention_days=str(plan.retention_days))
        return _execute_release(plan, args, context)
    except (CIWError, LanguagePrimitiveError, RuntimePrimitiveError, OSError, ValueError) as error:
        raise _failure(context, _RELEASE_DOMAIN, error) from error
