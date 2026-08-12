"""Checked-in Apple validation contract, trust, and plan resolution."""
from __future__ import annotations

import json
import re
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence

from .apple_types import (
    AppleCommand,
    AppleContainer,
    AppleProfile,
    AppleRunnerCapability,
    AppleSimulator,
    AppleStage,
    AppleToolchain,
    AppleValidationError,
    AppleValidationPlan,
    AppleValidationRequest,
)

CONTRACT_PATH = Path("contracts/apple-validation.json")
FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
IDENTIFIER = re.compile(r"^[a-z][a-z0-9.-]{2,95}$")
VERSION = re.compile(r"^[0-9]+(?:\.[0-9]+){1,2}$")
XCODE_BUILD = re.compile(r"^[0-9A-Z]{3,20}$")
SCHEME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_. +()-]{0,127}$")
CONFIGURATION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_. -]{0,63}$")
RUNTIME_ID = re.compile(r"^com\.apple\.CoreSimulator\.SimRuntime\.[A-Za-z0-9-]+$")
DEVICE_TYPE_ID = re.compile(
    r"^com\.apple\.CoreSimulator\.SimDeviceType\.[A-Za-z0-9-]+$"
)
ENVIRONMENT_KEY = re.compile(r"^[A-Z][A-Z0-9_]{2,63}$")
SIMULATOR_PLATFORM = {"iOS Simulator", "tvOS Simulator"}
SIMULATOR_FAMILY = {
    "iOS Simulator": "iPhone",
    "tvOS Simulator": "Apple TV",
}
CONTAINER_KINDS = {"project", "workspace", "package"}
COMMAND_KINDS = {"xcodebuild", "swift", "python3-script", "bash-script"}
XCODE_ACTIONS = {"build", "build-for-testing", "test"}
STATE_DIRECTORIES = {
    "home",
    "derived-data",
    "result-bundles",
    "swiftpm",
    "swiftpm-build",
    "cocoapods",
    "tmp",
    "logs",
    "reports",
    "native-output",
    "native-cache",
    "caches",
}
FORBIDDEN_ENVIRONMENT_KEYS = {
    "DEVELOPER_DIR",
    "CODE_SIGN_IDENTITY",
    "PROVISIONING_PROFILE",
    "PROVISIONING_PROFILE_SPECIFIER",
    "DEVELOPMENT_TEAM",
    "OTHER_CODE_SIGN_FLAGS",
    "KEYCHAIN_PATH",
    "KEYCHAIN_PASSWORD",
    "DEVICE_UDID",
    "DESTINATION",
    "KUBECONFIG",
}
PROHIBITED_ARGUMENT_FRAGMENTS = (
    "CODE_SIGNING_ALLOWED=YES",
    "CODE_SIGN_IDENTITY",
    "PROVISIONING_PROFILE",
    "DEVELOPMENT_TEAM",
    "-archivePath",
    "archive",
    "exportArchive",
    "-exportOptionsPlist",
    "notarytool",
    "altool",
    "transporter",
    "testflight",
    "app-store",
    "security import",
    "keychain",
    "curl ",
    "wget ",
    "sudo ",
    "kubectl ",
    "helm ",
    "docker ",
    "podman ",
    "buildah ",
)
REQUIRED_FORBIDDEN_INPUTS = {
    "xcode_path",
    "developer_dir",
    "arbitrary_command",
    "arguments",
    "shell",
    "callback",
    "runner",
    "runs_on",
    "runner_labels",
    "host",
    "architecture",
    "physical_device",
    "device_udid",
    "device_selector",
    "signing_identity",
    "provisioning_profile",
    "development_team",
    "keychain",
    "archive",
    "export",
    "store",
    "testflight",
    "notarization",
    "registry",
    "kubernetes",
    "deployment",
    "secret_name",
    "dependency_url",
    "mutable_ref",
}


def fail(code: str) -> None:
    raise AppleValidationError(code)


def require(condition: bool, code: str) -> None:
    if not condition:
        fail(code)


def _strings(
    value: Any,
    *,
    nonempty: bool = False,
    unique: bool = True,
) -> list[str]:
    require(isinstance(value, list) and (bool(value) or not nonempty), "contract_invalid")
    require(all(isinstance(item, str) and item for item in value), "contract_invalid")
    require(not unique or len(value) == len(set(value)), "contract_invalid")
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
        and "\x00" not in candidate,
        code,
    )
    require(all(part not in {"", "."} for part in path.parts), code)
    return path.as_posix()


def bounded_path(
    root: Path,
    relative: str,
    *,
    must_exist: bool = False,
) -> Path:
    boundary = root.resolve()
    require(boundary.is_dir() and not root.is_symlink(), "path_rejected")
    if relative == ".":
        return boundary
    lexical = boundary.joinpath(*PurePosixPath(safe_relative(relative)).parts)
    current = boundary
    for part in PurePosixPath(relative).parts:
        current /= part
        require(not current.is_symlink(), "path_rejected")
        if not current.exists():
            break
    resolved = lexical.resolve(strict=False)
    require(resolved != boundary and boundary in resolved.parents, "path_rejected")
    require(not must_exist or resolved.exists(), "path_rejected")
    return resolved


def regular_path(root: Path, relative: str, code: str = "path_rejected") -> Path:
    path = bounded_path(root, safe_relative(relative, code), must_exist=True)
    require(path.exists() and not path.is_symlink(), code)
    return path


def _validate_toolchain(raw: Mapping[str, Any]) -> AppleToolchain:
    require(
        set(raw)
        == {
            "xcode_version",
            "xcode_build",
            "swift_version",
            "sdk_versions",
            "simulator_runtimes",
        },
        "toolchain_mismatch",
    )
    for field in ("xcode_version", "swift_version"):
        require(
            isinstance(raw.get(field), str)
            and VERSION.fullmatch(str(raw[field])) is not None,
            "toolchain_mismatch",
        )
    require(
        isinstance(raw.get("xcode_build"), str)
        and XCODE_BUILD.fullmatch(str(raw["xcode_build"])) is not None,
        "toolchain_mismatch",
    )
    sdk_versions = raw.get("sdk_versions")
    require(isinstance(sdk_versions, Mapping) and sdk_versions, "sdk_missing")
    expected_sdks = {
        "iphoneos",
        "iphonesimulator",
        "appletvos",
        "appletvsimulator",
        "macosx",
    }
    require(set(sdk_versions) == expected_sdks, "sdk_missing")
    for sdk, version in sdk_versions.items():
        require(
            isinstance(sdk, str)
            and isinstance(version, str)
            and VERSION.fullmatch(version) is not None,
            "sdk_missing",
        )
    runtimes = raw.get("simulator_runtimes")
    require(isinstance(runtimes, Mapping) and set(runtimes) == {"ios", "tvos"}, "runtime_missing")
    runtime_rows: list[tuple[str, str, str]] = []
    for platform, value in sorted(runtimes.items()):
        require(isinstance(value, Mapping), "runtime_missing")
        identifier = value.get("identifier")
        version = value.get("version")
        require(
            isinstance(identifier, str)
            and RUNTIME_ID.fullmatch(identifier) is not None
            and isinstance(version, str)
            and VERSION.fullmatch(version) is not None,
            "runtime_missing",
        )
        runtime_rows.append((platform, identifier, version))
    return AppleToolchain(
        xcode_version=str(raw["xcode_version"]),
        xcode_build=str(raw["xcode_build"]),
        swift_version=str(raw["swift_version"]),
        sdk_versions=tuple(sorted((str(k), str(v)) for k, v in sdk_versions.items())),
        simulator_runtimes=tuple(runtime_rows),
    )


def _validate_container(raw: object) -> AppleContainer | None:
    if raw is None:
        return None
    require(isinstance(raw, Mapping), "container_invalid")
    require(
        set(raw)
        == {
            "kind",
            "path",
            "scheme",
            "configuration",
            "test_plan",
            "package_resolution_mode",
            "resolved_files",
        },
        "container_invalid",
    )
    kind = raw.get("kind")
    path = safe_relative(raw.get("path"), "container_invalid")
    require(kind in CONTAINER_KINDS, "container_invalid")
    if kind == "project":
        require(path.endswith(".xcodeproj"), "container_invalid")
    elif kind == "workspace":
        require(path.endswith(".xcworkspace"), "container_invalid")
    else:
        require(path.endswith("Package.swift"), "container_invalid")
    scheme = raw.get("scheme")
    configuration = raw.get("configuration")
    require(isinstance(scheme, str) and SCHEME.fullmatch(scheme) is not None, "scheme_rejected")
    require(
        isinstance(configuration, str)
        and CONFIGURATION.fullmatch(configuration) is not None,
        "configuration_rejected",
    )
    test_plan = raw.get("test_plan")
    if test_plan is not None:
        test_plan = safe_relative(test_plan, "test_plan_rejected")
        require(test_plan.endswith(".xctestplan"), "test_plan_rejected")
    resolution = raw.get("package_resolution_mode")
    require(resolution in {"locked", "disabled", "resolve-only"}, "package_resolution_rejected")
    resolved_files = tuple(
        safe_relative(item, "package_resolution_rejected")
        for item in _strings(raw.get("resolved_files"))
    )
    if resolution == "locked":
        require(bool(resolved_files), "package_resolution_rejected")
    return AppleContainer(
        kind=str(kind),
        path=path,
        scheme=scheme,
        configuration=configuration,
        test_plan=test_plan,
        package_resolution_mode=str(resolution),
        resolved_files=resolved_files,
    )


def _validate_simulator(identifier: str, raw: Mapping[str, Any]) -> AppleSimulator:
    require(IDENTIFIER.fullmatch(identifier) is not None, "simulator_contract_invalid")
    require(
        set(raw)
        == {
            "platform",
            "runtime_identifier",
            "runtime_version",
            "device_name_prefix",
            "device_type",
            "device_type_identifier",
            "device_family",
            "allow_create",
        },
        "simulator_contract_invalid",
    )
    platform = raw.get("platform")
    runtime_identifier = raw.get("runtime_identifier")
    runtime_version = raw.get("runtime_version")
    device_name_prefix = raw.get("device_name_prefix")
    device_type = raw.get("device_type")
    device_type_identifier = raw.get("device_type_identifier")
    device_family = raw.get("device_family")
    require(platform in SIMULATOR_PLATFORM, "unsafe_destination")
    require(
        isinstance(runtime_identifier, str)
        and RUNTIME_ID.fullmatch(runtime_identifier) is not None,
        "runtime_missing",
    )
    require(
        isinstance(runtime_version, str)
        and VERSION.fullmatch(runtime_version) is not None,
        "runtime_missing",
    )
    require(
        isinstance(device_name_prefix, str)
        and SCHEME.fullmatch(device_name_prefix) is not None
        and isinstance(device_type, str)
        and SCHEME.fullmatch(device_type) is not None,
        "simulator_contract_invalid",
    )
    require(
        isinstance(device_type_identifier, str)
        and DEVICE_TYPE_ID.fullmatch(device_type_identifier) is not None,
        "simulator_contract_invalid",
    )
    require(
        device_family == SIMULATOR_FAMILY.get(str(platform)),
        "simulator_contract_invalid",
    )
    require(isinstance(raw.get("allow_create"), bool), "simulator_contract_invalid")
    return AppleSimulator(
        platform=str(platform),
        runtime_identifier=runtime_identifier,
        runtime_version=runtime_version,
        device_name_prefix=device_name_prefix,
        device_type=device_type,
        device_type_identifier=device_type_identifier,
        device_family=str(device_family),
        allow_create=bool(raw["allow_create"]),
    )


def _validate_command(raw: Mapping[str, Any]) -> AppleCommand:
    require(
        set(raw)
        == {
            "stage",
            "kind",
            "action",
            "script_path",
            "fixed_arguments",
            "expected_outputs",
        },
        "command_profile_rejected",
    )
    try:
        stage = AppleStage(str(raw["stage"]))
    except ValueError as error:
        raise AppleValidationError("command_profile_rejected") from error
    kind = raw.get("kind")
    action = raw.get("action")
    require(kind in COMMAND_KINDS and isinstance(action, str), "command_profile_rejected")
    if kind == "xcodebuild":
        require(action in XCODE_ACTIONS, "command_profile_rejected")
    elif kind == "swift":
        require(action in {"test", "build", "resolve"}, "command_profile_rejected")
    else:
        require(action == "run", "command_profile_rejected")
    script_path = raw.get("script_path")
    if kind.endswith("-script"):
        script_path = safe_relative(script_path, "script_rejected")
        require(
            script_path.endswith(".py") if kind == "python3-script" else script_path.endswith(".sh"),
            "script_rejected",
        )
    else:
        require(script_path is None, "command_profile_rejected")
    arguments = tuple(_strings(raw.get("fixed_arguments"), unique=False))
    require(len(arguments) <= 24, "command_profile_rejected")
    serialized = " ".join(arguments).casefold()
    require(
        all("\n" not in item and "\r" not in item and "\x00" not in item for item in arguments),
        "command_profile_rejected",
    )
    require(
        not any(fragment.casefold() in serialized for fragment in PROHIBITED_ARGUMENT_FRAGMENTS),
        "forbidden_operation",
    )
    expected_outputs = tuple(
        safe_relative(item, "output_invalid")
        for item in _strings(raw.get("expected_outputs"))
    )
    return AppleCommand(
        stage=stage,
        kind=str(kind),
        action=action,
        script_path=script_path,
        fixed_arguments=arguments,
        expected_outputs=expected_outputs,
    )


def _validate_task(
    task_id: str,
    raw: Mapping[str, Any],
    profiles: Mapping[str, Any],
    simulators: Mapping[str, AppleSimulator],
    artifact_exceptions: Mapping[str, Any],
) -> dict[str, Any]:
    require(IDENTIFIER.fullmatch(task_id) is not None, "task_profile_rejected")
    required = {
        "validation_profile",
        "working_directory",
        "container",
        "simulator_id",
        "commands",
        "protected_paths",
        "cleanup_paths",
        "environment",
        "artifact_exception_ids",
    }
    require(set(raw) == required, "task_profile_rejected")
    profile = raw.get("validation_profile")
    require(profile in profiles, "unsupported_profile")
    working_directory = safe_relative(raw.get("working_directory"), allow_dot=True)
    container = _validate_container(raw.get("container"))
    simulator_id = raw.get("simulator_id")
    simulator = None
    if simulator_id is not None:
        require(simulator_id in simulators, "simulator_contract_invalid")
        simulator = simulators[str(simulator_id)]
    if profile == AppleProfile.IOS_SIMULATOR.value:
        require(simulator is not None and simulator.platform == "iOS Simulator", "unsafe_destination")
    elif profile == AppleProfile.TVOS_SIMULATOR.value:
        require(simulator is not None and simulator.platform == "tvOS Simulator", "unsafe_destination")
    else:
        require(simulator is None, "unsafe_destination")
    commands_raw = raw.get("commands")
    require(isinstance(commands_raw, list), "command_profile_rejected")
    commands = tuple(_validate_command(row) for row in commands_raw if isinstance(row, Mapping))
    require(len(commands) == len(commands_raw), "command_profile_rejected")
    if profile == AppleProfile.SOURCE_AUDIT.value:
        require(not commands and container is None, "command_profile_rejected")
    elif profile in {
        AppleProfile.IOS_SIMULATOR.value,
        AppleProfile.TVOS_SIMULATOR.value,
        AppleProfile.MACOS.value,
    }:
        require(
            container is not None
            and any(command.kind == "xcodebuild" for command in commands),
            "command_profile_rejected",
        )
    elif profile == AppleProfile.SWIFT_PACKAGE.value:
        require(
            container is not None
            and container.kind == "package"
            and any(command.kind == "swift" for command in commands),
            "command_profile_rejected",
        )
    elif profile in {
        AppleProfile.NATIVE_DEPENDENCY_PREPARATION.value,
        AppleProfile.REPOSITORY_RECOVERY.value,
    }:
        require(
            commands
            and all(command.kind.endswith("-script") for command in commands),
            "command_profile_rejected",
        )
    protected_paths = tuple(
        safe_relative(item, "path_rejected")
        for item in _strings(raw.get("protected_paths"))
    )
    cleanup_paths = tuple(
        safe_relative(item, "cleanup_failed")
        for item in _strings(raw.get("cleanup_paths"))
    )
    environment = raw.get("environment")
    require(isinstance(environment, Mapping), "environment_rejected")
    environment_bindings: list[tuple[str, str]] = []
    for key, value in sorted(environment.items()):
        require(
            isinstance(key, str)
            and ENVIRONMENT_KEY.fullmatch(key) is not None
            and key not in FORBIDDEN_ENVIRONMENT_KEYS
            and isinstance(value, str)
            and value in STATE_DIRECTORIES,
            "environment_rejected",
        )
        environment_bindings.append((key, value))
    exceptions = tuple(_strings(raw.get("artifact_exception_ids")))
    require(all(item in artifact_exceptions for item in exceptions), "artifact_exception_rejected")
    return {
        "validation_profile": str(profile),
        "working_directory": working_directory,
        "container": container,
        "simulator": simulator,
        "commands": commands,
        "protected_paths": protected_paths,
        "cleanup_paths": cleanup_paths,
        "environment_bindings": tuple(environment_bindings),
        "artifact_exception_ids": exceptions,
    }


def load_apple_contract(root: Path) -> Mapping[str, Any]:
    """Load and validate the complete checked-in Apple contract."""

    try:
        raw = json.loads((root / CONTRACT_PATH).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise AppleValidationError("contract_invalid") from error
    require(isinstance(raw, Mapping), "contract_invalid")
    require(
        (raw.get("schema_version"), raw.get("contract_version"), raw.get("organization"))
        == (1, "1.0.0", "StreamScapeTV"),
        "contract_invalid",
    )
    require(
        (raw.get("workflow_api"), raw.get("stable_check_name"))
        == ("validation.apple", "CI / Apple validation"),
        "contract_invalid",
    )
    require(
        (raw.get("planner_runner_profile"), raw.get("execution_runner_profile"))
        == ("portable", "apple"),
        "runner_rejected",
    )
    require(
        raw.get("hard_timeout_minutes") == 120
        and raw.get("cache_mode") == "disabled"
        and raw.get("artifact_policy") == "zero-default",
        "contract_invalid",
    )
    toolchain_raw = raw.get("toolchain")
    require(isinstance(toolchain_raw, Mapping), "toolchain_mismatch")
    toolchain = _validate_toolchain(toolchain_raw)
    profiles = raw.get("profiles")
    require(
        isinstance(profiles, Mapping)
        and set(profiles) == {profile.value for profile in AppleProfile},
        "unsupported_profile",
    )
    for name, profile in profiles.items():
        require(isinstance(profile, Mapping), "contract_invalid")
        require(
            isinstance(profile.get("timeout_minutes"), int)
            and 1 <= int(profile["timeout_minutes"]) <= 120,
            "contract_invalid",
        )
        require(
            set(_strings(profile.get("allowed_source_trust"), nonempty=True))
            <= {"trusted-pr", "trusted-exact"},
            "source_trust_rejected",
        )
        require(profile.get("runner") == "apple", "runner_rejected")
    simulator_raw = raw.get("simulators")
    require(isinstance(simulator_raw, Mapping), "simulator_contract_invalid")
    simulators = {
        str(identifier): _validate_simulator(str(identifier), value)
        for identifier, value in simulator_raw.items()
        if isinstance(value, Mapping)
    }
    require(len(simulators) == len(simulator_raw), "simulator_contract_invalid")
    artifact_exceptions = raw.get("artifact_exceptions")
    require(isinstance(artifact_exceptions, Mapping), "artifact_exception_rejected")
    for name, value in artifact_exceptions.items():
        require(IDENTIFIER.fullmatch(str(name)) is not None, "artifact_exception_rejected")
        require(isinstance(value, Mapping), "artifact_exception_rejected")
        require(
            value.get("maximum_files") in range(1, 101)
            and isinstance(value.get("maximum_bytes"), int)
            and 1 <= int(value["maximum_bytes"]) <= 64 * 1024 * 1024,
            "artifact_exception_rejected",
        )
        require(
            set(_strings(value.get("allowed_extensions"), nonempty=True))
            <= {".xcresult", ".json", ".log"},
            "artifact_exception_rejected",
        )
    task_raw = raw.get("tasks")
    require(isinstance(task_raw, Mapping) and task_raw, "task_profile_rejected")
    tasks = {
        str(task_id): _validate_task(
            str(task_id),
            value,
            profiles,
            simulators,
            artifact_exceptions,
        )
        for task_id, value in task_raw.items()
        if isinstance(value, Mapping)
    }
    require(len(tasks) == len(task_raw), "task_profile_rejected")
    consumers = raw.get("consumer_contracts")
    require(isinstance(consumers, Mapping) and consumers, "consumer_contract_rejected")
    for identifier, consumer in consumers.items():
        require(IDENTIFIER.fullmatch(str(identifier)) is not None, "consumer_contract_rejected")
        require(isinstance(consumer, Mapping), "consumer_contract_rejected")
        require(
            set(consumer) == {"repository", "profiles"}
            and isinstance(consumer.get("repository"), str)
            and REPOSITORY.fullmatch(str(consumer["repository"])) is not None,
            "consumer_contract_rejected",
        )
        mapping = consumer.get("profiles")
        require(isinstance(mapping, Mapping) and mapping, "consumer_contract_rejected")
        for profile, task_id in mapping.items():
            require(profile in profiles and task_id in tasks, "consumer_contract_rejected")
            require(tasks[str(task_id)]["validation_profile"] == profile, "consumer_contract_rejected")
    require(
        REQUIRED_FORBIDDEN_INPUTS
        <= set(_strings(raw.get("forbidden_inputs"), nonempty=True)),
        "contract_invalid",
    )
    cleanup = raw.get("cleanup")
    require(
        isinstance(cleanup, Mapping)
        and cleanup
        and all(value is True for value in cleanup.values()),
        "cleanup_failed",
    )
    return {
        **raw,
        "_toolchain": toolchain,
        "_simulators": simulators,
        "_tasks": tasks,
    }


def build_plan(
    contract: Mapping[str, Any],
    request: AppleValidationRequest,
) -> AppleValidationPlan:
    require(
        REPOSITORY.fullmatch(request.repository) is not None
        and FULL_SHA.fullmatch(request.admitted_sha) is not None,
        "invalid_input",
    )
    require(request.source_trust in {"trusted-pr", "trusted-exact"}, "source_trust_rejected")
    consumers = contract["consumer_contracts"]
    require(request.consumer_contract in consumers, "consumer_contract_rejected")
    consumer = consumers[request.consumer_contract]
    require(consumer["repository"] == request.repository, "consumer_contract_rejected")
    profile = request.validation_profile.value
    require(profile in consumer["profiles"], "profile_consumer_mismatch")
    task_id = str(consumer["profiles"][profile])
    task = contract["_tasks"][task_id]
    generic_profile = contract["profiles"][profile]
    require(request.source_trust in generic_profile["allowed_source_trust"], "source_trust_rejected")
    container: AppleContainer | None = task["container"]
    exact_values = {
        "working_directory": task["working_directory"],
        "project_path": container.path if container is not None else None,
        "scheme": container.scheme if container is not None else None,
        "configuration": container.configuration if container is not None else None,
        "test_plan": container.test_plan if container is not None else None,
    }
    for field, expected in exact_values.items():
        supplied = getattr(request, field)
        if supplied not in {None, ""}:
            require(supplied == expected, f"{field}_rejected" if field != "project_path" else "container_invalid")
    artifact_exception = request.artifact_exception_id
    if artifact_exception:
        require(
            artifact_exception in task["artifact_exception_ids"],
            "artifact_exception_rejected",
        )
    return AppleValidationPlan(
        request=request,
        task_profile=task_id,
        runner_profile=AppleRunnerCapability.APPLE,
        planner_runner_profile=AppleRunnerCapability.PORTABLE,
        workspace_profile="apple",
        timeout_minutes=int(generic_profile["timeout_minutes"]),
        toolchain=contract["_toolchain"],
        working_directory=task["working_directory"],
        container=container,
        simulator=task["simulator"],
        commands=task["commands"],
        protected_paths=task["protected_paths"],
        cleanup_paths=task["cleanup_paths"],
        environment_bindings=task["environment_bindings"],
        artifact_exception_id=artifact_exception,
    )
