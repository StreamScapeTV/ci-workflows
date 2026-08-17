"""Caller-owned Apple protected-full planning and single-executor execution.

The legacy Apple consumer/profile contract remains supported by ``ciw_apple``.
This module owns only the v2 protected-full path: a bounded sequence of Apple
stages sharing one Xcode/SwiftPM workspace while reusing the reviewed Apple
execution primitives for toolchain validation, signing lockdown, simulator
ownership, stale-simulator reclamation, and no-follow cleanup.
"""
from __future__ import annotations

import hashlib
import json
import re
from contextlib import nullcontext
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from . import apple_execution
from .apple_contract import bounded_path, safe_relative
from .apple_types import (
    AppleCommand,
    AppleContainer,
    AppleProfile,
    AppleRunnerCapability,
    AppleStage,
    AppleValidationError,
    AppleValidationPlan,
    AppleValidationRequest,
)

_FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
_REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_IDENTIFIER = re.compile(r"^[a-z][a-z0-9-]{1,31}$")
_SCHEME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_. +()-]{0,127}$")
_CONFIGURATION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_. -]{0,63}$")
_TEST_SELECTOR = re.compile(r"^[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+){0,3}$")
_MAX_PLAN_BYTES = 32 * 1024
_MAX_STAGES = 8
_MAX_ARGUMENTS = 24
_XCODE_OPERATIONS = {"build", "build-for-testing", "test"}
_FORBIDDEN_ARGUMENT_FRAGMENTS = (
    "code_signing_allowed=",
    "code_signing_required=",
    "code_sign_identity",
    "provisioning_profile",
    "development_team",
    "-destination",
    "-deriveddatapath",
    "-clonedsourcepackagesdirpath",
    "-resultbundlepath",
    "-archivepath",
    "archive",
    "exportarchive",
    "notarytool",
    "altool",
    "testflight",
    "app-store",
    "security",
    "keychain",
    "sudo",
    "kubectl",
    "helm",
    "docker",
    "podman",
    "buildah",
)


def _fail(code: str) -> None:
    raise AppleValidationError(code)


def _plain(
    value: object,
    code: str,
    *,
    allow_empty: bool = False,
    maximum: int = 4096,
) -> str:
    if not isinstance(value, str):
        _fail(code)
    text = value.strip()
    if (
        (not allow_empty and not text)
        or len(text.encode("utf-8")) > maximum
        or any(character in text for character in ("\x00", "\r", "\n"))
    ):
        _fail(code)
    return text


def _strings(
    value: object,
    code: str,
    *,
    maximum_items: int,
    unique: bool = True,
) -> tuple[str, ...]:
    if not isinstance(value, list) or len(value) > maximum_items:
        _fail(code)
    rows = tuple(_plain(item, code, maximum=2048) for item in value)
    if unique and len(rows) != len(set(rows)):
        _fail(code)
    return rows


def _relative(value: object, code: str, *, allow_dot: bool = False) -> str:
    return safe_relative(
        _plain(value, code, maximum=1024),
        code,
        allow_dot=allow_dot,
    )


def _json_object(raw: str) -> dict[str, object]:
    text = _plain(raw, "validation_plan_invalid", maximum=_MAX_PLAN_BYTES)

    def hook(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                _fail("validation_plan_invalid")
            result[key] = value
        return result

    try:
        value = json.loads(text, object_pairs_hook=hook)
    except json.JSONDecodeError as error:
        raise AppleValidationError("validation_plan_invalid") from error
    if not isinstance(value, dict) or set(value) != {"stages"}:
        _fail("validation_plan_invalid")
    return value


def _arguments(value: object) -> tuple[str, ...]:
    values = _strings(
        value,
        "validation_plan_invalid",
        maximum_items=_MAX_ARGUMENTS,
        unique=False,
    )
    serialized = " ".join(values).casefold()
    if any(fragment in serialized for fragment in _FORBIDDEN_ARGUMENT_FRAGMENTS):
        _fail("forbidden_operation")
    return values


def _paths(value: object, code: str, maximum_items: int = 16) -> tuple[str, ...]:
    return tuple(
        _relative(item, code)
        for item in _strings(value, code, maximum_items=maximum_items)
    )


def _simulator_for(contract: Mapping[str, Any], platform: str):
    identifier = {"ios": "ciw-ios", "tvos": "ciw-tvos"}.get(platform)
    if identifier is None:
        return None
    simulators = contract.get("_simulators")
    if not isinstance(simulators, Mapping) or identifier not in simulators:
        _fail("simulator_contract_invalid")
    return simulators[identifier]


def _profile(platform: str, operation: str) -> AppleProfile:
    if operation == "script":
        if platform != "source":
            _fail("validation_plan_invalid")
        return AppleProfile.REPOSITORY_RECOVERY
    try:
        return {
            "ios": AppleProfile.IOS_SIMULATOR,
            "tvos": AppleProfile.TVOS_SIMULATOR,
            "macos": AppleProfile.MACOS,
        }[platform]
    except KeyError as error:
        raise AppleValidationError("validation_plan_invalid") from error


@dataclass(frozen=True, slots=True)
class ProtectedAppleStage:
    identifier: str
    platform: str
    operation: str
    plan: AppleValidationPlan

    @property
    def needs_booted_simulator(self) -> bool:
        return self.operation == "test" and self.platform in {"ios", "tvos"}


@dataclass(frozen=True, slots=True)
class ProtectedApplePlan:
    repository: str
    admitted_sha: str
    source_trust: str
    stages: tuple[ProtectedAppleStage, ...]
    private_dependency_repository: str = ""
    private_dependency_sha: str = ""
    private_dependency_subdirectory: str = "."
    private_dependency_id: str = ""

    @property
    def private_dependency_used(self) -> bool:
        return bool(self.private_dependency_repository)

    @property
    def simulator_plans(self) -> tuple[AppleValidationPlan, ...]:
        unique: dict[str, AppleValidationPlan] = {}
        for stage in self.stages:
            if stage.plan.simulator is not None:
                simulator = stage.plan.simulator
                unique[simulator.platform] = stage.plan
        return tuple(unique.values())

    def planning_outputs(self) -> dict[str, str]:
        summary = json.dumps(
            [
                {
                    "id": stage.identifier,
                    "operation": stage.operation,
                    "platform": stage.platform,
                }
                for stage in self.stages
            ],
            sort_keys=True,
            separators=(",", ":"),
        )
        return {
            "result": "planned",
            "source_sha": self.admitted_sha,
            "validation_profile": "protected-full",
            "task_profile": "protected-full",
            "runner_profile": AppleRunnerCapability.APPLE.value,
            "planner_runner_profile": AppleRunnerCapability.PORTABLE.value,
            "workspace_profile": "apple",
            "timeout_minutes": "120",
            "source_trust": self.source_trust,
            "test_summary": summary,
            "private_dependency_used": str(self.private_dependency_used).lower(),
            "private_dependency_repository": self.private_dependency_repository,
            "private_dependency_sha": self.private_dependency_sha,
            "private_dependency_subdirectory": self.private_dependency_subdirectory,
            "private_dependency_id": self.private_dependency_id,
            "cleanup_result": "not-run",
            "failure_code": "",
        }


def _request(
    *,
    repository: str,
    admitted_sha: str,
    source_trust: str,
    profile: AppleProfile,
    working_directory: str,
    scheme: str | None,
    platform: str,
    destination_profile: str | None,
) -> AppleValidationRequest:
    return AppleValidationRequest(
        repository=repository,
        admitted_sha=admitted_sha,
        consumer_contract="caller-owned-protected-full",
        validation_profile=profile,
        source_trust=source_trust,
        working_directory=working_directory,
        scheme=scheme,
        platform=platform,
        destination_profile=destination_profile,
    )


def _script_stage(
    raw: Mapping[str, object],
    *,
    identifier: str,
    repository: str,
    admitted_sha: str,
    source_trust: str,
    working_directory: str,
    cleanup_paths: tuple[str, ...],
    expected_outputs: tuple[str, ...],
    contract: Mapping[str, Any],
) -> ProtectedAppleStage:
    for field in (
        "container",
        "scheme",
        "configuration",
        "test_plan",
    ):
        if raw[field] not in {None, ""}:
            _fail("validation_plan_invalid")
    if raw["resolved_files"] != [] or raw["xcodebuild_arguments"] != [] or raw["test_selectors"] != []:
        _fail("validation_plan_invalid")
    script = raw["script"]
    if not isinstance(script, Mapping) or set(script) != {"interpreter", "path", "arguments"}:
        _fail("validation_plan_invalid")
    interpreter = _plain(script["interpreter"], "validation_plan_invalid", maximum=16)
    if interpreter not in {"bash", "python3"}:
        _fail("validation_plan_invalid")
    script_path = _relative(script["path"], "script_rejected")
    if (interpreter == "bash" and not script_path.endswith(".sh")) or (
        interpreter == "python3" and not script_path.endswith(".py")
    ):
        _fail("script_rejected")
    command = AppleCommand(
        stage=AppleStage.REPOSITORY_RECOVERY,
        kind="bash-script" if interpreter == "bash" else "python3-script",
        action="run",
        script_path=script_path,
        fixed_arguments=_arguments(script["arguments"]),
        expected_outputs=expected_outputs,
    )
    plan = AppleValidationPlan(
        request=_request(
            repository=repository,
            admitted_sha=admitted_sha,
            source_trust=source_trust,
            profile=AppleProfile.REPOSITORY_RECOVERY,
            working_directory=working_directory,
            scheme=None,
            platform="apple",
            destination_profile=None,
        ),
        task_profile=f"protected-{identifier}",
        runner_profile=AppleRunnerCapability.APPLE,
        planner_runner_profile=AppleRunnerCapability.PORTABLE,
        workspace_profile="apple",
        timeout_minutes=120,
        toolchain=contract["_toolchain"],
        working_directory=working_directory,
        container=None,
        simulator=None,
        commands=(command,),
        protected_paths=(script_path,),
        cleanup_paths=cleanup_paths,
        environment_bindings=(),
        artifact_exception_id=None,
    )
    return ProtectedAppleStage(identifier, "source", "script", plan)


def _xcode_stage(
    raw: Mapping[str, object],
    *,
    identifier: str,
    platform: str,
    operation: str,
    repository: str,
    admitted_sha: str,
    source_trust: str,
    working_directory: str,
    cleanup_paths: tuple[str, ...],
    expected_outputs: tuple[str, ...],
    contract: Mapping[str, Any],
) -> ProtectedAppleStage:
    if platform not in {"ios", "tvos", "macos"} or raw["script"] is not None:
        _fail("validation_plan_invalid")
    container_raw = raw["container"]
    if not isinstance(container_raw, Mapping) or set(container_raw) != {"kind", "path"}:
        _fail("container_invalid")
    kind = _plain(container_raw["kind"], "container_invalid", maximum=16)
    if kind not in {"project", "workspace"}:
        _fail("container_invalid")
    container_path = _relative(container_raw["path"], "container_invalid")
    if (kind == "project" and not container_path.endswith(".xcodeproj")) or (
        kind == "workspace" and not container_path.endswith(".xcworkspace")
    ):
        _fail("container_invalid")
    scheme = _plain(raw["scheme"], "scheme_rejected", maximum=128)
    configuration = _plain(raw["configuration"], "configuration_rejected", maximum=64)
    if _SCHEME.fullmatch(scheme) is None or _CONFIGURATION.fullmatch(configuration) is None:
        _fail("validation_plan_invalid")
    test_plan = None
    if raw["test_plan"] not in {None, ""}:
        test_plan = _relative(raw["test_plan"], "test_plan_rejected")
        if not test_plan.endswith(".xctestplan"):
            _fail("test_plan_rejected")
    resolution = _plain(
        raw["package_resolution_mode"],
        "package_resolution_rejected",
        maximum=32,
    )
    if resolution not in {"locked", "disabled", "resolve-only"}:
        _fail("package_resolution_rejected")
    resolved_files = _paths(raw["resolved_files"], "package_resolution_rejected", 8)
    if resolution == "locked" and not resolved_files:
        _fail("package_resolution_rejected")
    selectors = _strings(
        raw["test_selectors"],
        "test_selector_rejected",
        maximum_items=16,
    )
    if operation != "test" and selectors:
        _fail("test_selector_rejected")
    arguments = list(_arguments(raw["xcodebuild_arguments"]))
    for selector in selectors:
        if _TEST_SELECTOR.fullmatch(selector) is None:
            _fail("test_selector_rejected")
        arguments.append(f"-only-testing:{selector}")
    container = AppleContainer(
        kind=kind,
        path=container_path,
        scheme=scheme,
        configuration=configuration,
        test_plan=test_plan,
        package_resolution_mode=resolution,
        resolved_files=resolved_files,
    )
    command = AppleCommand(
        stage=AppleStage.TEST if operation == "test" else AppleStage.BUILD,
        kind="xcodebuild",
        action=operation,
        script_path=None,
        fixed_arguments=tuple(arguments),
        expected_outputs=expected_outputs,
    )
    profile = _profile(platform, operation)
    public_platform = {"ios": "ios", "tvos": "tvos", "macos": "macos"}[platform]
    destination_profile = {
        "ios": "ios-simulator-default",
        "tvos": "tvos-simulator-default",
        "macos": "macos-unsigned",
    }[platform]
    protected_paths = [container_path, *resolved_files]
    if test_plan is not None:
        protected_paths.append(test_plan)
    plan = AppleValidationPlan(
        request=_request(
            repository=repository,
            admitted_sha=admitted_sha,
            source_trust=source_trust,
            profile=profile,
            working_directory=working_directory,
            scheme=scheme,
            platform=public_platform,
            destination_profile=destination_profile,
        ),
        task_profile=f"protected-{identifier}",
        runner_profile=AppleRunnerCapability.APPLE,
        planner_runner_profile=AppleRunnerCapability.PORTABLE,
        workspace_profile="apple",
        timeout_minutes=120,
        toolchain=contract["_toolchain"],
        working_directory=working_directory,
        container=container,
        simulator=_simulator_for(contract, platform),
        commands=(command,),
        protected_paths=tuple(dict.fromkeys(protected_paths)),
        cleanup_paths=cleanup_paths,
        environment_bindings=(),
        artifact_exception_id=None,
    )
    return ProtectedAppleStage(identifier, platform, operation, plan)


def _stage_plan(
    raw: Mapping[str, object],
    *,
    repository: str,
    admitted_sha: str,
    source_trust: str,
    contract: Mapping[str, Any],
) -> ProtectedAppleStage:
    expected = {
        "id",
        "platform",
        "operation",
        "working_directory",
        "container",
        "scheme",
        "configuration",
        "test_plan",
        "package_resolution_mode",
        "resolved_files",
        "script",
        "xcodebuild_arguments",
        "test_selectors",
        "expected_outputs",
        "cleanup_paths",
    }
    if set(raw) != expected:
        _fail("validation_plan_invalid")
    identifier = _plain(raw["id"], "validation_plan_invalid", maximum=32)
    if _IDENTIFIER.fullmatch(identifier) is None:
        _fail("validation_plan_invalid")
    platform = _plain(raw["platform"], "validation_plan_invalid", maximum=16).lower()
    operation = _plain(raw["operation"], "validation_plan_invalid", maximum=32).lower()
    if operation not in {*_XCODE_OPERATIONS, "script"}:
        _fail("validation_plan_invalid")
    working_directory = _relative(
        raw["working_directory"],
        "working_directory_rejected",
        allow_dot=True,
    )
    cleanup_paths = _paths(raw["cleanup_paths"], "cleanup_failed")
    expected_outputs = _paths(raw["expected_outputs"], "output_invalid")
    if operation == "script":
        return _script_stage(
            raw,
            identifier=identifier,
            repository=repository,
            admitted_sha=admitted_sha,
            source_trust=source_trust,
            working_directory=working_directory,
            cleanup_paths=cleanup_paths,
            expected_outputs=expected_outputs,
            contract=contract,
        )
    return _xcode_stage(
        raw,
        identifier=identifier,
        platform=platform,
        operation=operation,
        repository=repository,
        admitted_sha=admitted_sha,
        source_trust=source_trust,
        working_directory=working_directory,
        cleanup_paths=cleanup_paths,
        expected_outputs=expected_outputs,
        contract=contract,
    )


def build_protected_full_plan(
    raw_json: str,
    *,
    repository: str,
    admitted_sha: str,
    source_trust: str,
    contract: Mapping[str, Any],
    private_dependency_repository: str = "",
    private_dependency_sha: str = "",
    private_dependency_subdirectory: str = ".",
    private_dependency_id: str = "",
) -> ProtectedApplePlan:
    repository = _plain(repository, "invalid_input", maximum=255)
    admitted_sha = _plain(admitted_sha, "invalid_input", maximum=40)
    source_trust = _plain(source_trust, "source_trust_rejected", maximum=32)
    if _REPOSITORY.fullmatch(repository) is None or _FULL_SHA.fullmatch(admitted_sha) is None:
        _fail("invalid_input")
    if source_trust not in {"trusted-pr", "trusted-exact"}:
        _fail("source_trust_rejected")
    value = _json_object(raw_json)
    rows = value["stages"]
    if not isinstance(rows, list) or not 1 <= len(rows) <= _MAX_STAGES:
        _fail("validation_plan_invalid")
    stages = tuple(
        _stage_plan(
            row,
            repository=repository,
            admitted_sha=admitted_sha,
            source_trust=source_trust,
            contract=contract,
        )
        for row in rows
        if isinstance(row, Mapping)
    )
    if len(stages) != len(rows) or len({stage.identifier for stage in stages}) != len(stages):
        _fail("validation_plan_invalid")
    if not any(stage.operation in _XCODE_OPERATIONS for stage in stages):
        # Source-only recovery remains on the legacy/general-capacity path.
        _fail("validation_plan_invalid")

    dep_repository = _plain(
        private_dependency_repository,
        "private_dependency_invalid",
        allow_empty=True,
        maximum=255,
    )
    dep_sha = _plain(
        private_dependency_sha,
        "private_dependency_invalid",
        allow_empty=True,
        maximum=40,
    )
    dep_id = _plain(
        private_dependency_id,
        "private_dependency_invalid",
        allow_empty=True,
        maximum=32,
    )
    dep_subdirectory = safe_relative(
        private_dependency_subdirectory or ".",
        "private_dependency_subdirectory_invalid",
        allow_dot=True,
    )
    dependency_values = (dep_repository, dep_sha, dep_id)
    if any(dependency_values):
        if (
            not all(dependency_values)
            or _REPOSITORY.fullmatch(dep_repository) is None
            or _FULL_SHA.fullmatch(dep_sha) is None
            or _IDENTIFIER.fullmatch(dep_id) is None
        ):
            _fail("private_dependency_invalid")
    return ProtectedApplePlan(
        repository=repository,
        admitted_sha=admitted_sha,
        source_trust=source_trust,
        stages=stages,
        private_dependency_repository=dep_repository,
        private_dependency_sha=dep_sha,
        private_dependency_subdirectory=dep_subdirectory,
        private_dependency_id=dep_id,
    )


def verify_private_dependency(
    plan: ProtectedApplePlan,
    *,
    workflow_state_root: Path,
    environment: Mapping[str, str],
) -> Path | None:
    if not plan.private_dependency_used:
        if environment.get("CI_PRIVATE_DEPENDENCY_PATH", ""):
            _fail("private_dependency_rejected")
        return None
    if (
        environment.get("INPUT_PRIVATE_DEPENDENCY_VERIFIED") != "true"
        or environment.get("INPUT_PRIVATE_DEPENDENCY_REMOTES_ERASED") != "true"
        or environment.get("INPUT_PRIVATE_DEPENDENCY_CREDENTIALS_ERASED") != "true"
        or environment.get("INPUT_PRIVATE_DEPENDENCY_HEAD_SHA") != plan.private_dependency_sha
        or environment.get("INPUT_PRIVATE_DEPENDENCY_CHECKOUT_REPOSITORY")
        != plan.private_dependency_repository
        or environment.get("INPUT_PRIVATE_DEPENDENCY_CHECKOUT_ID") != plan.private_dependency_id
        or environment.get("INPUT_PRIVATE_DEPENDENCY_EXPECTED_SUBPATH")
        != plan.private_dependency_subdirectory
    ):
        _fail("private_dependency_unverified")
    raw = environment.get("CI_PRIVATE_DEPENDENCY_PATH", "")
    candidate = Path(raw)
    if not raw or not candidate.is_absolute() or candidate.is_symlink():
        _fail("private_dependency_path_invalid")
    expected = bounded_path(
        workflow_state_root,
        f"dependencies/{plan.private_dependency_id}",
        must_exist=True,
    )
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as error:
        raise AppleValidationError("private_dependency_path_invalid") from error
    if resolved != expected or not resolved.is_dir():
        _fail("private_dependency_path_invalid")
    subdirectory = bounded_path(
        resolved,
        plan.private_dependency_subdirectory,
        must_exist=True,
    )
    if not subdirectory.is_dir():
        _fail("private_dependency_subdirectory_invalid")
    return resolved


def _protected_hashes(
    plan: ProtectedApplePlan,
    source_root: Path,
) -> tuple[dict[str, str], ...]:
    return tuple(
        apple_execution.protected_hashes(source_root, stage.plan)
        for stage in plan.stages
    )


def _require_protected_unchanged(
    plan: ProtectedApplePlan,
    source_root: Path,
    before: Sequence[Mapping[str, str]],
) -> None:
    for stage, expected in zip(plan.stages, before, strict=True):
        actual = apple_execution.protected_hashes(source_root, stage.plan)
        if dict(expected) != actual:
            resolved = set(stage.plan.container.resolved_files) if stage.plan.container else set()
            if any(expected.get(path) != actual.get(path) for path in resolved):
                _fail("package_resolution_mutation")
            _fail("source_mutation")


def _stage_directories(
    shared: Mapping[str, Path],
    stage: ProtectedAppleStage,
) -> dict[str, Path]:
    result = dict(shared)
    result_bundles = shared["result-bundles"] / stage.identifier
    result_bundles.mkdir(parents=True, exist_ok=True)
    result["result-bundles"] = result_bundles
    return result


def execute_protected_full(
    plan: ProtectedApplePlan,
    *,
    source_root: Path,
    state_root: Path,
    runner: apple_execution.CommandRunner | None = None,
    environment: Mapping[str, str] | None = None,
) -> dict[str, str]:
    command_runner = runner or apple_execution.SubprocessCommandRunner()
    base_environment = dict(environment or {})
    apple_execution.verify_exact_source(source_root, plan.admitted_sha)
    for stage in plan.stages:
        apple_execution._validate_container_files(source_root, stage.plan)
    before = _protected_hashes(plan, source_root)
    env, shared_directories = apple_execution.isolated_environment(
        state_root,
        base_environment,
    )
    if plan.private_dependency_used:
        dependency = base_environment.get("CI_PRIVATE_DEPENDENCY_PATH", "")
        if not dependency:
            _fail("private_dependency_path_invalid")
        env["CI_PRIVATE_DEPENDENCY_PATH"] = dependency

    first_xcode = next(
        stage for stage in plan.stages if stage.operation in _XCODE_OPERATIONS
    )
    (
        xcode_version,
        xcode_build,
        swift_version,
        sdk_versions,
        _,
    ) = apple_execution.verify_toolchain(
        first_xcode.plan,
        source_root,
        state_root,
        command_runner,
        env,
    )
    summaries: list[dict[str, object]] = []
    ownership_context = (
        apple_execution._simulator_ownership(env, state_root)
        if plan.simulator_plans
        else nullcontext(None)
    )
    with ownership_context as ownership:
        for stage in plan.stages:
            lease = None
            if stage.needs_booted_simulator:
                if ownership is None:
                    _fail("simulator_ownership_invalid")
                lease = apple_execution.select_simulator(
                    stage.plan,
                    source_root,
                    state_root,
                    command_runner,
                    env,
                    ownership=ownership,
                )
            elif stage.platform in {"ios", "tvos"}:
                simulator = stage.plan.simulator
                if simulator is None:
                    _fail("simulator_contract_invalid")
                # Generic simulator destinations compile for the platform without
                # creating or booting any concrete simulator device.
                lease = apple_execution.SimulatorLease(
                    udid="",
                    destination=f"generic/platform={simulator.platform}",
                    redacted_identity="",
                    created=False,
                )
            stage_directories = _stage_directories(shared_directories, stage)
            output_verified = False
            for command in stage.plan.commands:
                output_verified = (
                    apple_execution._execute_command(
                        stage.plan,
                        command,
                        source_root,
                        state_root,
                        command_runner,
                        env,
                        stage_directories,
                        lease,
                    )
                    or output_verified
                )
            summaries.append(
                {
                    "id": stage.identifier,
                    "operation": stage.operation,
                    "output_verified": output_verified,
                    "platform": stage.platform,
                    "simulator_booted": stage.needs_booted_simulator,
                }
            )
            if stage.needs_booted_simulator:
                assert ownership is not None
                # Sequential tests must not leave one platform booted while a
                # later platform acquires its simulator.  Terminal cleanup still
                # remains the workflow-wide source/workspace residue boundary.
                apple_execution._cleanup_simulator_locked(
                    source_root,
                    state_root,
                    stage.plan,
                    runner=command_runner,
                    environment=env,
                    ownership=ownership,
                )
    _require_protected_unchanged(plan, source_root, before)
    apple_execution.verify_exact_source(source_root, plan.admitted_sha)
    evidence_id = hashlib.sha256(
        json.dumps(
            {
                "repository": plan.repository,
                "sha": plan.admitted_sha,
                "stages": summaries,
                "xcode": xcode_version,
                "xcode_build": xcode_build,
                "swift": swift_version,
                "sdks": sdk_versions,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return {
        "result": "success",
        "source_sha": plan.admitted_sha,
        "validation_profile": "protected-full",
        "task_profile": "protected-full",
        "test_summary": json.dumps(
            summaries,
            sort_keys=True,
            separators=(",", ":"),
        ),
        "runner_profile": AppleRunnerCapability.APPLE.value,
        "source_trust": plan.source_trust,
        "xcode_version": xcode_version,
        "xcode_build": xcode_build,
        "swift_version": swift_version,
        "sdk_versions_json": json.dumps(
            dict(sorted(sdk_versions.items())),
            sort_keys=True,
            separators=(",", ":"),
        ),
        "simulator_identity": "",
        "output_verified": str(
            any(bool(row["output_verified"]) for row in summaries)
        ).lower(),
        "artifact_exception_used": "false",
        "clean_tree": "true",
        "cleanup_result": "not-run",
        "failure_code": "",
        "evidence_id": evidence_id,
    }


def _cleanup_targets(
    plan: ProtectedApplePlan,
    source_root: Path,
    state_root: Path,
) -> tuple[Path, ...]:
    targets = [apple_execution._lexical_target(state_root, "apple-validation")]
    for stage in plan.stages:
        targets.extend(
            apple_execution._lexical_target(source_root, relative)
            for relative in stage.plan.cleanup_paths
        )
    return tuple(dict.fromkeys(targets))


def cleanup_protected_full(
    plan: ProtectedApplePlan,
    *,
    source_root: Path,
    state_root: Path,
    runner: apple_execution.CommandRunner | None = None,
    environment: Mapping[str, str] | None = None,
) -> None:
    command_runner = runner or apple_execution.SubprocessCommandRunner()
    env = dict(environment or {})
    ownership_context = (
        apple_execution._simulator_ownership(env, state_root)
        if plan.simulator_plans
        else nullcontext(None)
    )
    with ownership_context as ownership:
        if plan.simulator_plans:
            assert ownership is not None
            for simulator_plan in plan.simulator_plans:
                apple_execution._cleanup_simulator_locked(
                    source_root,
                    state_root,
                    simulator_plan,
                    runner=command_runner,
                    environment=env,
                    ownership=ownership,
                )
        for target in _cleanup_targets(plan, source_root, state_root):
            apple_execution._remove_no_follow(target)


def assert_zero_protected_full_residue(
    plan: ProtectedApplePlan,
    *,
    source_root: Path,
    state_root: Path,
    runner: apple_execution.CommandRunner | None = None,
    environment: Mapping[str, str] | None = None,
) -> None:
    if any(
        apple_execution._lstat(target) is not None
        for target in _cleanup_targets(plan, source_root, state_root)
    ):
        _fail("cleanup_failed")
    if not plan.simulator_plans:
        return
    command_runner = runner or apple_execution.SubprocessCommandRunner()
    env = dict(environment or {})
    with apple_execution._simulator_ownership(env, state_root) as ownership:
        if ownership.rows:
            _fail("cleanup_failed")
        for simulator_plan in plan.simulator_plans:
            payload = apple_execution._device_inventory(
                command_runner,
                source_root=source_root,
                state_root=state_root,
                env=env,
                available_only=False,
                failure_code="cleanup_failed",
                record_log=False,
            )
            if apple_execution._exact_owned_candidates(
                simulator_plan,
                payload,
                require_available=False,
            ):
                _fail("cleanup_failed")
