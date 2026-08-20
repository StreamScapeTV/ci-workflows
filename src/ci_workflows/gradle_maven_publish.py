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
_BRANCH = re.compile(r"^[A-Za-z0-9._/-]{1,255}$")
_TASK = re.compile(r"^:?[-A-Za-z0-9_.]+(?::[-A-Za-z0-9_.]+)*$")
_MAX_TASKS = 16


class GradleMavenPublishError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class GradleMavenPublishPlan:
    source_root: Path
    working_directory: Path
    wrapper: Path
    version_file: Path
    release_version: str
    tasks: tuple[str, ...]


def _relative(root: Path, value: str, *, allow_dot: bool) -> Path:
    if not value or "\\" in value or value.startswith("/"):
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


def parse_publication_tasks(raw: str) -> tuple[str, ...]:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as error:
        raise GradleMavenPublishError("publication tasks must be valid JSON") from error
    if not isinstance(value, list) or not value or len(value) > _MAX_TASKS:
        raise GradleMavenPublishError("publication tasks must be a non-empty bounded list")
    tasks: list[str] = []
    for item in value:
        if not isinstance(item, str) or _TASK.fullmatch(item) is None or item.startswith("-"):
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
    if _SEMVER.fullmatch(base_version) is None:
        raise GradleMavenPublishError("VERSION must contain stable MAJOR.MINOR.PATCH")
    if _SHA.fullmatch(admitted_sha) is None:
        raise GradleMavenPublishError("admitted source must be an exact lowercase SHA")
    if _BRANCH.fullmatch(expected_branch) is None or expected_branch.startswith("/") or ".." in expected_branch.split("/"):
        raise GradleMavenPublishError("expected branch is invalid")
    if github_ref == f"refs/heads/{expected_branch}":
        return f"{base_version}-develop.{admitted_sha[:12]}"
    if github_ref == f"refs/tags/v{base_version}":
        return base_version
    raise GradleMavenPublishError("publication ref is neither the expected development branch nor the exact stable tag")


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
    root = source_root.resolve()
    if not root.is_dir():
        raise GradleMavenPublishError("exact caller source root is missing")
    work = _relative(root, working_directory, allow_dot=True)
    if not work.is_dir() or work.is_symlink():
        raise GradleMavenPublishError("Gradle publication working directory is invalid")
    wrapper = _relative(work, gradle_wrapper_path, allow_dot=False)
    if not wrapper.is_file() or wrapper.is_symlink():
        raise GradleMavenPublishError("checked-in Gradle wrapper is missing")
    versions = _relative(root, version_file, allow_dot=False)
    if not versions.is_file() or versions.is_symlink():
        raise GradleMavenPublishError("VERSION file is missing")
    base_version = versions.read_text(encoding="utf-8").strip()
    release_version = derive_release_version(
        base_version,
        github_ref=github_ref,
        expected_branch=expected_branch,
        admitted_sha=admitted_sha,
    )
    tasks = parse_publication_tasks(arguments_json)
    return GradleMavenPublishPlan(root, work, wrapper, versions, release_version, tasks)


def publish(
    plan: GradleMavenPublishPlan,
    *,
    environment: Mapping[str, str],
    registry_username: str,
    registry_token: str,
) -> int:
    if not registry_username or not registry_token:
        raise GradleMavenPublishError("Maven registry credentials are required")
    runtime = dict(environment)
    runtime["CIW_MAVEN_REGISTRY_USERNAME"] = registry_username
    runtime["CIW_MAVEN_REGISTRY_TOKEN"] = registry_token
    runtime["CI_MAVEN_PUBLICATION_VERSION"] = plan.release_version
    command = [
        str(plan.wrapper),
        "--no-daemon",
        f"-PciMavenPublicationVersion={plan.release_version}",
        *plan.tasks,
    ]
    started = time.monotonic()
    result = subprocess.run(command, cwd=plan.working_directory, env=runtime, check=False)
    wall_ms = int((time.monotonic() - started) * 1000)
    if result.returncode != 0:
        raise GradleMavenPublishError(f"Gradle Maven publication failed with exit code {result.returncode}")
    return wall_ms


def append_github_outputs(path: str | None, *, release_version: str, wall_ms: int) -> None:
    if not path:
        return
    with Path(path).open("a", encoding="utf-8") as handle:
        handle.write(f"release_version={release_version}\n")
        handle.write(f"gradle_wall_ms={wall_ms}\n")


def main() -> int:
    try:
        plan = resolve_plan(
            source_root=Path(os.environ.get("INPUT_SOURCE_ROOT", "source")),
            admitted_sha=os.environ.get("INPUT_ADMITTED_SHA", ""),
            github_ref=os.environ.get("GITHUB_REF", ""),
            expected_branch=os.environ.get("INPUT_EXPECTED_BRANCH", ""),
            working_directory=os.environ.get("INPUT_WORKING_DIRECTORY", "."),
            gradle_wrapper_path=os.environ.get("INPUT_GRADLE_WRAPPER_PATH", "gradlew"),
            version_file=os.environ.get("INPUT_VERSION_FILE", "VERSION"),
            arguments_json=os.environ.get("INPUT_ARGUMENTS_JSON", ""),
        )
        wall_ms = publish(
            plan,
            environment=os.environ,
            registry_username=os.environ.get("CIW_MAVEN_REGISTRY_USERNAME", ""),
            registry_token=os.environ.get("CIW_MAVEN_REGISTRY_TOKEN", ""),
        )
        append_github_outputs(
            os.environ.get("GITHUB_OUTPUT"),
            release_version=plan.release_version,
            wall_ms=wall_ms,
        )
        print(f"gradle-maven-publication version={plan.release_version} wall_ms={wall_ms}")
        return 0
    except (OSError, UnicodeError, GradleMavenPublishError) as error:
        print(f"gradle-maven-publication failed: {error}", file=os.sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
