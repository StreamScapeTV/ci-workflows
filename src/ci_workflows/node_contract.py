"""Checked-in Node validation contract, trust, and plan resolution."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

from .node_types import (
    NodeCommand,
    NodeValidationError,
    NodeValidationPlan,
    NodeValidationRequest,
)

CONTRACT_PATH = Path("contracts/node-validation.json")
_FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
_EXACT_VERSION = re.compile(
    r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$"
)
_REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_ENVIRONMENT_NAME = re.compile(r"^[A-Z][A-Z0-9_]{1,63}$")
_TOKEN_LIKE = re.compile(
    r"(?:gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{30,}|"
    r"AKIA[0-9A-Z]{16}|eyJ[A-Za-z0-9_-]{12,}\.[A-Za-z0-9_-]{12,}\.)"
)
_PROFILE_NAMES = {
    "locked-node",
    "next-static-export",
    "frontend-contract-static",
    "node-source-audit",
}
_COMMAND_NAMES = {
    "quality-test",
    "quality-test-build",
    "contract-test-build",
    "source-audit",
}
_FAILURE_CODES = {
    "invalid_input",
    "unsupported_profile",
    "invalid_runtime_source",
    "runtime_mismatch",
    "unsupported_package_manager",
    "missing_lockfile",
    "lockfile_drift",
    "install_failed",
    "command_profile_rejected",
    "quality_failed",
    "tests_failed",
    "build_failed",
    "public_environment_rejected",
    "output_missing",
    "output_malformed",
    "output_verifier_failed",
    "dirty_tree",
    "artifact_policy_failed",
    "cleanup_failed",
}
_REQUIRED_FORBIDDEN_INPUTS = {
    "arbitrary_command",
    "shell",
    "arguments",
    "callback",
    "runner",
    "runs_on",
    "runner_labels",
    "container_engine",
    "registry_host",
    "cloudflare_deployment",
    "worker",
    "wrangler",
    "secret_name",
    "database_url",
    "flux_target",
    "mutable_ref",
    "artifact_upload",
}
_ALTERNATE_PACKAGE_FILES = {
    "yarn.lock",
    "pnpm-lock.yaml",
    "pnpm-workspace.yaml",
    "bun.lock",
    "bun.lockb",
    "bunfig.toml",
    ".yarnrc",
    ".yarnrc.yml",
}


def require(condition: bool, code: str) -> None:
    if not condition:
        raise NodeValidationError(code)


def _read_json(path: Path, code: str = "invalid_input") -> Mapping[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise NodeValidationError(code) from error
    require(isinstance(value, Mapping), code)
    return value


def _strings(value: Any, *, empty: bool = True) -> list[str]:
    require(isinstance(value, list), "invalid_input")
    require(
        all(isinstance(item, str) and item for item in value),
        "invalid_input",
    )
    require(empty or bool(value), "invalid_input")
    require(len(value) == len(set(value)), "invalid_input")
    return list(value)


def safe_relative(
    value: Any,
    code: str = "invalid_input",
    *,
    allow_dot: bool = False,
) -> str:
    require(isinstance(value, str), code)
    candidate = value.strip()
    if allow_dot and candidate == ".":
        return "."
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


def bounded_path(root: Path, relative: str) -> Path:
    """Resolve an ordinary source path while rejecting every symlink component."""

    resolved_root = root.resolve()
    require(resolved_root.is_dir() and not root.is_symlink(), "invalid_input")
    if relative == ".":
        return resolved_root
    normalized = safe_relative(relative)
    lexical = resolved_root.joinpath(*PurePosixPath(normalized).parts)
    current = resolved_root
    for part in PurePosixPath(normalized).parts:
        current /= part
        require(not current.is_symlink(), "invalid_input")
        if not current.exists():
            break
    target = lexical.resolve(strict=False)
    require(
        target != resolved_root and resolved_root in target.parents,
        "invalid_input",
    )
    return target


def _validate_profile_contract(payload: Mapping[str, Any]) -> None:
    profiles = payload.get("profiles")
    require(isinstance(profiles, Mapping), "invalid_input")
    require(set(profiles) == _PROFILE_NAMES, "invalid_input")
    expected = {
        "locked-node": ("npm-ci", "none", 30),
        "next-static-export": ("npm-ci", "static-verifier", 60),
        "frontend-contract-static": ("npm-ci", "static-generic", 45),
        "node-source-audit": ("none", "none", 20),
    }
    for name, values in expected.items():
        profile = profiles[name]
        require(isinstance(profile, Mapping), "invalid_input")
        require(
            (
                profile.get("install_profile"),
                profile.get("output_mode"),
                profile.get("timeout_minutes"),
            )
            == values,
            "invalid_input",
        )
        require(profile.get("workspace_profile") == "minimal", "invalid_input")
        trusts = set(
            _strings(profile.get("allowed_source_trust"), empty=False)
        )
        require(
            trusts <= {"untrusted-fork", "trusted-pr", "trusted-exact"},
            "invalid_input",
        )


def _validate_command_contract(payload: Mapping[str, Any]) -> None:
    commands = payload.get("command_profiles")
    require(isinstance(commands, Mapping), "invalid_input")
    require(set(commands) == _COMMAND_NAMES, "invalid_input")
    for command in commands.values():
        require(isinstance(command, Mapping), "invalid_input")
        require(
            set(_strings(command.get("allowed_profiles"), empty=False))
            <= _PROFILE_NAMES,
            "invalid_input",
        )
        stages = _strings(command.get("stages"), empty=False)
        require(len(stages) <= 8, "invalid_input")
        require(
            command.get("hook_mode")
            in {"none", "fixed-output-verifier", "fixed-script"},
            "invalid_input",
        )


def _validate_consumer_shape(
    payload: Mapping[str, Any],
    command_name: str,
    value: Mapping[str, Any],
) -> None:
    command = payload["command_profiles"].get(command_name)
    require(isinstance(command, Mapping), "invalid_input")
    profile_name = value.get("validation_profile")
    require(profile_name in payload["profiles"], "unsupported_profile")
    require(profile_name in command["allowed_profiles"], "unsupported_profile")
    require(
        value.get("install_profile")
        == payload["profiles"][profile_name]["install_profile"],
        "invalid_input",
    )
    version_file = _optional_relative(value.get("version_file"))
    node_version = value.get("node_version")
    require(
        isinstance(node_version, str)
        and _EXACT_VERSION.fullmatch(node_version) is not None,
        "invalid_runtime_source",
    )
    require(
        not (
            version_file is None
            and value.get("version_file") not in {None, ""}
        ),
        "invalid_input",
    )
    safe_relative(
        str(value.get("working_directory", ".")),
        allow_dot=True,
    )
    for field in (
        "manifest_path",
        "lockfile_path",
        "script_path",
        "static_output_directory",
        "output_verifier_path",
    ):
        _optional_relative(value.get(field))
    require(isinstance(value.get("adoption_ready"), bool), "invalid_input")
    environment = set(_strings(value.get("allowed_public_environment")))
    require(
        environment <= set(payload["public_environment_keys"]),
        "public_environment_rejected",
    )
    rows = value.get("commands")
    require(isinstance(rows, list) and rows, "invalid_input")
    command_stages: list[str] = []
    for row in rows:
        require(
            isinstance(row, Mapping) and set(row) == {"stage", "argv"},
            "invalid_input",
        )
        stage = row.get("stage")
        require(stage in command["stages"], "command_profile_rejected")
        argv = _strings(row.get("argv"), empty=False)
        require(
            all("\n" not in item and "\r" not in item for item in argv),
            "invalid_input",
        )
        require(
            argv[0] not in {"yarn", "pnpm", "bun", "corepack"},
            "unsupported_package_manager",
        )
        require(
            not (argv[:2] == ["npm", "install"]),
            "unsupported_package_manager",
        )
        command_stages.append(str(stage))
    executable_stages = [
        stage for stage in command["stages"]
        if stage != "output-verification"
    ]
    require(
        command_stages == executable_stages
        or command_stages == list(command["stages"]),
        "command_profile_rejected",
    )
    hook_mode = command["hook_mode"]
    require(
        (hook_mode == "fixed-script") == bool(value.get("script_path")),
        "command_profile_rejected",
    )
    require(
        (hook_mode == "fixed-output-verifier")
        == bool(value.get("output_verifier_path")),
        "command_profile_rejected",
    )


def load_node_contract(root: Path) -> Mapping[str, Any]:
    """Load and validate the complete checked-in Node contract."""

    payload = _read_json(root / CONTRACT_PATH)
    require(payload.get("schema_version") == 1, "invalid_input")
    require(payload.get("contract_version") == "1.0.0", "invalid_input")
    require(payload.get("organization") == "StreamScapeTV", "invalid_input")
    require(payload.get("workflow_api") == "validation.node", "invalid_input")
    require(
        payload.get("stable_check_name") == "CI / Node validation",
        "invalid_input",
    )
    require(payload.get("runner_profile") == "portable", "invalid_input")
    require(payload.get("hard_timeout_minutes") == 90, "invalid_input")
    require(payload.get("cache_mode") == "disabled", "invalid_input")
    require(payload.get("artifact_policy") == "zero-default", "invalid_input")
    require(
        payload.get("package_manager") == "npm",
        "unsupported_package_manager",
    )
    require(payload.get("lockfile_version") == 3, "lockfile_drift")
    _validate_profile_contract(payload)
    _validate_command_contract(payload)
    environment_keys = set(_strings(payload.get("public_environment_keys")))
    require(
        environment_keys
        == {
            "NEXT_PUBLIC_API_URL",
            "NEXT_PUBLIC_API_BASE_URL",
            "NEXT_PUBLIC_PROJECT",
        },
        "public_environment_rejected",
    )
    limits = payload.get("public_environment_limits")
    require(
        isinstance(limits, Mapping)
        and limits.get("max_items") == 8
        and limits.get("max_key_length") == 64
        and limits.get("max_value_length") == 512,
        "invalid_input",
    )
    consumers = payload.get("consumers")
    require(isinstance(consumers, Mapping), "invalid_input")
    require(
        set(consumers)
        == {
            "StreamScapeTV/StreamScapeWeb",
            "StreamScapeTV/agent-state",
            "StreamScapeTV/agent-state-dashboard",
            "StreamScapeTV/finance-hub",
        },
        "invalid_input",
    )
    for repository, consumer in consumers.items():
        require(_REPOSITORY.fullmatch(repository) is not None, "invalid_input")
        require(isinstance(consumer, Mapping), "invalid_input")
        profiles = consumer.get("profiles")
        require(isinstance(profiles, Mapping) and profiles, "invalid_input")
        for name, value in profiles.items():
            require(isinstance(value, Mapping), "invalid_input")
            _validate_consumer_shape(payload, str(name), value)
    require(
        set(_strings(payload.get("failure_codes"), empty=False))
        == _FAILURE_CODES,
        "invalid_input",
    )
    require(
        _REQUIRED_FORBIDDEN_INPUTS
        <= set(_strings(payload.get("forbidden_inputs"), empty=False)),
        "invalid_input",
    )
    cleanup = payload.get("cleanup")
    require(
        isinstance(cleanup, Mapping)
        and cleanup
        and all(value is True for value in cleanup.values()),
        "invalid_input",
    )
    output_limits = payload.get("output_limits")
    require(
        isinstance(output_limits, Mapping)
        and isinstance(output_limits.get("max_files"), int)
        and 1 <= int(output_limits["max_files"]) <= 100000
        and isinstance(output_limits.get("max_bytes"), int)
        and 1 <= int(output_limits["max_bytes"]) <= 1024 * 1024 * 1024,
        "invalid_input",
    )
    return payload


def source_trust_from_environment(environment: Mapping[str, str]) -> str:
    """Derive trust from immutable GitHub event metadata."""

    if environment.get("GITHUB_EVENT_NAME") != "pull_request":
        return "trusted-exact"
    try:
        payload = json.loads(
            Path(environment.get("GITHUB_EVENT_PATH", "")).read_text(
                encoding="utf-8"
            )
        )
        head_repository = payload["pull_request"]["head"]["repo"][
            "full_name"
        ]
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as error:
        raise NodeValidationError("invalid_input") from error
    require(
        isinstance(head_repository, str) and head_repository,
        "invalid_input",
    )
    return (
        "trusted-pr"
        if head_repository == environment.get("GITHUB_REPOSITORY")
        else "untrusted-fork"
    )


def _public_environment(
    raw: str,
    contract: Mapping[str, Any],
) -> Mapping[str, str]:
    if not raw.strip():
        return {}
    require(
        len(raw.encode("utf-8")) <= 4096,
        "public_environment_rejected",
    )
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as error:
        raise NodeValidationError("public_environment_rejected") from error
    require(isinstance(payload, Mapping), "public_environment_rejected")
    limits = contract["public_environment_limits"]
    globally_allowed = set(contract["public_environment_keys"])
    require(
        len(payload) <= int(limits["max_items"]),
        "public_environment_rejected",
    )
    result: dict[str, str] = {}
    for name, value in payload.items():
        require(
            isinstance(name, str)
            and name in globally_allowed
            and _ENVIRONMENT_NAME.fullmatch(name) is not None
            and len(name) <= int(limits["max_key_length"])
            and isinstance(value, str)
            and len(value) <= int(limits["max_value_length"])
            and value
            and not any(
                ord(character) < 32 or ord(character) == 127
                for character in value
            )
            and "${{" not in value
            and "secrets." not in value.casefold()
            and "github." not in value.casefold()
            and _TOKEN_LIKE.search(value) is None,
            "public_environment_rejected",
        )
        result[name] = value
    return dict(sorted(result.items()))


def request_from_environment(
    environment: Mapping[str, str],
    contract: Mapping[str, Any],
) -> NodeValidationRequest:
    """Build one typed request from bounded action inputs."""

    repository = environment.get("GITHUB_REPOSITORY", "").strip()
    admitted_sha = environment.get("INPUT_ADMITTED_SHA", "").strip()
    validation_profile = environment.get(
        "INPUT_VALIDATION_PROFILE", ""
    ).strip()
    command_profile = environment.get("INPUT_COMMAND_PROFILE", "").strip()
    working = environment.get("INPUT_WORKING_DIRECTORY", ".").strip() or "."
    version_file = _optional_relative(
        environment.get("INPUT_VERSION_FILE", "").strip() or None
    )
    node_version = environment.get("INPUT_NODE_VERSION", "").strip() or None
    require(_REPOSITORY.fullmatch(repository) is not None, "invalid_input")
    require(_FULL_SHA.fullmatch(admitted_sha) is not None, "invalid_input")
    require(validation_profile and command_profile, "invalid_input")
    require(
        (version_file is None) != (node_version is None),
        "invalid_runtime_source",
    )
    if node_version is not None:
        require(
            _EXACT_VERSION.fullmatch(node_version) is not None,
            "invalid_runtime_source",
        )
    artifact = environment.get(
        "INPUT_ARTIFACT_EXCEPTION_ID", ""
    ).strip() or None
    require(artifact is None, "artifact_policy_failed")
    return NodeValidationRequest(
        repository=repository,
        admitted_sha=admitted_sha,
        validation_profile=validation_profile,
        version_file=version_file,
        node_version=node_version,
        working_directory=safe_relative(working, allow_dot=True),
        install_profile=environment.get("INPUT_INSTALL_PROFILE", "").strip(),
        command_profile=command_profile,
        script_path=_optional_relative(
            environment.get("INPUT_SCRIPT_PATH", "").strip() or None
        ),
        static_output_directory=_optional_relative(
            environment.get("INPUT_STATIC_OUTPUT_DIRECTORY", "").strip()
            or None
        ),
        output_verifier_path=_optional_relative(
            environment.get("INPUT_OUTPUT_VERIFIER_PATH", "").strip()
            or None
        ),
        public_environment=_public_environment(
            environment.get("INPUT_PUBLIC_ENVIRONMENT", ""),
            contract,
        ),
        artifact_exception_id=artifact,
        source_trust=source_trust_from_environment(environment),
    )


def resolve_validation_plan(
    contract: Mapping[str, Any],
    request: NodeValidationRequest,
) -> NodeValidationPlan:
    """Resolve one product-neutral plan from checked-in compatibility data."""

    profiles = contract["profiles"]
    require(request.validation_profile in profiles, "unsupported_profile")
    profile = profiles[request.validation_profile]
    require(
        request.source_trust in profile["allowed_source_trust"],
        "unsupported_profile",
    )
    require(
        request.install_profile == profile["install_profile"],
        "unsupported_package_manager",
    )
    command_profiles = contract["command_profiles"]
    require(
        request.command_profile in command_profiles,
        "command_profile_rejected",
    )
    require(
        request.validation_profile
        in command_profiles[request.command_profile]["allowed_profiles"],
        "unsupported_profile",
    )
    require(request.repository in contract["consumers"], "unsupported_profile")
    mappings = contract["consumers"][request.repository]["profiles"]
    require(request.command_profile in mappings, "unsupported_profile")
    shape = mappings[request.command_profile]
    require(
        shape["validation_profile"] == request.validation_profile,
        "unsupported_profile",
    )
    require(
        shape["install_profile"] == request.install_profile,
        "unsupported_package_manager",
    )
    require(
        shape.get("working_directory", ".") == request.working_directory,
        "invalid_input",
    )
    expected_version_file = shape.get("version_file")
    if expected_version_file is not None:
        expected_version_file = safe_relative(expected_version_file)
        require(
            request.version_file == expected_version_file
            and request.node_version is None,
            "invalid_runtime_source",
        )
    else:
        require(
            request.version_file is None
            and request.node_version == shape["node_version"],
            "invalid_runtime_source",
        )
    for request_value, expected in (
        (request.script_path, shape.get("script_path")),
        (
            request.static_output_directory,
            shape.get("static_output_directory"),
        ),
        (request.output_verifier_path, shape.get("output_verifier_path")),
    ):
        require(request_value == expected, "invalid_input")
    allowed_environment = tuple(shape["allowed_public_environment"])
    require(
        set(request.public_environment.keys())
        <= set(allowed_environment),
        "public_environment_rejected",
    )
    output_mode = str(profile["output_mode"])
    require(
        (output_mode != "none")
        == bool(request.static_output_directory),
        "invalid_input",
    )
    require(
        (output_mode == "static-verifier")
        == bool(request.output_verifier_path),
        "invalid_input",
    )
    return NodeValidationPlan(
        repository=request.repository,
        admitted_sha=request.admitted_sha,
        validation_profile=request.validation_profile,
        command_profile=request.command_profile,
        runner_profile=str(contract["runner_profile"]),
        timeout_minutes=int(profile["timeout_minutes"]),
        workspace_profile=str(profile["workspace_profile"]),
        source_trust=request.source_trust,
        version_file=expected_version_file,
        node_version=str(shape["node_version"]),
        working_directory=request.working_directory,
        install_profile=request.install_profile,
        manifest_path=shape.get("manifest_path"),
        lockfile_path=shape.get("lockfile_path"),
        script_path=shape.get("script_path"),
        static_output_directory=shape.get("static_output_directory"),
        output_verifier_path=shape.get("output_verifier_path"),
        output_mode=output_mode,
        allowed_public_environment=allowed_environment,
        public_environment=dict(request.public_environment),
        commands=tuple(
            NodeCommand(
                str(row["stage"]),
                tuple(str(item) for item in row["argv"]),
            )
            for row in shape["commands"]
        ),
        adoption_ready=bool(shape["adoption_ready"]),
    )


def resolve_exact_node_version(
    source_root: Path,
    plan: NodeValidationPlan,
) -> str:
    """Resolve exactly one canonical version from the selected authority."""

    expected = plan.node_version
    require(
        _EXACT_VERSION.fullmatch(expected) is not None,
        "invalid_runtime_source",
    )
    if plan.version_file is None:
        require(plan.version_authority == "exact-api", "invalid_runtime_source")
        return expected
    require(plan.version_authority == "version-file", "invalid_runtime_source")
    try:
        path = bounded_path(source_root, plan.version_file)
    except NodeValidationError as error:
        raise NodeValidationError("invalid_runtime_source") from error
    require(path.is_file() and not path.is_symlink(), "invalid_runtime_source")
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as error:
        raise NodeValidationError("invalid_runtime_source") from error
    require(
        raw.endswith("\n") and raw.count("\n") == 1,
        "invalid_runtime_source",
    )
    value = raw[:-1]
    require(
        value == value.strip()
        and _EXACT_VERSION.fullmatch(value) is not None,
        "invalid_runtime_source",
    )
    require(value == expected, "runtime_mismatch")
    return value


def file_sha256(path: Path, code: str) -> str:
    require(path.is_file() and not path.is_symlink(), code)
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as error:
        raise NodeValidationError(code) from error


def load_package_manifest(
    source_root: Path,
    plan: NodeValidationPlan,
) -> Mapping[str, Any] | None:
    if plan.manifest_path is None:
        require(
            plan.install_profile == "none",
            "unsupported_package_manager",
        )
        return None
    manifest = _read_json(
        bounded_path(source_root, plan.manifest_path),
        "unsupported_package_manager",
    )
    package_manager = manifest.get("packageManager")
    if package_manager is not None:
        require(
            isinstance(package_manager, str)
            and re.fullmatch(
                r"npm@[0-9]+\.[0-9]+\.[0-9]+",
                package_manager,
            )
            is not None,
            "unsupported_package_manager",
        )
    working = bounded_path(source_root, plan.working_directory)
    for filename in _ALTERNATE_PACKAGE_FILES:
        require(
            not (working / filename).exists(),
            "unsupported_package_manager",
        )
    return manifest


def load_lockfile(
    source_root: Path,
    plan: NodeValidationPlan,
) -> Mapping[str, Any] | None:
    if plan.install_profile == "none":
        require(
            plan.lockfile_path is None,
            "unsupported_package_manager",
        )
        return None
    require(
        plan.install_profile == "npm-ci"
        and plan.lockfile_path is not None,
        "unsupported_package_manager",
    )
    lock = _read_json(
        bounded_path(source_root, plan.lockfile_path),
        "missing_lockfile",
    )
    require(lock.get("lockfileVersion") == 3, "lockfile_drift")
    require(isinstance(lock.get("packages"), Mapping), "lockfile_drift")
    return lock


def _version_tuple(value: str) -> tuple[int, int, int]:
    match = _EXACT_VERSION.fullmatch(value)
    require(match is not None, "runtime_mismatch")
    return tuple(int(part) for part in match.groups())


def _bound_tuple(value: str) -> tuple[int, int, int]:
    parts = value.split(".")
    require(
        1 <= len(parts) <= 3 and all(part.isdigit() for part in parts),
        "runtime_mismatch",
    )
    padded = [*parts, *("0" for _ in range(3 - len(parts)))]
    return tuple(int(part) for part in padded)


def version_satisfies(version: str, expression: str) -> bool:
    """Evaluate the deliberately small reviewed engine-range grammar."""

    require(
        isinstance(expression, str) and expression.strip(),
        "runtime_mismatch",
    )
    require(
        not any(
            token in expression
            for token in ("||", "^", "~", "*", "x", "X", "latest", "lts")
        ),
        "runtime_mismatch",
    )
    actual = _version_tuple(version)
    tokens = expression.split()
    if len(tokens) == 1 and _EXACT_VERSION.fullmatch(tokens[0]):
        return actual == _version_tuple(tokens[0])
    require(
        tokens
        and all(
            re.fullmatch(
                r"(?:>=|>|<=|<)[0-9]+(?:\.[0-9]+){0,2}",
                token,
            )
            for token in tokens
        ),
        "runtime_mismatch",
    )
    for token in tokens:
        if token.startswith(">=") and not actual >= _bound_tuple(token[2:]):
            return False
        if (
            token.startswith(">")
            and not token.startswith(">=")
            and not actual > _bound_tuple(token[1:])
        ):
            return False
        if token.startswith("<=") and not actual <= _bound_tuple(token[2:]):
            return False
        if (
            token.startswith("<")
            and not token.startswith("<=")
            and not actual < _bound_tuple(token[1:])
        ):
            return False
    return True


def verify_manifest_engines(
    manifest: Mapping[str, Any] | None,
    node_version: str,
    npm_version: str,
) -> None:
    if manifest is None:
        return
    engines = manifest.get("engines", {})
    require(isinstance(engines, Mapping), "runtime_mismatch")
    node_range = engines.get("node")
    npm_range = engines.get("npm")
    if node_range is not None:
        require(
            isinstance(node_range, str)
            and version_satisfies(node_version, node_range),
            "runtime_mismatch",
        )
    if npm_range is not None:
        require(
            isinstance(npm_range, str)
            and version_satisfies(npm_version, npm_range),
            "runtime_mismatch",
        )
