"""Checked-in Python validation contract, trust, and plan resolution."""
from __future__ import annotations

import json
import re
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

from .foundation_types import stable_identifier
from .python_types import (
    PythonCommand,
    PythonValidationError,
    PythonValidationPlan,
    PythonValidationRequest,
)

CONTRACT_PATH = Path("contracts/python-validation.json")
FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
EXACT_VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
VERSION_FAMILY = re.compile(r"^[0-9]+\.[0-9]+$")
DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
IMAGE_REPOSITORY = re.compile(r"^docker\.io/library/[a-z0-9][a-z0-9._-]*$")
REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
ENVIRONMENT_NAME = re.compile(r"^[A-Z][A-Z0-9_]{1,63}$")
TOKEN_LIKE = re.compile(
    r"(?:gh[pousr]_[A-Za-z0-9_]{24,}|github_pat_[A-Za-z0-9_]{40,}|"
    r"AKIA[0-9A-Z]{16}|eyJ[A-Za-z0-9_-]{12,}\.[A-Za-z0-9_-]{12,}\.)"
)
PROFILE_RUNNERS = {
    "audit": "portable",
    "host": "portable",
    "podman": "buildah-high",
    "podman-postgres": "buildah-medium",
}
PROFILE_TIMEOUTS = {
    "audit": 15,
    "host": 30,
    "podman": 60,
    "podman-postgres": 90,
}
FAILURE_CODES = {
    "invalid_input",
    "unsupported_profile",
    "source_mismatch",
    "python_version_drift",
    "dependency_lock_drift",
    "dependency_restore_failed",
    "toolchain_mismatch",
    "isolation_unavailable",
    "postgres_readiness_timeout",
    "command_failed",
    "dirty_tree",
    "generated_drift",
    "policy_failed",
    "artifact_policy_failed",
    "cleanup_failed",
}
REQUIRED_FORBIDDEN_INPUTS = {
    "arbitrary_command",
    "shell",
    "callback",
    "runner",
    "runs_on",
    "runner_labels",
    "container_engine",
    "storage_driver",
    "service_image",
    "database_url",
    "registry_host",
    "secret_name",
    "release_version",
    "helm_chart",
    "flux_target",
    "cluster",
    "namespace",
    "deployment_operation",
}


def require(condition: bool, code: str) -> None:
    if not condition:
        raise PythonValidationError(code)


def _read_json(path: Path) -> Mapping[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise PythonValidationError("invalid_input") from error
    require(isinstance(value, Mapping), "invalid_input")
    return value


def _strings(value: Any, *, empty: bool = True) -> list[str]:
    require(isinstance(value, list), "invalid_input")
    require(all(isinstance(item, str) and item for item in value), "invalid_input")
    require(empty or bool(value), "invalid_input")
    return list(value)


def safe_relative(value: Any, code: str = "invalid_input") -> str:
    require(isinstance(value, str), code)
    candidate = value.strip()
    path = PurePosixPath(candidate)
    require(
        bool(candidate)
        and not path.is_absolute()
        and ".." not in path.parts
        and "\\" not in candidate
        and all(part not in {"", "."} for part in path.parts),
        code,
    )
    return path.as_posix()


def _optional_relative(value: Any) -> str | None:
    return None if value in {None, ""} else safe_relative(value)


def runtime_reference(runtime: Mapping[str, Any]) -> str | None:
    if runtime.get("kind") == "host":
        require(runtime.get("implementation") == "cpython", "toolchain_mismatch")
        require(
            EXACT_VERSION.fullmatch(str(runtime.get("python_version", ""))) is not None,
            "toolchain_mismatch",
        )
        return None
    repository = runtime.get("repository")
    tag = runtime.get("tag")
    digest = runtime.get("digest")
    require(
        isinstance(repository, str)
        and IMAGE_REPOSITORY.fullmatch(repository) is not None
        and isinstance(tag, str)
        and tag
        and "latest" not in tag.casefold()
        and isinstance(digest, str)
        and DIGEST.fullmatch(digest) is not None,
        "toolchain_mismatch",
    )
    return f"{repository}:{tag}@{digest}"


def _validate_profile_contract(payload: Mapping[str, Any]) -> None:
    profiles = payload.get("profiles")
    require(isinstance(profiles, Mapping), "invalid_input")
    require(set(profiles) == set(PROFILE_RUNNERS), "invalid_input")
    for name, profile in profiles.items():
        require(isinstance(profile, Mapping), "invalid_input")
        require(profile.get("runner_profile") == PROFILE_RUNNERS[name], "invalid_input")
        require(profile.get("timeout_minutes") == PROFILE_TIMEOUTS[name], "invalid_input")
        require(isinstance(profile.get("postgres"), bool), "invalid_input")
        require(bool(_strings(profile.get("allowed_source_trust"), empty=False)), "invalid_input")
    require(profiles["podman-postgres"]["postgres"] is True, "invalid_input")
    require(
        all(profiles[name]["postgres"] is False for name in ("audit", "host", "podman")),
        "invalid_input",
    )


def _validate_runtime_contract(payload: Mapping[str, Any]) -> None:
    runtimes = payload.get("runtimes")
    require(isinstance(runtimes, Mapping), "toolchain_mismatch")
    expected = {
        "host-cpython-3.12.13",
        "python-3.12.8-slim-amd64",
        "python-3.12.13-slim-amd64",
        "postgres-16.11-alpine-amd64",
    }
    require(set(runtimes) == expected, "toolchain_mismatch")
    for runtime in runtimes.values():
        require(isinstance(runtime, Mapping), "toolchain_mismatch")
        runtime_reference(runtime)
    require(
        runtimes["python-3.12.8-slim-amd64"].get("python_version") == "3.12.8",
        "toolchain_mismatch",
    )
    require(
        runtimes["python-3.12.13-slim-amd64"].get("python_version") == "3.12.13",
        "toolchain_mismatch",
    )
    require(
        runtimes["postgres-16.11-alpine-amd64"].get("postgres_major") == 16,
        "toolchain_mismatch",
    )


def _validate_consumer_profile(
    payload: Mapping[str, Any],
    command_profile: str,
    value: Mapping[str, Any],
) -> None:
    command_contracts = payload["command_profiles"]
    validation_profile = value.get("validation_profile")
    require(command_profile in command_contracts, "invalid_input")
    require(validation_profile in payload["profiles"], "unsupported_profile")
    require(
        validation_profile in command_contracts[command_profile]["allowed_profiles"],
        "unsupported_profile",
    )
    require(value.get("runtime") in payload["runtimes"], "toolchain_mismatch")
    working = str(value.get("working_directory", "."))
    require(working == "." or safe_relative(working) == working, "invalid_input")
    for field in ("version_file", "dependency_file", "script_path"):
        _optional_relative(value.get(field))
    database_name = value.get("database_environment_variable")
    require(
        database_name is None
        or isinstance(database_name, str)
        and ENVIRONMENT_NAME.fullmatch(database_name) is not None,
        "invalid_input",
    )
    environment = value.get("environment")
    require(isinstance(environment, Mapping), "invalid_input")
    for name, raw in environment.items():
        require(
            isinstance(name, str)
            and ENVIRONMENT_NAME.fullmatch(name) is not None
            and isinstance(raw, str)
            and "\n" not in raw
            and "\r" not in raw
            and TOKEN_LIKE.search(raw) is None,
            "invalid_input",
        )
    rows = value.get("commands")
    require(isinstance(rows, list) and rows, "invalid_input")
    for row in rows:
        require(isinstance(row, Mapping) and set(row) == {"stage", "argv"}, "invalid_input")
        require(row.get("stage") in command_contracts[command_profile]["stages"], "invalid_input")
        argv = _strings(row.get("argv"), empty=False)
        require(all("\n" not in item and "\r" not in item for item in argv), "invalid_input")


def load_python_contract(root: Path) -> Mapping[str, Any]:
    """Load and validate the complete checked-in Python validation contract."""

    payload = _read_json(root / CONTRACT_PATH)
    require(payload.get("schema_version") == 1, "invalid_input")
    require(payload.get("contract_version") == "1.0.0", "invalid_input")
    require(payload.get("organization") == "StreamScapeTV", "invalid_input")
    require(payload.get("workflow_api") == "validation.python", "invalid_input")
    require(payload.get("stable_check_name") == "CI / Python validation", "invalid_input")
    require(payload.get("hard_timeout_minutes") == 120, "invalid_input")
    require(payload.get("cache_mode") == "disabled", "invalid_input")
    require(payload.get("artifact_policy") == "zero-default", "invalid_input")
    _validate_profile_contract(payload)
    _validate_runtime_contract(payload)
    command_profiles = payload.get("command_profiles")
    require(isinstance(command_profiles, Mapping), "invalid_input")
    require(
        set(command_profiles)
        == {"source-audit", "locked-test", "full-test", "postgres-test", "release-contract"},
        "invalid_input",
    )
    for command in command_profiles.values():
        require(isinstance(command, Mapping), "invalid_input")
        require(command.get("script_path_mode") == "contract-fixed-only", "invalid_input")
        require(set(_strings(command.get("allowed_profiles"), empty=False)) <= set(PROFILE_RUNNERS), "invalid_input")
        _strings(command.get("stages"), empty=False)
    consumers = payload.get("consumers")
    require(isinstance(consumers, Mapping), "invalid_input")
    require(
        set(consumers)
        == {"StreamScapeTV/iptv-backend", "StreamScapeTV/agent-state", "StreamScapeTV/flux"},
        "invalid_input",
    )
    for repository, consumer in consumers.items():
        require(REPOSITORY.fullmatch(repository) is not None, "invalid_input")
        require(isinstance(consumer, Mapping), "invalid_input")
        profiles = consumer.get("profiles")
        require(isinstance(profiles, Mapping) and profiles, "invalid_input")
        for name, value in profiles.items():
            require(isinstance(value, Mapping), "invalid_input")
            _validate_consumer_profile(payload, str(name), value)
    require(set(_strings(payload.get("failure_codes"), empty=False)) == FAILURE_CODES, "invalid_input")
    require(REQUIRED_FORBIDDEN_INPUTS <= set(_strings(payload.get("forbidden_inputs"), empty=False)), "invalid_input")
    cleanup = payload.get("cleanup")
    require(
        isinstance(cleanup, Mapping)
        and cleanup
        and all(value is True for value in cleanup.values()),
        "invalid_input",
    )
    postgres = payload.get("postgres")
    require(
        isinstance(postgres, Mapping)
        and postgres.get("remote_fallback") is False
        and postgres.get("credentials") == "ephemeral-per-execution"
        and postgres.get("readiness_attempts") == 30
        and postgres.get("readiness_interval_seconds") == 2,
        "invalid_input",
    )
    return payload


def source_trust_from_environment(environment: Mapping[str, str]) -> str:
    """Derive source trust from immutable GitHub event context."""

    if environment.get("GITHUB_EVENT_NAME") != "pull_request":
        return "trusted-exact"
    try:
        payload = json.loads(Path(environment.get("GITHUB_EVENT_PATH", "")).read_text(encoding="utf-8"))
        head_repository = payload["pull_request"]["head"]["repo"]["full_name"]
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as error:
        raise PythonValidationError("invalid_input") from error
    require(isinstance(head_repository, str) and head_repository, "invalid_input")
    return "trusted-pr" if head_repository == environment.get("GITHUB_REPOSITORY") else "untrusted-fork"


def request_from_environment(environment: Mapping[str, str]) -> PythonValidationRequest:
    """Build a typed request from bounded action inputs and GitHub identity."""

    repository = environment.get("GITHUB_REPOSITORY", "").strip()
    admitted_sha = environment.get("INPUT_ADMITTED_SHA", "").strip()
    validation_profile = environment.get("INPUT_VALIDATION_PROFILE", "").strip()
    command_profile = environment.get("INPUT_COMMAND_PROFILE", "").strip()
    working = environment.get("INPUT_WORKING_DIRECTORY", ".").strip() or "."
    require(REPOSITORY.fullmatch(repository) is not None, "invalid_input")
    require(FULL_SHA.fullmatch(admitted_sha) is not None, "invalid_input")
    require(validation_profile and command_profile, "invalid_input")
    artifact = environment.get("INPUT_ARTIFACT_EXCEPTION_ID", "").strip() or None
    require(artifact is None, "artifact_policy_failed")
    return PythonValidationRequest(
        repository=repository,
        admitted_sha=admitted_sha,
        validation_profile=validation_profile,
        command_profile=command_profile,
        working_directory="." if working == "." else safe_relative(working),
        version_file=_optional_relative(environment.get("INPUT_VERSION_FILE", "").strip() or None),
        script_path=_optional_relative(environment.get("INPUT_SCRIPT_PATH", "").strip() or None),
        artifact_exception_id=artifact,
        source_trust=source_trust_from_environment(environment),
    )


def resolve_validation_plan(
    contract: Mapping[str, Any],
    request: PythonValidationRequest,
) -> PythonValidationPlan:
    """Resolve one contract-owned plan without caller-selected behavior."""

    profiles = contract["profiles"]
    require(request.validation_profile in profiles, "unsupported_profile")
    profile = profiles[request.validation_profile]
    require(request.source_trust in profile["allowed_source_trust"], "unsupported_profile")
    command_profiles = contract["command_profiles"]
    require(request.command_profile in command_profiles, "unsupported_profile")
    require(
        request.validation_profile in command_profiles[request.command_profile]["allowed_profiles"],
        "unsupported_profile",
    )
    require(request.repository in contract["consumers"], "unsupported_profile")
    mappings = contract["consumers"][request.repository]["profiles"]
    require(request.command_profile in mappings, "unsupported_profile")
    consumer = mappings[request.command_profile]
    require(consumer["validation_profile"] == request.validation_profile, "unsupported_profile")
    require(request.working_directory == consumer.get("working_directory", "."), "invalid_input")
    if request.version_file is not None:
        require(request.version_file == consumer.get("version_file"), "invalid_input")
    if request.script_path is not None:
        require(request.script_path == consumer.get("script_path"), "invalid_input")
    command_contract = command_profiles[request.command_profile]
    require(
        bool(consumer.get("dependency_file")) == bool(command_contract["dependency_required"]),
        "dependency_lock_drift",
    )
    require(
        bool(consumer.get("database_environment_variable"))
        == bool(command_contract["postgres_required"]),
        "invalid_input",
    )
    runtime_id = str(consumer["runtime"])
    runtime = contract["runtimes"][runtime_id]
    postgres_reference = None
    if profile["postgres"]:
        postgres_reference = runtime_reference(contract["runtimes"][contract["postgres"]["runtime"]])
        require(postgres_reference is not None, "toolchain_mismatch")
    return PythonValidationPlan(
        repository=request.repository,
        admitted_sha=request.admitted_sha,
        validation_profile=request.validation_profile,
        command_profile=request.command_profile,
        runner_profile=str(profile["runner_profile"]),
        timeout_minutes=int(profile["timeout_minutes"]),
        workspace_profile=str(profile["workspace_profile"]),
        isolation=str(profile["isolation"]),
        runtime_id=runtime_id,
        runtime_reference=runtime_reference(runtime),
        python_version=str(runtime["python_version"]),
        working_directory=request.working_directory,
        version_file=consumer.get("version_file"),
        dependency_file=consumer.get("dependency_file"),
        script_path=consumer.get("script_path"),
        database_environment_variable=consumer.get("database_environment_variable"),
        environment=dict(consumer["environment"]),
        commands=tuple(
            PythonCommand(str(row["stage"]), tuple(str(item) for item in row["argv"]))
            for row in consumer["commands"]
        ),
        postgres_runtime_reference=postgres_reference,
        readiness_attempts=int(contract["postgres"]["readiness_attempts"]),
        readiness_interval_seconds=int(contract["postgres"]["readiness_interval_seconds"]),
    )


def bounded_path(root: Path, relative: str) -> Path:
    resolved_root = root.resolve()
    if relative == ".":
        return resolved_root
    target = (resolved_root / Path(*PurePosixPath(relative).parts)).resolve(strict=False)
    require(resolved_root in target.parents, "invalid_input")
    current = resolved_root
    for part in target.relative_to(resolved_root).parts:
        current /= part
        require(not (current.exists() and current.is_symlink()), "invalid_input")
    return target


def resolve_python_version(source_root: Path, plan: PythonValidationPlan) -> str:
    """Verify an optional consumer-owned exact or major/minor version file."""

    expected = plan.python_version
    require(EXACT_VERSION.fullmatch(expected) is not None, "python_version_drift")
    if plan.version_file is None:
        return expected
    path = bounded_path(source_root, plan.version_file)
    require(path.is_file() and not path.is_symlink(), "python_version_drift")
    try:
        value = path.read_text(encoding="utf-8").strip()
    except OSError as error:
        raise PythonValidationError("python_version_drift") from error
    require("\n" not in value and "\r" not in value, "python_version_drift")
    require(
        EXACT_VERSION.fullmatch(value) is not None
        or VERSION_FAMILY.fullmatch(value) is not None,
        "python_version_drift",
    )
    if EXACT_VERSION.fullmatch(value):
        require(value == expected, "python_version_drift")
    else:
        require(expected.startswith(value + "."), "python_version_drift")
    return expected


def _lock_material(path: Path, source_root: Path, visited: set[Path]) -> str:
    resolved = path.resolve()
    require(source_root.resolve() in resolved.parents, "dependency_lock_drift")
    require(resolved not in visited, "dependency_lock_drift")
    visited.add(resolved)
    require(path.is_file() and not path.is_symlink(), "dependency_lock_drift")
    try:
        content = path.read_text(encoding="utf-8")
    except OSError as error:
        raise PythonValidationError("dependency_lock_drift") from error
    rows = [
        line.strip()
        for line in content.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    require(bool(rows), "dependency_lock_drift")
    material = [f"{path.relative_to(source_root).as_posix()}\n{content}"]
    for row in rows:
        lowered = row.casefold()
        if lowered.startswith("-r ") or lowered.startswith("--requirement "):
            parts = row.split(maxsplit=1)
            require(len(parts) == 2, "dependency_lock_drift")
            include = bounded_path(path.parent, safe_relative(parts[1], "dependency_lock_drift"))
            material.append(_lock_material(include, source_root, visited))
            continue
        require(
            "git+" not in lowered
            and not lowered.startswith("-e ")
            and not lowered.startswith("--editable")
            and (
                "==" in row
                or ("--hash=sha256:" in row and ("https://" in row or "http://" in row))
            ),
            "dependency_lock_drift",
        )
    return "\n".join(material)


def validate_dependency_lock(source_root: Path, plan: PythonValidationPlan) -> str | None:
    """Require exact pins, including bounded recursive requirements includes."""

    if plan.dependency_file is None:
        return None
    material = _lock_material(
        bounded_path(source_root, plan.dependency_file),
        source_root.resolve(),
        set(),
    )
    return stable_identifier(
        "python-lock",
        {"path": plan.dependency_file, "content": material},
        length=32,
    )
