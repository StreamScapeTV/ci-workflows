"""Bounded Gradle-to-Maven publication mechanics for reusable CI."""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import time
from typing import Mapping


_SHA = re.compile(r"^[0-9a-f]{40}$")
_SEMVER = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
_PUBLICATION_TASK = re.compile(
    r"^:?(?:[-A-Za-z0-9_.]+:)*publish(?:AllPublications)?To[A-Za-z0-9_.-]+Repository$"
)
_MAX_TASKS = 16
_DEVELOP_BRANCH = "develop"


class GradleMavenPublishError(RuntimeError):
    """Stable, non-secret Gradle Maven publication error."""


@dataclass(frozen=True, slots=True)
class GradleMavenPublishPlan:
    source_root: Path
    working_directory: Path
    wrapper: Path
    version_file: Path
    release_version: str
    tasks: tuple[str, ...]


def _relative(root: Path, value: str, *, allow_dot: bool) -> Path:
    if not isinstance(value, str) or not value or "\\" in value or value.startswith("/"):
        raise GradleMavenPublishError("publication path must be repository-relative")
    if value == ".":
        if not allow_dot:
            raise GradleMavenPublishError("publication path may not be dot")
        return root
    pure = PurePosixPath(value)
    if not pure.parts or any(part in {"", ".", ".."} for part in pure.parts):
        raise GradleMavenPublishError("publication path is not bounded")
    resolved = (root / pure.as_posix()).resolve()
    root_resolved = root.resolve()
    if resolved != root_resolved and root_resolved not in resolved.parents:
        raise GradleMavenPublishError("publication path escaped source root")
    return resolved


def _require_sha(admitted_sha: str) -> str:
    if _SHA.fullmatch(admitted_sha) is None:
        raise GradleMavenPublishError("admitted source must be an exact lowercase SHA")
    return admitted_sha


def parse_publication_tasks(raw: str) -> tuple[str, ...]:
    """Accept only bounded Gradle tasks that publish to one Maven repository."""

    try:
        value = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as error:
        raise GradleMavenPublishError("publication tasks must be valid JSON") from error
    if not isinstance(value, list) or not value or len(value) > _MAX_TASKS:
        raise GradleMavenPublishError("publication tasks must be a non-empty bounded list")
    tasks: list[str] = []
    for item in value:
        if not isinstance(item, str) or _PUBLICATION_TASK.fullmatch(item) is None:
            raise GradleMavenPublishError("publication task is invalid")
        tasks.append(item)
    if len(set(tasks)) != len(tasks):
        raise GradleMavenPublishError("publication tasks must be unique")
    return tuple(tasks)


def derive_release_version(
    base_version: str,
    *,
    github_ref: str,
    expected_branch: str,
    admitted_sha: str,
) -> str:
    """Derive the only supported Maven version from the exact caller ref."""

    if _SEMVER.fullmatch(base_version) is None:
        raise GradleMavenPublishError("VERSION must contain stable MAJOR.MINOR.PATCH")
    _require_sha(admitted_sha)
    if expected_branch != _DEVELOP_BRANCH:
        raise GradleMavenPublishError("expected branch must be develop")
    if github_ref == "refs/heads/develop":
        return f"{base_version}-develop.{admitted_sha[:12]}"
    if github_ref == f"refs/tags/v{base_version}":
        return base_version
    raise GradleMavenPublishError(
        "publication ref is neither develop nor the exact stable VERSION tag"
    )


def resolve_plan(
    *,
    source_root: Path,
    admitted_sha: str,
    github_ref: str,
    expected_branch: str,
    working_directory: str,
    gradle_wrapper_path: str,
    version_file: str,
    arguments_json: str,
) -> GradleMavenPublishPlan:
    """Validate caller-owned hooks without accepting commands or registry endpoints."""

    root = source_root.resolve()
    if not root.is_dir() or root.is_symlink():
        raise GradleMavenPublishError("exact caller source root is missing")
    work = _relative(root, working_directory, allow_dot=True)
    if not work.is_dir() or work.is_symlink():
        raise GradleMavenPublishError("Gradle publication working directory is invalid")
    wrapper = _relative(work, gradle_wrapper_path, allow_dot=False)
    if not wrapper.is_file() or wrapper.is_symlink():
        raise GradleMavenPublishError("checked-in Gradle wrapper is missing")
    version = _relative(root, version_file, allow_dot=False)
    if not version.is_file() or version.is_symlink():
        raise GradleMavenPublishError("VERSION file is missing")
    release_version = derive_release_version(
        version.read_text(encoding="utf-8").strip(),
        github_ref=github_ref,
        expected_branch=expected_branch,
        admitted_sha=admitted_sha,
    )
    return GradleMavenPublishPlan(
        source_root=root,
        working_directory=work,
        wrapper=wrapper,
        version_file=version,
        release_version=release_version,
        tasks=parse_publication_tasks(arguments_json),
    )


def publish(
    plan: GradleMavenPublishPlan,
    *,
    environment: Mapping[str, str],
    registry_username: str,
    registry_token: str,
) -> int:
    """Run exactly one bounded Gradle publication process."""

    if not registry_username or not registry_token:
        raise GradleMavenPublishError("Maven registry credentials are required")
    runtime = dict(environment)
    runtime.pop("INPUT_REGISTRY_USERNAME", None)
    runtime.pop("INPUT_REGISTRY_TOKEN", None)
    runtime["FORGEJO_REGISTRY_USERNAME"] = registry_username
    runtime["FORGEJO_REGISTRY_TOKEN"] = registry_token
    runtime["CI_MAVEN_PUBLICATION_VERSION"] = plan.release_version
    command = (
        str(plan.wrapper),
        "--no-daemon",
        f"-PciMavenPublicationVersion={plan.release_version}",
        *plan.tasks,
    )
    started = time.monotonic()
    result = subprocess.run(command, cwd=plan.working_directory, env=runtime, check=False)
    wall_ms = int((time.monotonic() - started) * 1000)
    if result.returncode != 0:
        raise GradleMavenPublishError(
            f"Gradle Maven publication failed with exit code {result.returncode}"
        )
    return wall_ms


def _git(source_root: Path, *arguments: str) -> str:
    result = subprocess.run(
        ("git", *arguments),
        cwd=source_root,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise GradleMavenPublishError("exact caller source verification failed")
    return result.stdout


def _verify_admitted_source(source_root: Path, admitted_sha: str) -> None:
    if not source_root.is_dir() or source_root.is_symlink():
        raise GradleMavenPublishError("exact caller source root is missing")
    if _git(source_root, "rev-parse", "HEAD").strip() != _require_sha(admitted_sha):
        raise GradleMavenPublishError("exact caller source changed")


def cleanup_source(source_root: Path, *, admitted_sha: str) -> None:
    """Remove ignored Gradle build state from the disposable exact checkout."""

    root = source_root.resolve()
    _verify_admitted_source(root, admitted_sha)
    _git(root, "clean", "-ffdX")


def verify_source_clean(source_root: Path, *, admitted_sha: str) -> None:
    """Require exact HEAD and no tracked, untracked, or ignored build residue."""

    root = source_root.resolve()
    _verify_admitted_source(root, admitted_sha)
    if _git(root, "status", "--porcelain=v1", "--untracked-files=all"):
        raise GradleMavenPublishError("exact caller source is not clean")
    if _git(root, "clean", "-ffdX", "--dry-run"):
        raise GradleMavenPublishError("ignored publication residue remains")


def append_github_outputs(path: str | None, **values: str | int) -> None:
    if not path:
        return
    with Path(path).open("a", encoding="utf-8") as handle:
        for name, value in values.items():
            handle.write(f"{name}={value}\n")


def _plan_from_environment(environment: Mapping[str, str]) -> GradleMavenPublishPlan:
    return resolve_plan(
        source_root=Path(environment.get("INPUT_SOURCE_ROOT", "source")),
        admitted_sha=environment.get("INPUT_ADMITTED_SHA", ""),
        github_ref=environment.get("GITHUB_REF", ""),
        expected_branch=environment.get("INPUT_EXPECTED_BRANCH", ""),
        working_directory=environment.get("INPUT_WORKING_DIRECTORY", "."),
        gradle_wrapper_path=environment.get("INPUT_GRADLE_WRAPPER_PATH", "gradlew"),
        version_file=environment.get("INPUT_VERSION_FILE", "VERSION"),
        arguments_json=environment.get("INPUT_ARGUMENTS_JSON", ""),
    )


def main(environment: Mapping[str, str] | None = None) -> int:
    """Dispatch one internal action phase without logging credential material."""

    runtime = os.environ if environment is None else environment
    phase = runtime.get("INPUT_PHASE", "execute")
    try:
        if phase == "plan":
            plan = _plan_from_environment(runtime)
            append_github_outputs(
                runtime.get("GITHUB_OUTPUT"), release_version=plan.release_version
            )
        elif phase == "execute":
            plan = _plan_from_environment(runtime)
            wall_ms = publish(
                plan,
                environment=runtime,
                registry_username=runtime.get("INPUT_REGISTRY_USERNAME", ""),
                registry_token=runtime.get("INPUT_REGISTRY_TOKEN", ""),
            )
            append_github_outputs(
                runtime.get("GITHUB_OUTPUT"),
                release_version=plan.release_version,
                gradle_wall_ms=wall_ms,
            )
            print(
                f"gradle-maven-publication version={plan.release_version} wall_ms={wall_ms}"
            )
        elif phase == "cleanup":
            cleanup_source(
                Path(runtime.get("INPUT_SOURCE_ROOT", "source")),
                admitted_sha=runtime.get("INPUT_ADMITTED_SHA", ""),
            )
        elif phase == "residue":
            verify_source_clean(
                Path(runtime.get("INPUT_SOURCE_ROOT", "source")),
                admitted_sha=runtime.get("INPUT_ADMITTED_SHA", ""),
            )
        else:
            raise GradleMavenPublishError("publication phase is invalid")
        return 0
    except (OSError, UnicodeError, GradleMavenPublishError) as error:
        print(f"gradle-maven-publication failed: {error}", file=os.sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
