"""Typed contracts for bounded Flutter validation."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Mapping, Sequence


class FlutterProfile(str, Enum):
    SOURCE_AUDIT = "source-audit"
    QUALITY = "quality"
    CANONICAL_GATE = "canonical-gate"
    ANDROID_DEBUG = "android-debug"
    IOS_SIMULATOR = "ios-simulator"
    COMPATIBILITY_SMOKE = "compatibility-smoke"
    DEVICE_HANDOFF = "device-handoff"


class RunnerCapability(str, Enum):
    PORTABLE = "portable"
    MOBILE = "mobile"
    APPLE = "apple"


class FlutterStage(str, Enum):
    SOURCE_AUDIT = "source-audit"
    JDK_VERIFY = "jdk-verify"
    RUNTIME_VERIFY = "runtime-verify"
    GRADLE_VERIFY = "gradle-verify"
    DEPENDENCY_RESTORE = "dependency-restore"
    NODE_COMPOSITION = "node-composition"
    QUALITY = "quality"
    TESTS = "tests"
    CANONICAL_GATE = "canonical-gate"
    ANDROID_DEBUG = "android-debug"
    IOS_SIMULATOR = "ios-simulator"
    COMPATIBILITY_SMOKE = "compatibility-smoke"
    DEVICE_HANDOFF = "device-handoff"
    CLEANUP = "cleanup"


@dataclass(frozen=True, slots=True)
class FlutterToolchain:
    flutter_version: str
    dart_version: str
    framework_revision: str
    engine_revision: str
    setup_action: str
    gradle_version: str = ""
    jdk_distribution: str = ""
    jdk_version: str = ""
    java_version: str = ""
    java_runtime_version: str = ""
    java_vendor: str = ""
    javac_version: str = ""
    jdk_setup_action: str = ""


@dataclass(frozen=True, slots=True)
class FlutterPin:
    version: str
    sources: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class FlutterCommand:
    command_id: str
    stage: FlutterStage
    argv: tuple[str, ...]
    working_directory: str = "."
    expected_outputs: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class FlutterRequest:
    repository: str
    admitted_sha: str
    consumer_contract: str
    validation_profile: FlutterProfile
    source_trust: str


@dataclass(frozen=True, slots=True)
class FlutterPlan:
    request: FlutterRequest
    runner_profile: RunnerCapability
    install_required: bool
    workspace_profile: str
    timeout_minutes: int
    pin: FlutterPin | None
    toolchain: FlutterToolchain
    stages: tuple[FlutterStage, ...]
    commands: tuple[FlutterCommand, ...]
    node_composition: Mapping[str, str] | None
    gate_path: str | None
    device_handoff: Mapping[str, str] | None

    def planning_outputs(self) -> dict[str, str]:
        return {
            "result": "planned",
            "source_sha": self.request.admitted_sha,
            "consumer_contract": self.request.consumer_contract,
            "validation_profile": self.request.validation_profile.value,
            "runner_profile": self.runner_profile.value,
            "workspace_profile": self.workspace_profile,
            "timeout_minutes": str(self.timeout_minutes),
            "source_trust": self.request.source_trust,
            "flutter_version": self.toolchain.flutter_version,
            "dart_version": self.toolchain.dart_version,
            "gradle_version": self.toolchain.gradle_version,
            "jdk_distribution": self.toolchain.jdk_distribution,
            "jdk_version": self.toolchain.jdk_version,
            "java_version": self.toolchain.java_version,
            "java_runtime_version": self.toolchain.java_runtime_version,
            "java_vendor": self.toolchain.java_vendor,
            "javac_version": self.toolchain.javac_version,
            "jdk_setup_action": self.toolchain.jdk_setup_action,
            "install_required": str(self.install_required).lower(),
            "stage_order_json": _compact_json([stage.value for stage in self.stages]),
            "node_composition": str(self.node_composition is not None).lower(),
            "failure_code": "",
            "primary_failure_code": "",
            "cleanup_failure_code": "",
        }


@dataclass(frozen=True, slots=True)
class FlutterResult:
    plan: FlutterPlan
    status: str
    completed_stages: tuple[FlutterStage, ...]
    flutter_version: str
    dart_version: str
    framework_revision: str
    engine_revision: str
    lockfile_sha256: str
    clean_tree: bool
    cleanup_result: str
    output_verified: bool
    evidence_id: str
    device_handoff: Mapping[str, str] | None = None
    gradle_version: str = ""
    java_version: str = ""
    java_runtime_version: str = ""
    java_vendor: str = ""
    javac_version: str = ""
    pub_cache_path: str = ""
    persistent_pub_cache_unchanged: bool = False

    def output_values(self) -> dict[str, str]:
        return {
            "result": self.status,
            "source_sha": self.plan.request.admitted_sha,
            "consumer_contract": self.plan.request.consumer_contract,
            "validation_profile": self.plan.request.validation_profile.value,
            "runner_profile": self.plan.runner_profile.value,
            "workspace_profile": self.plan.workspace_profile,
            "timeout_minutes": str(self.plan.timeout_minutes),
            "source_trust": self.plan.request.source_trust,
            "flutter_version": self.flutter_version,
            "dart_version": self.dart_version,
            "framework_revision": self.framework_revision,
            "engine_revision": self.engine_revision,
            "gradle_version": self.gradle_version,
            "jdk_distribution": self.plan.toolchain.jdk_distribution,
            "jdk_version": self.plan.toolchain.jdk_version,
            "java_version": self.java_version,
            "java_runtime_version": self.java_runtime_version,
            "java_vendor": self.java_vendor,
            "javac_version": self.javac_version,
            "lockfile_sha256": self.lockfile_sha256,
            "stage_summary_json": _compact_json(
                [stage.value for stage in self.completed_stages]
            ),
            "clean_tree": str(self.clean_tree).lower(),
            "cleanup_result": self.cleanup_result,
            "output_verified": str(self.output_verified).lower(),
            "artifact_exception_used": "false",
            "device_handoff_json": _compact_json(self.device_handoff or {}),
            "evidence_id": self.evidence_id,
            "pub_cache_path": self.pub_cache_path,
            "persistent_pub_cache_unchanged": str(
                self.persistent_pub_cache_unchanged
            ).lower(),
            "failure_code": "",
            "primary_failure_code": "",
            "cleanup_failure_code": "",
        }


def _compact_json(value: object) -> str:
    import json

    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def command_ids(commands: Sequence[FlutterCommand]) -> tuple[str, ...]:
    return tuple(command.command_id for command in commands)
