"""Bounded product-neutral Python, npm, and JVM package publication adapter."""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable, Mapping, Sequence

from .ciw_types import write_command_file
from .package_primitives import (
    PackageArtifact,
    PackageBuildResult,
    PackagePrimitiveError,
    PublicationResult,
    build_python_packages,
    npm_pack,
    npm_publish,
    publish_python_packages,
    run_jvm_publication_tasks,
    validate_package_identity,
)
from .runtime_primitives import (
    RuntimePrimitiveError,
    create_temporary_workspace,
    finalize_temporary_paths,
    run_process,
)
from .workspace import resolve_state_root

_SHA = re.compile(r"^[0-9a-f]{40}$")
_JVM_GROUP = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9_.-]{0,254}[A-Za-z0-9])?$")
_ECOSYSTEMS = frozenset({"python", "npm", "jvm"})
_PACKAGE_CREDENTIAL_NAMES = (
    "CI_PACKAGE_USERNAME",
    "CI_PACKAGE_PASSWORD",
    "CI_PACKAGE_TOKEN",
)
_MAX_PLAN_BYTES = 16 * 1024
_MAX_REGISTRY_CONFIG_BYTES = 4 * 1024
_MAX_ITEMS = 32
_PYTHON_TOOL_TIMEOUT_SECONDS = 300.0
_PYTHON_PUBLICATION_TOOLS = ("build==1.3.0", "twine==6.2.0")
_COMMON_PLAN_KEYS = frozenset({"output_directory"})
_REGISTRY_PLAN_KEYS = _COMMON_PLAN_KEYS | {"registry_profile", "registry_config_path"}
_PYTHON_PLAN_KEYS = _REGISTRY_PLAN_KEYS
_NPM_PLAN_KEYS = _REGISTRY_PLAN_KEYS
_JVM_PLAN_KEYS = _COMMON_PLAN_KEYS | {"maven_actions", "maven_options", "maven_executable"}
_PYTHON_REGISTRIES = {
    "pypi": "https://upload.pypi.org/legacy/",
    "test-pypi": "https://test.pypi.org/legacy/",
}
_NPM_REGISTRIES = {
    "npmjs": "https://registry.npmjs.org",
    "github-packages": "https://npm.pkg.github.com",
}
_RUNNER_PLANS = {
    "python": ("general-small", '["linux","amd64","general","small"]', "minimal"),
    "npm": ("mobile", '["linux","amd64","mobile"]', "node"),
    "jvm": ("mobile", '["linux","amd64","mobile"]', "minimal"),
}


class PackagePublishError(RuntimeError):
    """Fail closed with one stable package-publication error code."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class PackagePublishRequest:
    admitted_sha: str
    ecosystem: str
    working_directory: Path
    package_name: str
    package_version: str
    package_group: str | None
    output_directory: Path
    registry_profile: str | None
    registry_config_path: Path | None
    maven_actions: tuple[str, ...]
    maven_options: tuple[str, ...]
    maven_executable: str


PythonToolBootstrap = Callable[[Path, Path, Mapping[str, str]], Mapping[str, str]]


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise PackagePublishError(code)


def _single_line(
    value: object,
    *,
    code: str,
    maximum: int = 2048,
    allow_empty: bool = False,
) -> str:
    _require(isinstance(value, str), code)
    _require((allow_empty or bool(value)) and value == value.strip(), code)
    _require(len(value.encode("utf-8")) <= maximum, code)
    _require(not any(character in value for character in ("\x00", "\r", "\n")), code)
    return value


def _relative_path(value: object, *, code: str, allow_dot: bool) -> Path:
    text = _single_line(value, code=code, maximum=1024)
    _require("\\" not in text, code)
    pure = PurePosixPath(text)
    _require(not pure.is_absolute() and ".." not in pure.parts, code)
    path = Path(*pure.parts)
    if str(path) in {"", "."}:
        _require(allow_dot, code)
        return Path(".")
    return path


def _string_list(value: object, *, code: str) -> tuple[str, ...]:
    _require(isinstance(value, list) and 0 < len(value) <= _MAX_ITEMS, code)
    return tuple(_single_line(item, code=code, maximum=1024) for item in value)


def _optional_string_list(value: object, *, code: str) -> tuple[str, ...]:
    if value is None:
        return ()
    _require(isinstance(value, list) and len(value) <= _MAX_ITEMS, code)
    return tuple(_single_line(item, code=code, maximum=1024) for item in value)


def _plan(raw: str, ecosystem: str) -> dict[str, object]:
    _require(isinstance(raw, str) and bool(raw), "publication_plan_invalid")
    _require(len(raw.encode("utf-8")) <= _MAX_PLAN_BYTES, "publication_plan_invalid")
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as error:
        raise PackagePublishError("publication_plan_invalid") from error
    _require(isinstance(value, dict), "publication_plan_invalid")
    allowed = {
        "python": _PYTHON_PLAN_KEYS,
        "npm": _NPM_PLAN_KEYS,
        "jvm": _JVM_PLAN_KEYS,
    }[ecosystem]
    _require(set(value) <= allowed, "publication_plan_invalid")
    return value


def _registry_destination(
    plan: Mapping[str, object],
    registries: Mapping[str, str],
) -> tuple[str | None, Path | None]:
    profile_raw = plan.get("registry_profile")
    config_raw = plan.get("registry_config_path")
    _require((profile_raw is None) != (config_raw is None), "registry_destination_invalid")
    if profile_raw is not None:
        profile = _single_line(
            profile_raw,
            code="registry_profile_invalid",
            maximum=64,
        )
        _require(profile in registries, "registry_profile_invalid")
        return profile, None
    return None, _relative_path(
        config_raw,
        code="registry_config_path_invalid",
        allow_dot=False,
    )


def _request(environment: Mapping[str, str]) -> PackagePublishRequest:
    admitted_sha = environment.get("INPUT_ADMITTED_SHA", "").strip()
    _require(_SHA.fullmatch(admitted_sha) is not None, "admitted_sha_invalid")
    ecosystem = environment.get("INPUT_ECOSYSTEM", "").strip()
    _require(ecosystem in _ECOSYSTEMS, "ecosystem_invalid")
    working_directory = _relative_path(
        environment.get("INPUT_WORKING_DIRECTORY", "."),
        code="working_directory_invalid",
        allow_dot=True,
    )
    package_name = _single_line(
        environment.get("INPUT_PACKAGE_NAME", ""),
        code="package_name_invalid",
        maximum=128,
    )
    package_version = _single_line(
        environment.get("INPUT_PACKAGE_VERSION", ""),
        code="package_version_invalid",
        maximum=127,
    )
    try:
        identity = validate_package_identity(ecosystem, package_name, package_version)
    except PackagePrimitiveError as error:
        raise PackagePublishError(error.code) from error

    group_raw = environment.get("INPUT_PACKAGE_GROUP", "").strip()
    package_group = (
        _single_line(group_raw, code="package_group_invalid", maximum=256)
        if group_raw
        else None
    )
    if ecosystem != "jvm":
        _require(package_group is None, "package_group_forbidden")
    elif package_group is not None:
        _require(_JVM_GROUP.fullmatch(package_group) is not None, "package_group_invalid")

    plan = _plan(environment.get("INPUT_PUBLICATION_PLAN_JSON", ""), ecosystem)
    output_directory = _relative_path(
        plan.get("output_directory", ".ciw-package-output"),
        code="output_directory_invalid",
        allow_dot=False,
    )

    registry_profile: str | None = None
    registry_config_path: Path | None = None
    maven_actions: tuple[str, ...] = ()
    maven_options: tuple[str, ...] = ()
    maven_executable = "mvnw"
    if ecosystem == "python":
        registry_profile, registry_config_path = _registry_destination(
            plan,
            _PYTHON_REGISTRIES,
        )
    elif ecosystem == "npm":
        registry_profile, registry_config_path = _registry_destination(
            plan,
            _NPM_REGISTRIES,
        )
    else:
        maven_actions = _string_list(
            plan.get("maven_actions"),
            code="maven_actions_invalid",
        )
        maven_options = _optional_string_list(
            plan.get("maven_options"),
            code="maven_options_invalid",
        )
        _require(all("://" not in item for item in maven_options), "maven_options_invalid")
        maven_executable = _single_line(
            plan.get("maven_executable", "mvnw"),
            code="maven_executable_invalid",
            maximum=1024,
        )
        if maven_executable != "mvn":
            executable_path = _relative_path(
                maven_executable,
                code="maven_executable_invalid",
                allow_dot=False,
            )
            _require(executable_path.name == "mvnw", "maven_executable_invalid")
            maven_executable = executable_path.as_posix()

    return PackagePublishRequest(
        admitted_sha=admitted_sha,
        ecosystem=identity.ecosystem,
        working_directory=working_directory,
        package_name=identity.name,
        package_version=identity.version,
        package_group=package_group,
        output_directory=output_directory,
        registry_profile=registry_profile,
        registry_config_path=registry_config_path,
        maven_actions=maven_actions,
        maven_options=maven_options,
        maven_executable=maven_executable,
    )


def _bounded_source(workspace: Path, relative: Path) -> Path:
    try:
        root = (workspace / "source").resolve(strict=True)
        candidate = root / relative
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(root)
    except (OSError, ValueError) as error:
        raise PackagePublishError("working_directory_invalid") from error
    _require(resolved.is_dir() and not candidate.is_symlink(), "working_directory_invalid")
    return resolved


def _validate_source_symlinks(source: Path) -> None:
    try:
        root = source.resolve(strict=True)
        for current, directories, files in os.walk(root, followlinks=False):
            for name in (*directories, *files):
                candidate = Path(current) / name
                if not candidate.is_symlink():
                    continue
                target = candidate.resolve(strict=True)
                target.relative_to(root)
    except (OSError, RuntimeError, ValueError) as error:
        raise PackagePublishError("package_source_symlink_invalid") from error


def _copy_source(source: Path, destination: Path) -> None:
    _require(not destination.exists() and not destination.is_symlink(), "package_copy_failed")
    _validate_source_symlinks(source)
    try:
        shutil.copytree(
            source,
            destination,
            symlinks=True,
            ignore=shutil.ignore_patterns(".git", "__pycache__"),
        )
    except OSError as error:
        raise PackagePublishError("package_copy_failed") from error


def _tool_from_path(name: str, *, code: str) -> Path:
    resolved = shutil.which(name)
    _require(resolved is not None, code)
    path = Path(resolved)
    _require(path.is_absolute(), code)
    return path


def _maven_tool(project: Path, value: str) -> Path:
    if value == "mvn":
        return _tool_from_path("mvn", code="maven_tool_unavailable")
    relative = Path(*PurePosixPath(value).parts)
    candidate = project / relative
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(project.resolve(strict=True))
    except (OSError, ValueError) as error:
        raise PackagePublishError("maven_tool_unavailable") from error
    _require(
        candidate.name == "mvnw" and resolved.is_file() and not candidate.is_symlink(),
        "maven_tool_unavailable",
    )
    return resolved


def _registry_from_config(project: Path, relative: Path) -> str:
    candidate = project / relative
    try:
        root = project.resolve(strict=True)
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(root)
        _require(
            resolved.is_file() and not candidate.is_symlink(),
            "registry_config_invalid",
        )
        _require(
            resolved.stat().st_size <= _MAX_REGISTRY_CONFIG_BYTES,
            "registry_config_invalid",
        )
        raw = resolved.read_text(encoding="utf-8")
    except (OSError, UnicodeError, ValueError) as error:
        raise PackagePublishError("registry_config_invalid") from error
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as error:
        raise PackagePublishError("registry_config_invalid") from error
    _require(
        isinstance(value, dict) and set(value) == {"registry_url"},
        "registry_config_invalid",
    )
    return _single_line(
        value["registry_url"],
        code="registry_config_invalid",
        maximum=2048,
    )


def _registry_url(
    request: PackagePublishRequest,
    project: Path,
    registries: Mapping[str, str],
) -> str:
    if request.registry_profile is not None:
        return registries[request.registry_profile]
    _require(request.registry_config_path is not None, "registry_destination_invalid")
    return _registry_from_config(project, request.registry_config_path)


def _without_package_credentials(environment: Mapping[str, str]) -> dict[str, str]:
    return {
        name: value
        for name, value in environment.items()
        if name not in _PACKAGE_CREDENTIAL_NAMES
    }


def _isolated_package_environment(
    environment: Mapping[str, str],
    run_root: Path,
) -> dict[str, str]:
    base = _without_package_credentials(environment)
    directories = {
        "home": run_root / "home",
        "tmp": run_root / "tmp",
        "xdg_cache": run_root / "xdg" / "cache",
        "xdg_config": run_root / "xdg" / "config",
        "xdg_data": run_root / "xdg" / "data",
        "npm_cache": run_root / "npm" / "cache",
        "npm_prefix": run_root / "npm" / "prefix",
        "maven_home": run_root / "maven" / "home",
        "maven_repository": run_root / "maven" / "repository",
    }
    try:
        for directory in directories.values():
            directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        npm_userconfig = run_root / "npm" / "user-npmrc"
        npm_globalconfig = run_root / "npm" / "global-npmrc"
        npm_userconfig.touch(mode=0o600, exist_ok=False)
        npm_globalconfig.touch(mode=0o600, exist_ok=False)
    except OSError as error:
        raise PackagePublishError("isolation_unavailable") from error

    base.update(
        {
            "HOME": str(directories["home"]),
            "TMPDIR": str(directories["tmp"]),
            "TMP": str(directories["tmp"]),
            "TEMP": str(directories["tmp"]),
            "XDG_CACHE_HOME": str(directories["xdg_cache"]),
            "XDG_CONFIG_HOME": str(directories["xdg_config"]),
            "XDG_DATA_HOME": str(directories["xdg_data"]),
            "npm_config_cache": str(directories["npm_cache"]),
            "npm_config_prefix": str(directories["npm_prefix"]),
            "npm_config_userconfig": str(npm_userconfig),
            "npm_config_globalconfig": str(npm_globalconfig),
            "PIP_CACHE_DIR": str(directories["xdg_cache"] / "pip"),
            "PYTHONNOUSERSITE": "1",
            "PYTHONPYCACHEPREFIX": str(directories["xdg_cache"] / "pycache"),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "MAVEN_USER_HOME": str(directories["maven_home"]),
            "MAVEN_OPTS": f"-Duser.home={directories['maven_home']}",
        }
    )
    return base


def _publication_environment(
    base: Mapping[str, str],
    source: Mapping[str, str],
    ecosystem: str,
) -> dict[str, str]:
    result = dict(base)
    for name in _PACKAGE_CREDENTIAL_NAMES:
        if name in source:
            result[name] = source[name]

    if ecosystem == "python":
        username = source.get("CI_PACKAGE_USERNAME", "")
        password = source.get("CI_PACKAGE_PASSWORD", "")
        token = source.get("CI_PACKAGE_TOKEN", "")
        if username and (password or token):
            result["CI_PACKAGE_USERNAME"] = username
            result["CI_PACKAGE_PASSWORD"] = password or token
            result.pop("CI_PACKAGE_TOKEN", None)
    return result


def _bootstrap_python_tools(
    interpreter: Path,
    run_root: Path,
    environment: Mapping[str, str],
) -> Mapping[str, str]:
    tools = run_root / "python-tools"
    _require(not tools.exists() and not tools.is_symlink(), "python_tool_bootstrap_failed")
    child = dict(environment)
    child["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
    child["PIP_NO_INPUT"] = "1"
    child["PIP_NO_CACHE_DIR"] = "1"
    try:
        result = run_process(
            (
                str(interpreter),
                "-m",
                "pip",
                "install",
                "--isolated",
                "--disable-pip-version-check",
                "--no-input",
                "--no-cache-dir",
                "--target",
                str(tools),
                *_PYTHON_PUBLICATION_TOOLS,
            ),
            cwd=run_root,
            environment=child,
            timeout_seconds=_PYTHON_TOOL_TIMEOUT_SECONDS,
        )
    except RuntimePrimitiveError as error:
        raise PackagePublishError("python_tool_bootstrap_failed") from error
    _require(result.ok, "python_tool_bootstrap_failed")
    _require(tools.is_dir() and not tools.is_symlink(), "python_tool_bootstrap_failed")
    existing = child.get("PYTHONPATH", "")
    child["PYTHONPATH"] = str(tools) + (os.pathsep + existing if existing else "")
    return child


def _artifact_paths(
    project: Path,
    output: Path,
    artifacts: Sequence[PackageArtifact],
) -> tuple[Path, ...]:
    result: list[Path] = []
    for artifact in artifacts:
        relative = Path(artifact.path)
        candidate = project / relative
        if artifact.ecosystem == "python" and relative.parent == Path("."):
            candidate = project / output / relative
        result.append(candidate)
    return tuple(result)


def plan_outputs(environment: Mapping[str, str]) -> dict[str, str]:
    request = _request(environment)
    runner_profile, runs_on_json, workspace_profile = _RUNNER_PLANS[request.ecosystem]
    return {
        "runner_profile": runner_profile,
        "runs_on_json": runs_on_json,
        "workspace_profile": workspace_profile,
        "ecosystem": request.ecosystem,
        "package_name": request.package_name,
        "package_version": request.package_version,
    }


def execute_package_publish(
    *,
    workspace: Path,
    state_root: Path,
    environment: Mapping[str, str],
    python_builder: Callable[..., PackageBuildResult] = build_python_packages,
    python_publisher: Callable[..., PublicationResult] = publish_python_packages,
    npm_packer: Callable[..., PackageBuildResult] = npm_pack,
    npm_publisher: Callable[..., PublicationResult] = npm_publish,
    jvm_publisher: Callable[..., PackageBuildResult] = run_jvm_publication_tasks,
    python_tool_bootstrap: PythonToolBootstrap = _bootstrap_python_tools,
) -> dict[str, str]:
    """Build, validate, and publish one exact package from an isolated source copy."""

    request = _request(environment)
    source = _bounded_source(workspace.resolve(), request.working_directory)
    generated = state_root / "generated"
    _require(generated.is_dir() and not generated.is_symlink(), "isolation_unavailable")
    try:
        run_root = create_temporary_workspace(generated, prefix="package-publication")
    except RuntimePrimitiveError as error:
        raise PackagePublishError("isolation_unavailable") from error

    project = run_root / "project"
    primary_error: Exception | None = None
    try:
        _copy_source(source, project)
        build_environment = _isolated_package_environment(environment, run_root)
        publication_environment = _publication_environment(
            build_environment,
            environment,
            request.ecosystem,
        )
        output = request.output_directory
        if request.ecosystem == "python":
            python = Path(sys.executable).resolve()
            build_environment = dict(
                python_tool_bootstrap(python, run_root, build_environment)
            )
            publication_environment = _publication_environment(
                build_environment,
                environment,
                request.ecosystem,
            )
            built = python_builder(
                python,
                project_directory=project,
                output_directory=output,
                expected_name=request.package_name,
                expected_version=request.package_version,
                environment=build_environment,
            )
            python_publisher(
                python,
                _artifact_paths(project, output, built.artifacts),
                project_directory=project,
                registry_url=_registry_url(request, project, _PYTHON_REGISTRIES),
                package_name=request.package_name,
                package_version=request.package_version,
                environment=publication_environment,
            )
        elif request.ecosystem == "npm":
            npm = _tool_from_path("npm", code="npm_tool_unavailable")
            built = npm_packer(
                npm,
                project_directory=project,
                output_directory=output,
                expected_name=request.package_name,
                expected_version=request.package_version,
                environment=build_environment,
            )
            _require(len(built.artifacts) == 1, "npm_artifact_invalid")
            npm_publisher(
                npm,
                project / built.artifacts[0].path,
                project_directory=project,
                temporary_root=state_root / "tmp",
                registry_url=_registry_url(request, project, _NPM_REGISTRIES),
                package_name=request.package_name,
                package_version=request.package_version,
                environment=publication_environment,
            )
        else:
            maven_repository = run_root / "maven" / "repository"
            jvm_publisher(
                "maven",
                _maven_tool(project, request.maven_executable),
                request.maven_actions,
                project_directory=project,
                output_directory=output,
                package_name=request.package_name,
                package_version=request.package_version,
                package_group=request.package_group,
                options=(f"-Dmaven.repo.local={maven_repository}", *request.maven_options),
                environment=publication_environment,
            )
    except Exception as error:
        primary_error = error

    try:
        finalize_temporary_paths((run_root,), root=generated)
    except RuntimePrimitiveError as error:
        raise PackagePublishError("package_cleanup_failed") from error
    if primary_error is not None:
        raise primary_error

    return {
        "result": "success",
        "ecosystem": request.ecosystem,
        "package_name": request.package_name,
        "package_version": request.package_version,
        "failure_code": "",
    }


def _state_root(environment: Mapping[str, str], contract_root: Path) -> Path:
    try:
        runner_temp = Path(environment["RUNNER_TEMP"])
        state_id = environment["CI_WORKFLOW_STATE_ID"]
        declared_root = environment["CI_WORKFLOW_ROOT"]
    except KeyError as error:
        raise PackagePublishError("isolation_unavailable") from error
    try:
        return resolve_state_root(
            runner_temp=runner_temp,
            state_id=state_id,
            declared_root=declared_root,
            contract_root=contract_root,
        )
    except Exception as error:
        raise PackagePublishError("isolation_unavailable") from error


def _emit(environment: Mapping[str, str], values: Mapping[str, str]) -> None:
    output = environment.get("GITHUB_OUTPUT", "")
    if output:
        write_command_file(Path(output), values)


def _failure(environment: Mapping[str, str], code: str) -> None:
    _emit(
        environment,
        {
            "result": "failure",
            "ecosystem": "",
            "package_name": "",
            "package_version": "",
            "failure_code": code,
        },
    )


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog="ciw-packages")
    result.add_argument("--phase", choices=("plan", "execute"), required=True)
    result.add_argument("--root", type=Path, required=True)
    result.add_argument("--workspace", type=Path, required=True)
    return result


def main(
    argv: Sequence[str] | None = None,
    *,
    environment: Mapping[str, str] | None = None,
) -> int:
    args = parser().parse_args(argv)
    env = dict(os.environ if environment is None else environment)
    try:
        if args.phase == "plan":
            outputs = plan_outputs(env)
        else:
            outputs = execute_package_publish(
                workspace=args.workspace.resolve(),
                state_root=_state_root(env, args.root.resolve()),
                environment=env,
            )
        _emit(env, outputs)
        return 0
    except PackagePublishError as error:
        _failure(env, error.code)
        sys.stderr.write(f"package publication failed: {error.code}\n")
        return 2
    except PackagePrimitiveError as error:
        _failure(env, error.code)
        sys.stderr.write(f"package publication failed: {error.code}\n")
        return 2
    except RuntimePrimitiveError as error:
        _failure(env, error.code)
        sys.stderr.write(f"package publication failed: {error.code}\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
