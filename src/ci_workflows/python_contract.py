"Checked-in product-neutral contract for reusable Python validation."

from __future__ import annotations

import json
import os
import re
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence

from .foundation_types import stable_identifier
from .python_types import PythonValidationError, PythonValidationPlan, PythonValidationRequest

CONTRACT_PATH = "contracts/python-validation.json"
FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
EXACT_VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
VERSION_FAMILY = re.compile(r"^[0-9]+\.[0-9]+$")
IMAGE_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
FAILURE_CODES = {
    "artifact_policy_failed", "cleanup_failed", "command_failed",
    "dependency_lock_drift", "dependency_restore_failed", "dirty_tree",
    "invalid_input", "isolation_unavailable", "policy_failed",
    "postgres_readiness_timeout", "python_version_drift", "source_mismatch",
    "toolchain_mismatch", "unsupported_profile",
}
REQUIRED_FORBIDDEN_INPUTS = {
    "arbitrary_command", "arguments", "arguments_json", "callback", "command",
    "command_profile", "container_engine", "database_environment_variable",
    "database_password", "database_url", "database_url_scheme", "environment",
    "environment_json", "function_name", "handler", "module_name", "python_image",
    "runner", "runner_labels", "runs_on", "secret_name", "service_image", "shell",
    "storage_driver",
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


def _strings(value: Any, *, empty: bool = True) -> tuple[str, ...]:
    require(isinstance(value, list), "invalid_input")
    require(all(isinstance(item, str) and item for item in value), "invalid_input")
    require(len(value) == len(set(value)), "invalid_input")
    if not empty:
        require(bool(value), "invalid_input")
    return tuple(value)


def safe_relative(value: str, code: str = "invalid_input") -> str:
    require(isinstance(value, str) and bool(value), code)
    require("\\" not in value and not value.startswith("/"), code)
    if value == ".":
        return value
    path = PurePosixPath(value)
    require(
        not path.is_absolute()
        and all(part not in {"", ".", ".."} for part in path.parts),
        code,
    )
    normalized = path.as_posix()
    require(normalized == value and len(normalized) <= 240, code)
    return normalized


def _optional_relative(value: Any) -> str | None:
    if value is None:
        return None
    require(isinstance(value, str) and bool(value), "invalid_input")
    return safe_relative(value)


def runtime_reference(runtime: Mapping[str, Any]) -> str | None:
    kind = runtime.get("kind")
    if kind == "host":
        return None
    repository = runtime.get("repository")
    tag = runtime.get("tag")
    digest = runtime.get("digest")
    require(
        kind in {"container", "postgres"}
        and isinstance(repository, str)
        and repository.startswith("docker.io/library/")
        and isinstance(tag, str)
        and tag != "latest"
        and isinstance(digest, str)
        and IMAGE_DIGEST.fullmatch(digest) is not None
        and runtime.get("platform") == "linux/amd64",
        "toolchain_mismatch",
    )
    return f"{repository}:{tag}@{digest}"


def _validate_profiles(payload: Mapping[str, Any]) -> None:
    profiles = payload.get("profiles")
    require(isinstance(profiles, Mapping), "invalid_input")
    require(set(profiles) == {"audit", "host", "podman", "podman-postgres"}, "invalid_input")
    for name, value in profiles.items():
        require(isinstance(value, Mapping), "invalid_input")
        require(value.get("runner_profile") in {"portable", "buildah-high", "buildah-medium"}, "invalid_input")
        require(type(value.get("timeout_minutes")) is int and 1 <= int(value["timeout_minutes"]) < 120, "invalid_input")
        require(value.get("workspace_profile") in {"minimal", "container"}, "invalid_input")
        require(value.get("isolation") in {"copied-host-source", "podman-vfs", "podman-vfs-postgres"}, "invalid_input")
        require(value.get("dependency_mode") in {"none", "locked-optional"}, "invalid_input")
        require(value.get("runtime_kind") in {"host", "container"}, "invalid_input")
        _strings(value.get("allowed_source_trust"), empty=False)
        require(type(value.get("postgres")) is bool, "invalid_input")
        if name == "podman-postgres":
            require(value["postgres"] is True, "invalid_input")
        else:
            require(value["postgres"] is False, "invalid_input")


def _validate_runtimes(payload: Mapping[str, Any]) -> None:
    runtimes = payload.get("runtimes")
    require(isinstance(runtimes, Mapping), "toolchain_mismatch")
    expected = {"host-cpython-3.12", "python-3.12.8-slim-amd64", "python-3.12.13-slim-amd64", "postgres-16.11-alpine-amd64"}
    require(set(runtimes) == expected, "toolchain_mismatch")
    for runtime in runtimes.values():
        require(isinstance(runtime, Mapping), "toolchain_mismatch")
        runtime_reference(runtime)
    host = runtimes["host-cpython-3.12"]
    require(host.get("python_version") == "3.12" and host.get("platforms") == ["linux/x64"], "toolchain_mismatch")
    versions = payload.get("python_versions")
    require(isinstance(versions, Mapping), "toolchain_mismatch")
    require(versions.get("host") == ["3.12"], "toolchain_mismatch")
    require(versions.get("container") == ["3.12.8", "3.12.13"], "toolchain_mismatch")
    for value in versions["container"]:
        matches = [runtime for runtime in runtimes.values() if runtime.get("kind") == "container" and runtime.get("python_version") == value]
        require(len(matches) == 1, "toolchain_mismatch")


def load_python_contract(root: Path) -> Mapping[str, Any]:
    payload = _read_json(root / CONTRACT_PATH)
    require(payload.get("schema_version") == 1, "invalid_input")
    require(payload.get("contract_version") == "2.0.0", "invalid_input")
    require(payload.get("organization") == "StreamScapeTV", "invalid_input")
    require(payload.get("workflow_api") == "validation.python", "invalid_input")
    require(payload.get("stable_check_name") == "CI / Python validation", "invalid_input")
    require(payload.get("hard_timeout_minutes") == 120, "invalid_input")
    require(payload.get("cache_mode") == "disabled" and payload.get("artifact_policy") == "zero-default", "invalid_input")
    require("consumers" not in payload and "command_profiles" not in payload, "invalid_input")
    _validate_profiles(payload)
    _validate_runtimes(payload)
    script = payload.get("script_contract")
    require(
        isinstance(script, Mapping)
        and script.get("path_mode") == "repository-relative-executable"
        and script.get("arguments") == "none"
        and script.get("environment") == "generic-only"
        and script.get("maximum_path_length") == 240,
        "invalid_input",
    )
    require(set(_strings(payload.get("failure_codes"), empty=False)) == FAILURE_CODES, "invalid_input")
    require(REQUIRED_FORBIDDEN_INPUTS <= set(_strings(payload.get("forbidden_inputs"), empty=False)), "invalid_input")
    postgres = payload.get("postgres")
    require(
        isinstance(postgres, Mapping)
        and postgres.get("runtime") == "postgres-16.11-alpine-amd64"
        and postgres.get("connection_environment_variable") == "CIW_POSTGRES_URL"
        and postgres.get("connection_url_scheme") == "postgresql"
        and postgres.get("remote_fallback") is False
        and postgres.get("credentials") == "ephemeral-per-execution"
        and postgres.get("readiness_attempts") == 30
        and postgres.get("readiness_interval_seconds") == 2
        and postgres.get("readiness_timeout_seconds") == 60,
        "invalid_input",
    )
    cleanup = payload.get("cleanup")
    require(isinstance(cleanup, Mapping) and cleanup and all(value is True for value in cleanup.values()), "invalid_input")
    return payload


def source_trust_from_environment(environment: Mapping[str, str]) -> str:
    if environment.get("GITHUB_EVENT_NAME") != "pull_request":
        return "trusted-exact"
    try:
        event = json.loads(Path(environment.get("GITHUB_EVENT_PATH", "")).read_text(encoding="utf-8"))
        head_repository = event["pull_request"]["head"]["repo"]["full_name"]
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as error:
        raise PythonValidationError("invalid_input") from error
    require(isinstance(head_repository, str) and head_repository, "invalid_input")
    return "trusted-pr" if head_repository == environment.get("GITHUB_REPOSITORY") else "untrusted-fork"


def request_from_environment(environment: Mapping[str, str]) -> PythonValidationRequest:
    repository = environment.get("GITHUB_REPOSITORY", "").strip()
    admitted_sha = environment.get("INPUT_ADMITTED_SHA", "").strip()
    profile = environment.get("INPUT_VALIDATION_PROFILE", "").strip()
    python_version = environment.get("INPUT_PYTHON_VERSION", "").strip()
    script_path = environment.get("INPUT_SCRIPT_PATH", "").strip()
    working = environment.get("INPUT_WORKING_DIRECTORY", ".").strip() or "."
    require(REPOSITORY.fullmatch(repository) is not None, "invalid_input")
    require(FULL_SHA.fullmatch(admitted_sha) is not None, "invalid_input")
    require(profile and python_version and script_path, "invalid_input")
    require(EXACT_VERSION.fullmatch(python_version) is not None or VERSION_FAMILY.fullmatch(python_version) is not None, "python_version_drift")
    artifact = environment.get("INPUT_ARTIFACT_EXCEPTION_ID", "").strip() or None
    require(artifact is None, "artifact_policy_failed")
    return PythonValidationRequest(
        repository=repository,
        admitted_sha=admitted_sha,
        validation_profile=profile,
        python_version=python_version,
        working_directory="." if working == "." else safe_relative(working),
        version_file=_optional_relative(environment.get("INPUT_VERSION_FILE", "").strip() or None),
        dependency_file=_optional_relative(environment.get("INPUT_DEPENDENCY_FILE", "").strip() or None),
        script_path=safe_relative(script_path),
        artifact_exception_id=artifact,
        source_trust=source_trust_from_environment(environment),
    )


def _runtime_id(contract: Mapping[str, Any], profile: Mapping[str, Any], python_version: str) -> str:
    if profile["runtime_kind"] == "host":
        require(python_version in contract["python_versions"]["host"], "python_version_drift")
        return "host-cpython-3.12"
    require(python_version in contract["python_versions"]["container"], "python_version_drift")
    matches = [identifier for identifier, runtime in contract["runtimes"].items() if runtime.get("kind") == "container" and runtime.get("python_version") == python_version]
    require(len(matches) == 1, "toolchain_mismatch")
    return matches[0]


def resolve_validation_plan(contract: Mapping[str, Any], request: PythonValidationRequest) -> PythonValidationPlan:
    profiles = contract["profiles"]
    require(request.validation_profile in profiles, "unsupported_profile")
    profile = profiles[request.validation_profile]
    require(request.source_trust in profile["allowed_source_trust"], "unsupported_profile")
    if profile["dependency_mode"] == "none":
        require(request.dependency_file is None, "invalid_input")
    runtime_id = _runtime_id(contract, profile, request.python_version)
    runtime = contract["runtimes"][runtime_id]
    postgres_reference = None
    if profile["postgres"]:
        postgres_reference = runtime_reference(contract["runtimes"][contract["postgres"]["runtime"]])
        require(postgres_reference is not None, "toolchain_mismatch")
    return PythonValidationPlan(
        repository=request.repository,
        admitted_sha=request.admitted_sha,
        validation_profile=request.validation_profile,
        runner_profile=str(profile["runner_profile"]),
        timeout_minutes=int(profile["timeout_minutes"]),
        workspace_profile=str(profile["workspace_profile"]),
        isolation=str(profile["isolation"]),
        runtime_id=runtime_id,
        runtime_reference=runtime_reference(runtime),
        python_version=request.python_version,
        working_directory=request.working_directory,
        version_file=request.version_file,
        dependency_file=request.dependency_file,
        script_path=request.script_path,
        postgres_runtime_reference=postgres_reference,
        readiness_attempts=int(contract["postgres"]["readiness_attempts"]),
        readiness_interval_seconds=int(contract["postgres"]["readiness_interval_seconds"]),
    )


def bounded_path(root: Path, relative: str) -> Path:
    resolved_root = root.resolve()
    if relative == ".":
        return resolved_root
    normalized = safe_relative(relative)
    parts = PurePosixPath(normalized).parts
    current = resolved_root
    for part in parts:
        current /= part
        require(not (current.exists() and current.is_symlink()), "invalid_input")
    target = (resolved_root / Path(*parts)).resolve(strict=False)
    require(target == resolved_root or resolved_root in target.parents, "invalid_input")
    return target


def validate_script_entrypoint(source_root: Path, plan: PythonValidationPlan) -> Path:
    script = bounded_path(source_root, plan.script_path)
    require(script.is_file() and not script.is_symlink() and os.access(script, os.X_OK), "invalid_input")
    return script


def resolve_python_version(source_root: Path, plan: PythonValidationPlan) -> str:
    expected = plan.python_version
    expected_exact = EXACT_VERSION.fullmatch(expected) is not None
    expected_family = VERSION_FAMILY.fullmatch(expected) is not None
    require(expected_exact or expected_family, "python_version_drift")
    if plan.version_file is None:
        return expected
    path = bounded_path(source_root, plan.version_file)
    require(path.is_file() and not path.is_symlink(), "python_version_drift")
    try:
        value = path.read_text(encoding="utf-8").strip()
    except OSError as error:
        raise PythonValidationError("python_version_drift") from error
    require("\n" not in value and "\r" not in value, "python_version_drift")
    value_exact = EXACT_VERSION.fullmatch(value) is not None
    value_family = VERSION_FAMILY.fullmatch(value) is not None
    require(value_exact or value_family, "python_version_drift")
    if expected_exact:
        require(value == expected or value_family and expected.startswith(value + "."), "python_version_drift")
    elif value_exact:
        require(value.startswith(expected + "."), "python_version_drift")
    else:
        require(value == expected, "python_version_drift")
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
    rows = [line.strip() for line in content.splitlines() if line.strip() and not line.lstrip().startswith("#")]
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
        require("git+" not in lowered and not lowered.startswith("-e ") and not lowered.startswith("--editable") and ("==" in row or ("--hash=sha256:" in row and ("https://" in row or "http://" in row))), "dependency_lock_drift")
    return "\n".join(material)


def validate_dependency_lock(source_root: Path, plan: PythonValidationPlan) -> str | None:
    if plan.dependency_file is None:
        return None
    material = _lock_material(bounded_path(source_root, plan.dependency_file), source_root.resolve(), set())
    return stable_identifier("python-lock", {"path": plan.dependency_file, "content": material}, length=32)
