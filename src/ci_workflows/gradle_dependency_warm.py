"""Resolve a Gradle project's dependency graph without compiling product code."""
from __future__ import annotations

import argparse
import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Mapping, Sequence

from .ciw_types import CIWError, write_command_file
from .runtime_primitives import RuntimePrimitiveError, run_process

_DOMAIN = "gradle-warm"
_FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
_SECRET = re.compile(r"(?i)\b(token|password|authorization|secret|keystore)\s*[:=]\s*\S+")
_URL_CREDENTIAL = re.compile(r"(?i)https?://[^\s/@:]+:[^\s/@]+@")
_COPY_RELATIVE = "tmp/gradle-dependency-warm-source"
_GRADLE_RO_DEP_CACHE = Path("/opt/gradle-ro-cache")
_MAX_DIAGNOSTIC_LINES = 80
_MAX_DIAGNOSTIC_BYTES = 16 * 1024


@dataclass(frozen=True, slots=True)
class GradleDependencyWarmResult:
    source_sha: str
    cache_mode: str
    wall_ms: int

    def output_values(self) -> dict[str, str]:
        return {
            "result": "success",
            "source_sha": self.source_sha,
            "gradle_dependency_cache_mode": self.cache_mode,
            "warm_wall_ms": str(self.wall_ms),
        }


def _plain(value: object, code: str, *, maximum: int = 32768) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value.encode("utf-8")) > maximum
        or any(character in value for character in ("\x00", "\r", "\n"))
    ):
        raise CIWError(_DOMAIN, code)
    return value


def _relative(value: object, code: str, *, allow_dot: bool) -> str:
    text = _plain(value, code, maximum=1024)
    if text == "." and allow_dot:
        return text
    if text.startswith("/") or "\\" in text:
        raise CIWError(_DOMAIN, code)
    pure = PurePosixPath(text)
    if not pure.parts or any(part in {"", ".", ".."} for part in pure.parts):
        raise CIWError(_DOMAIN, code)
    return pure.as_posix()


def _existing_directory(raw: object, code: str) -> Path:
    text = _plain(raw, code)
    candidate = Path(text)
    if not candidate.is_absolute() or candidate.is_symlink():
        raise CIWError(_DOMAIN, code)
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as error:
        raise CIWError(_DOMAIN, code) from error
    if resolved != candidate or not resolved.is_dir():
        raise CIWError(_DOMAIN, code)
    return resolved


def _bounded_directory(root: Path, relative: str, code: str) -> Path:
    normalized = _relative(relative, code, allow_dot=True)
    if normalized == ".":
        return root
    cursor = root
    for part in PurePosixPath(normalized).parts:
        cursor /= part
        if cursor.is_symlink():
            raise CIWError(_DOMAIN, code)
    try:
        resolved = cursor.resolve(strict=True)
        resolved.relative_to(root)
    except (OSError, ValueError) as error:
        raise CIWError(_DOMAIN, code) from error
    if not resolved.is_dir():
        raise CIWError(_DOMAIN, code)
    return resolved


def _bounded_file(root: Path, relative: str, code: str) -> Path:
    normalized = _relative(relative, code, allow_dot=False)
    cursor = root
    for part in PurePosixPath(normalized).parts:
        cursor /= part
        if cursor.is_symlink():
            raise CIWError(_DOMAIN, code)
    try:
        resolved = cursor.resolve(strict=True)
        resolved.relative_to(root)
    except (OSError, ValueError) as error:
        raise CIWError(_DOMAIN, code) from error
    if not resolved.is_file() or not os.access(resolved, os.X_OK):
        raise CIWError(_DOMAIN, code)
    return resolved


def _git_environment(environment: Mapping[str, str]) -> dict[str, str]:
    return {
        "PATH": _plain(environment.get("PATH", ""), "runtime_environment_invalid"),
        "HOME": str(_existing_directory(environment.get("HOME", ""), "runtime_environment_invalid")),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_TERMINAL_PROMPT": "0",
        "LANG": "C",
        "LC_ALL": "C",
    }


def _git_output(root: Path, arguments: Sequence[str], environment: Mapping[str, str]) -> str:
    try:
        result = run_process(
            ("git", *arguments),
            cwd=root,
            environment=_git_environment(environment),
            timeout_seconds=60,
        )
    except RuntimePrimitiveError as error:
        raise CIWError(_DOMAIN, "source_verification_failed") from error
    if result.timed_out or result.returncode != 0:
        raise CIWError(_DOMAIN, "source_verification_failed")
    return result.stdout.strip()


def _verify_exact_source(root: Path, sha: str, environment: Mapping[str, str]) -> None:
    if not root.is_dir() or not (root / ".git").exists():
        raise CIWError(_DOMAIN, "source_verification_failed")
    if _git_output(root, ("rev-parse", "HEAD"), environment) != sha:
        raise CIWError(_DOMAIN, "source_verification_failed")
    if _git_output(root, ("status", "--porcelain=v1", "--untracked-files=all"), environment):
        raise CIWError(_DOMAIN, "source_not_clean")


def _copy_source(source: Path, destination: Path) -> None:
    if destination.exists() or destination.is_symlink():
        raise CIWError(_DOMAIN, "warm_state_exists")
    try:
        for current, directories, files in os.walk(source, followlinks=False):
            base = Path(current)
            entries = [*(base / name for name in directories), *(base / name for name in files)]
            if any(entry.is_symlink() for entry in entries):
                raise CIWError(_DOMAIN, "source_symlink_rejected")
            target = destination / base.relative_to(source)
            target.mkdir(parents=True, exist_ok=True)
            for name in files:
                source_file = base / name
                target_file = target / name
                target_file.write_bytes(source_file.read_bytes())
                os.chmod(target_file, source_file.stat().st_mode & 0o777)
    except OSError as error:
        raise CIWError(_DOMAIN, "warm_copy_failed") from error


def _remove_no_follow(path: Path) -> None:
    if not path.exists() and not path.is_symlink():
        return
    if path.is_symlink() or path.is_file():
        path.unlink()
        return
    if not path.is_dir():
        raise CIWError(_DOMAIN, "warm_cleanup_failed")
    try:
        for entry in os.scandir(path):
            child = path / entry.name
            if entry.is_symlink() or entry.is_file(follow_symlinks=False):
                child.unlink()
            elif entry.is_dir(follow_symlinks=False):
                _remove_no_follow(child)
            else:
                raise CIWError(_DOMAIN, "warm_cleanup_failed")
        path.rmdir()
    except OSError as error:
        raise CIWError(_DOMAIN, "warm_cleanup_failed") from error


def _read_only_cache(environment: Mapping[str, str]) -> Path | None:
    raw = environment.get("GRADLE_RO_DEP_CACHE", "")
    if not raw:
        return None
    if raw != str(_GRADLE_RO_DEP_CACHE):
        raise CIWError(_DOMAIN, "gradle_read_only_cache_invalid")
    candidate = Path(raw)
    if candidate.is_symlink():
        raise CIWError(_DOMAIN, "gradle_read_only_cache_invalid")
    try:
        resolved = candidate.resolve(strict=True)
    except OSError:
        return None
    if resolved != candidate or not resolved.is_dir():
        return None
    return resolved


def _runtime_environment(environment: Mapping[str, str], state_root: Path) -> dict[str, str]:
    home = _existing_directory(environment.get("HOME", ""), "runtime_environment_invalid")
    gradle_home = _existing_directory(
        environment.get("GRADLE_USER_HOME", ""),
        "runtime_environment_invalid",
    )
    temporary = _existing_directory(environment.get("TMPDIR", ""), "runtime_environment_invalid")
    if gradle_home != state_root / "gradle":
        raise CIWError(_DOMAIN, "runtime_environment_invalid")
    result = {
        "PATH": _plain(environment.get("PATH", ""), "runtime_environment_invalid"),
        "HOME": str(home),
        "GRADLE_USER_HOME": str(gradle_home),
        "TMPDIR": str(temporary),
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "TZ": "UTC",
        "CI": "true",
        "GITHUB_ACTIONS": "true",
        "GRADLE_OPTS": "-Dorg.gradle.daemon=false",
    }
    java_home = environment.get("JAVA_HOME", "")
    if java_home:
        result["JAVA_HOME"] = str(_existing_directory(java_home, "java_home_invalid"))
    sdk_raw = environment.get("ANDROID_SDK_ROOT") or environment.get("ANDROID_HOME") or ""
    if sdk_raw:
        sdk = _existing_directory(sdk_raw, "android_sdk_invalid")
        result["ANDROID_SDK_ROOT"] = str(sdk)
        result["ANDROID_HOME"] = str(sdk)
    cache = _read_only_cache(environment)
    if cache is not None:
        if cache == gradle_home:
            raise CIWError(_DOMAIN, "gradle_read_only_cache_invalid")
        result["GRADLE_RO_DEP_CACHE"] = str(cache)
    dependency_raw = environment.get("CI_PRIVATE_DEPENDENCY_PATH", "")
    if dependency_raw:
        dependency = _existing_directory(dependency_raw, "private_dependency_path_invalid")
        dependencies_root = state_root / "dependencies"
        try:
            dependency.relative_to(dependencies_root)
        except ValueError as error:
            raise CIWError(_DOMAIN, "private_dependency_path_invalid") from error
        result["CI_PRIVATE_DEPENDENCY_PATH"] = str(dependency)
    return result


def _diagnostic(result_stdout: str, result_stderr: str, roots: Sequence[Path]) -> None:
    text = "\n".join(part for part in (result_stdout, result_stderr) if part)
    for root in roots:
        text = text.replace(str(root), "<state>")
    text = _URL_CREDENTIAL.sub("https://<redacted>@", text)
    text = _SECRET.sub(r"\1=<redacted>", text)
    text = "\n".join(text.splitlines()[-_MAX_DIAGNOSTIC_LINES:])
    encoded = text.encode("utf-8", errors="replace")
    if len(encoded) > _MAX_DIAGNOSTIC_BYTES:
        text = encoded[-_MAX_DIAGNOSTIC_BYTES:].decode("utf-8", errors="replace")
    if text:
        print("gradle-dependency-warm-diagnostic-begin", file=sys.stderr)
        print(text, file=sys.stderr)
        print("gradle-dependency-warm-diagnostic-end", file=sys.stderr)


def warm_gradle_dependencies(
    *,
    admitted_sha: str,
    working_directory: str,
    gradle_wrapper_path: str,
    environment: Mapping[str, str],
) -> GradleDependencyWarmResult:
    if _FULL_SHA.fullmatch(admitted_sha) is None:
        raise CIWError(_DOMAIN, "admitted_sha_invalid")
    workspace = _existing_directory(environment.get("GITHUB_WORKSPACE", ""), "workspace_invalid")
    state_root = _existing_directory(environment.get("CI_WORKFLOW_ROOT", ""), "workspace_state_invalid")
    if state_root.name != environment.get("CI_WORKFLOW_STATE_ID", ""):
        raise CIWError(_DOMAIN, "workspace_state_invalid")
    source = _bounded_directory(workspace, "source", "source_invalid")
    _verify_exact_source(source, admitted_sha, environment)
    copy = state_root / _COPY_RELATIVE
    _copy_source(source, copy)
    failure: BaseException | None = None
    result: GradleDependencyWarmResult | None = None
    try:
        project = _bounded_directory(
            copy,
            _relative(working_directory, "working_directory_invalid", allow_dot=True),
            "working_directory_invalid",
        )
        wrapper = _bounded_file(
            project,
            _relative(gradle_wrapper_path, "gradle_wrapper_path_invalid", allow_dot=False),
            "gradle_wrapper_path_invalid",
        )
        runtime = _runtime_environment(environment, state_root)
        started = time.monotonic_ns()
        try:
            completed = run_process(
                (
                    str(wrapper),
                    "--no-daemon",
                    "--write-verification-metadata",
                    "sha256",
                ),
                cwd=project,
                environment=runtime,
                timeout_seconds=30 * 60,
            )
        except RuntimePrimitiveError as error:
            raise CIWError(_DOMAIN, "dependency_warm_process_failed") from error
        if completed.timed_out:
            _diagnostic(completed.stdout, completed.stderr, (copy, state_root))
            raise CIWError(_DOMAIN, "dependency_warm_timeout")
        if completed.returncode != 0:
            _diagnostic(completed.stdout, completed.stderr, (copy, state_root))
            raise CIWError(_DOMAIN, "dependency_warm_failed")
        wall_ms = max(0, (time.monotonic_ns() - started) // 1_000_000)
        result = GradleDependencyWarmResult(
            admitted_sha,
            "read-only-seed" if "GRADLE_RO_DEP_CACHE" in runtime else "cold",
            wall_ms,
        )
    except BaseException as error:
        failure = error
    try:
        _remove_no_follow(copy)
        if copy.exists() or copy.is_symlink():
            raise CIWError(_DOMAIN, "warm_cleanup_failed")
        _verify_exact_source(source, admitted_sha, environment)
    except BaseException as cleanup_error:
        if failure is None:
            failure = cleanup_error
    if failure is not None:
        raise failure
    if result is None:
        raise CIWError(_DOMAIN, "dependency_warm_failed")
    return result


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog="gradle-dependency-warm")
    result.add_argument("--admitted-sha")
    result.add_argument("--working-directory", default=".")
    result.add_argument("--gradle-wrapper-path", default="gradlew")
    return result


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        warmed = warm_gradle_dependencies(
            admitted_sha=_plain(args.admitted_sha, "admitted_sha_invalid", maximum=40),
            working_directory=args.working_directory,
            gradle_wrapper_path=args.gradle_wrapper_path,
            environment=os.environ,
        )
        output_path = os.environ.get("GITHUB_OUTPUT", "")
        if not output_path:
            raise CIWError(_DOMAIN, "github_output_missing")
        write_command_file(Path(output_path), warmed.output_values())
        print(
            "gradle-dependency-warm "
            f"cache_mode={warmed.cache_mode} wall_ms={warmed.wall_ms}"
        )
    except CIWError as error:
        print(f"gradle-dependency-warm failed: {error.code}", file=sys.stderr)
        return error.exit_code
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
