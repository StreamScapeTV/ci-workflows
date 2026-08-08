"""Typed models and stable errors for reusable Apple validation."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import Enum
from typing import Mapping

_SAFE_CODE = re.compile(r"^[a-z][a-z0-9_]{2,95}$")


class AppleValidationError(RuntimeError):
    """Fail-closed Apple validation error carrying one stable code."""

    def __init__(self, code: str, *, cleanup_failed: bool = False) -> None:
        if _SAFE_CODE.fullmatch(code) is None:
            raise ValueError("apple validation error code must be safe")
        self.code = code
        self.cleanup_failed = cleanup_failed
        super().__init__(code)


class AppleProfile(str, Enum):
    SOURCE_AUDIT = "source-audit"
    SWIFT_PACKAGE = "swift-package"
    IOS_SIMULATOR = "ios-simulator"
    TVOS_SIMULATOR = "tvos-simulator"
    MACOS = "macos"
    NATIVE_DEPENDENCY_PREPARATION = "native-dependency-preparation"
    REPOSITORY_RECOVERY = "repository-recovery"


class AppleRunnerCapability(str, Enum):
    PORTABLE = "portable"
    APPLE = "apple"


class AppleStage(str, Enum):
    SOURCE_AUDIT = "source-audit"
    TOOLCHAIN_VERIFY = "toolchain-verify"
    SDK_VERIFY = "sdk-verify"
    SIMULATOR_SELECT = "simulator-select"
    PACKAGE_RESOLVE = "package-resolve"
    BUILD = "build"
    TEST = "test"
    SWIFT_TEST = "swift-test"
    DEPENDENCY_PREPARATION = "dependency-preparation"
    REPOSITORY_RECOVERY = "repository-recovery"
    OUTPUT_VERIFY = "output-verify"
    CLEANUP = "cleanup"


@dataclass(frozen=True, slots=True)
class AppleToolchain:
    xcode_version: str
    xcode_build: str
    swift_version: str
    sdk_versions: tuple[tuple[str, str], ...]
    simulator_runtimes: tuple[tuple[str, str, str], ...]


@dataclass(frozen=True, slots=True)
class AppleContainer:
    kind: str
    path: str
    scheme: str
    configuration: str
    test_plan: str | None
    package_resolution_mode: str
    resolved_files: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AppleSimulator:
    platform: str
    runtime_identifier: str
    runtime_version: str
    device_name_prefix: str
    device_type: str
    device_type_identifier: str
    device_family: str
    allow_create: bool


@dataclass(frozen=True, slots=True)
class AppleCommand:
    stage: AppleStage
    kind: str
    action: str
    script_path: str | None = None
    fixed_arguments: tuple[str, ...] = ()
    expected_outputs: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class AppleValidationRequest:
    repository: str
    admitted_sha: str
    consumer_contract: str
    validation_profile: AppleProfile
    source_trust: str
    working_directory: str | None = None
    project_path: str | None = None
    scheme: str | None = None
    configuration: str | None = None
    test_plan: str | None = None
    artifact_exception_id: str | None = None


@dataclass(frozen=True, slots=True)
class AppleValidationPlan:
    request: AppleValidationRequest
    task_profile: str
    runner_profile: AppleRunnerCapability
    planner_runner_profile: AppleRunnerCapability
    workspace_profile: str
    timeout_minutes: int
    toolchain: AppleToolchain
    working_directory: str
    container: AppleContainer | None
    simulator: AppleSimulator | None
    commands: tuple[AppleCommand, ...]
    protected_paths: tuple[str, ...]
    cleanup_paths: tuple[str, ...]
    environment_bindings: tuple[tuple[str, str], ...]
    artifact_exception_id: str | None

    @property
    def requires_simulator(self) -> bool:
        return self.simulator is not None

    def planning_outputs(self) -> dict[str, str]:
        return {
            "result": "planned",
            "source_sha": self.request.admitted_sha,
            "consumer_contract": self.request.consumer_contract,
            "validation_profile": self.request.validation_profile.value,
            "task_profile": self.task_profile,
            "runner_profile": self.runner_profile.value,
            "planner_runner_profile": self.planner_runner_profile.value,
            "workspace_profile": self.workspace_profile,
            "timeout_minutes": str(self.timeout_minutes),
            "source_trust": self.request.source_trust,
            "xcode_version": self.toolchain.xcode_version,
            "xcode_build": self.toolchain.xcode_build,
            "swift_version": self.toolchain.swift_version,
            "destination_family": (
                self.simulator.platform
                if self.simulator is not None
                else self.request.validation_profile.value
            ),
            "artifact_exception_used": "false",
            "clean_tree": "false",
            "cleanup_result": "not-run",
            "failure_code": "",
            "evidence_id": "",
        }


@dataclass(frozen=True, slots=True)
class AppleValidationResult:
    plan: AppleValidationPlan
    status: str
    completed_stages: tuple[AppleStage, ...]
    xcode_version: str
    xcode_build: str
    swift_version: str
    sdk_versions: Mapping[str, str]
    simulator_identity: str | None
    output_verified: bool
    clean_tree: bool
    cleanup_result: str
    artifact_exception_used: bool
    evidence_id: str

    def output_values(self) -> dict[str, str]:
        summary = json.dumps(
            {
                "profile": self.plan.request.validation_profile.value,
                "stages": [stage.value for stage in self.completed_stages],
                "status": self.status,
                "task_profile": self.plan.task_profile,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return {
            "result": self.status,
            "source_sha": self.plan.request.admitted_sha,
            "consumer_contract": self.plan.request.consumer_contract,
            "validation_profile": self.plan.request.validation_profile.value,
            "task_profile": self.plan.task_profile,
            "test_summary": summary,
            "runner_profile": self.plan.runner_profile.value,
            "source_trust": self.plan.request.source_trust,
            "xcode_version": self.xcode_version,
            "xcode_build": self.xcode_build,
            "swift_version": self.swift_version,
            "sdk_versions_json": json.dumps(
                dict(sorted(self.sdk_versions.items())),
                sort_keys=True,
                separators=(",", ":"),
            ),
            "simulator_identity": self.simulator_identity or "",
            "output_verified": str(self.output_verified).lower(),
            "artifact_exception_used": str(self.artifact_exception_used).lower(),
            "clean_tree": str(self.clean_tree).lower(),
            "cleanup_result": self.cleanup_result,
            "failure_code": "",
            "evidence_id": self.evidence_id,
        }
