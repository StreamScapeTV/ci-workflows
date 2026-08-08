"""Execution engine for the bounded Flutter validation contract."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Protocol, Sequence

from .flutter_contract import (
    FlutterValidationError,
    checked_in_script,
    fail,
    parse_runtime_identity,
    source_authority_hashes,
)
from .flutter_types import FlutterPlan, FlutterProfile, FlutterResult, FlutterStage


@dataclass(frozen=True, slots=True)
class CommandOutcome:
    returncode: int
    stdout: str
    stderr: str


class CommandRunner(Protocol):
    def run(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        env: Mapping[str, str],
    ) -> CommandOutcome: ...


class SubprocessCommandRunner:
    def run(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        env: Mapping[str, str],
    ) -> CommandOutcome:
        completed = subprocess.run(
            list(argv),
            cwd=cwd,
            env=dict(env),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        return CommandOutcome(completed.returncode, completed.stdout, completed.stderr)


def _state_directory(state_root: Path) -> Path:
    path = state_root / "flutter-validation"
    if path.is_symlink():
        fail("cleanup_failed")
    path.mkdir(parents=True, exist_ok=True)
    return path


def isolated_environment(
    state_root: Path,
    base: Mapping[str, str] | None = None,
) -> tuple[dict[str, str], dict[str, Path]]:
    root = _state_directory(state_root)
    directories = {
        name: root / name
        for name in (
            "home",
            "pub-cache",
            "flutter-state",
            "gradle-home",
            "cocoapods-home",
            "derived-data",
            "tmp",
            "logs",
            "reports",
            "build-output",
        )
    }
    for path in directories.values():
        if path.is_symlink():
            fail("cleanup_failed")
        path.mkdir(parents=True, exist_ok=True)
    blocked = {
        "FLUTTER_STORAGE_BASE_URL",
        "PUB_HOSTED_URL",
        "CI_FLUTTER_VERSION",
        "FLUTTER_VERSION",
        "DEVICE_ID",
        "ANDROID_SERIAL",
        "FASTLANE_SESSION",
        "MATCH_PASSWORD",
        "APPLE_CERTIFICATE",
        "APPLE_CERTIFICATE_PASSWORD",
        "APP_STORE_CONNECT_API_KEY",
        "SUPABASE_ACCESS_TOKEN",
        "DATABASE_URL",
    }
    env = {
        key: value
        for key, value in (base or os.environ).items()
        if key not in blocked
    }
    env.update(
        HOME=str(directories["home"]),
        PUB_CACHE=str(directories["pub-cache"]),
        FLUTTER_SUPPRESS_ANALYTICS="true",
        CI="true",
        GRADLE_USER_HOME=str(directories["gradle-home"]),
        COCOAPODS_HOME=str(directories["cocoapods-home"]),
        CI_DERIVED_DATA=str(directories["derived-data"]),
        TMPDIR=str(directories["tmp"]),
        XDG_CACHE_HOME=str(directories["home"] / ".cache"),
        XDG_CONFIG_HOME=str(directories["home"] / ".config"),
        XDG_STATE_HOME=str(directories["home"] / ".local" / "state"),
        NO_COLOR="1",
    )
    return env, directories


def _render_argv(argv: Sequence[str], values: Mapping[str, str]) -> tuple[str, ...]:
    rendered: list[str] = []
    for item in argv:
        output = item
        for key, value in values.items():
            output = output.replace("${" + key + "}", value)
        if "${" in output or "}" in output:
            fail("command_profile_rejected")
        rendered.append(output)
    return tuple(rendered)


def _run_checked(
    runner: CommandRunner,
    argv: Sequence[str],
    *,
    cwd: Path,
    env: Mapping[str, str],
    failure_code: str,
) -> CommandOutcome:
    outcome = runner.run(argv, cwd=cwd, env=env)
    if outcome.returncode != 0:
        fail(failure_code)
    return outcome


def _verify_authority(source_root: Path, before: Mapping[str, str]) -> str:
    after = source_authority_hashes(source_root)
    if dict(before) != after:
        fail("lockfile_drift")
    return after["pubspec.lock"]


def _assert_clean_source(source_root: Path) -> None:
    git_marker = source_root / ".git"
    if not git_marker.exists() and not git_marker.is_file():
        return
    completed = subprocess.run(
        ["git", "status", "--porcelain=v1", "--untracked-files=all"],
        cwd=source_root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if completed.returncode != 0 or completed.stdout.strip():
        fail("dirty_source")


def _verify_expected_outputs(source_root: Path, expected: Sequence[str]) -> bool:
    for value in expected:
        path = source_root / value
        if not path.exists() or path.is_symlink():
            fail("output_missing")
    return bool(expected)


def _compose_node(
    plan: FlutterPlan,
    contract_root: Path,
    source_root: Path,
    state_root: Path,
    runner: CommandRunner,
    env: Mapping[str, str],
) -> None:
    composition = plan.node_composition
    if composition is None:
        return
    node_script = contract_root / "scripts" / "ci" / "node.py"
    if not node_script.is_file() or node_script.is_symlink():
        fail("node_composition_failed")
    node_env = dict(env)
    node_env.update(
        INPUT_ADMITTED_SHA=plan.request.admitted_sha,
        INPUT_VALIDATION_PROFILE=composition["validation_profile"],
        INPUT_VERSION_FILE=composition.get("version_file", ""),
        INPUT_NODE_VERSION=composition.get("node_version", ""),
        INPUT_WORKING_DIRECTORY=composition.get("working_directory", "."),
        INPUT_INSTALL_PROFILE=composition["install_profile"],
        INPUT_COMMAND_PROFILE=composition["command_profile"],
        INPUT_SCRIPT_PATH=composition.get("script_path", ""),
        INPUT_STATIC_OUTPUT_DIRECTORY="",
        INPUT_OUTPUT_VERIFIER_PATH="",
        INPUT_PUBLIC_ENVIRONMENT="",
        INPUT_ARTIFACT_EXCEPTION_ID="",
        GITHUB_WORKSPACE=str(source_root.parent),
        RUNNER_TEMP=str(state_root.parent),
    )
    _run_checked(
        runner,
        (
            "python3",
            str(node_script),
            "--phase",
            "execute",
            "--source-root",
            source_root.name,
        ),
        cwd=contract_root,
        env=node_env,
        failure_code="node_composition_failed",
    )


def execute_flutter_plan(
    *,
    plan: FlutterPlan,
    contract_root: Path,
    source_root: Path,
    state_root: Path,
    runner: CommandRunner | None = None,
    environment: Mapping[str, str] | None = None,
) -> FlutterResult:
    command_runner = runner or SubprocessCommandRunner()
    before = source_authority_hashes(source_root)
    env, directories = isolated_environment(state_root, environment)
    completed: list[FlutterStage] = []
    identity = {
        "flutter_version": plan.toolchain.flutter_version,
        "dart_version": plan.toolchain.dart_version,
        "framework_revision": plan.toolchain.framework_revision,
        "engine_revision": plan.toolchain.engine_revision,
    }
    output_verified = False
    cleanup_result = "not-run"
    try:
        if plan.request.validation_profile is FlutterProfile.SOURCE_AUDIT:
            if plan.install_required:
                fail("source_audit_install_rejected")
        elif plan.request.validation_profile is FlutterProfile.DEVICE_HANDOFF:
            if plan.install_required:
                fail("device_boundary_rejected")
        else:
            version = _run_checked(
                command_runner,
                ("flutter", "--version", "--machine"),
                cwd=source_root,
                env=env,
                failure_code="runtime_identity_invalid",
            )
            identity = parse_runtime_identity(version.stdout, plan.toolchain)
            completed.append(FlutterStage.RUNTIME_VERIFY)

        if plan.node_composition is not None:
            _compose_node(
                plan,
                contract_root,
                source_root,
                state_root,
                command_runner,
                env,
            )
            completed.append(FlutterStage.NODE_COMPOSITION)

        values = {
            "source_sha": plan.request.admitted_sha,
            "run_number": env.get("GITHUB_RUN_NUMBER", "0"),
            "derived_data": str(directories["derived-data"]),
        }
        for command in plan.commands:
            cwd = source_root / command.working_directory
            resolved = cwd.resolve(strict=False)
            source_resolved = source_root.resolve()
            if resolved != source_resolved and source_resolved not in resolved.parents:
                fail("command_profile_rejected")
            if not cwd.is_dir() or cwd.is_symlink():
                fail("command_profile_rejected")
            argv = _render_argv(command.argv, values)
            if argv[0] == "checked-in-script":
                script = checked_in_script(source_root, argv[1])
                argv = ("bash", str(script), *argv[2:])
            _run_checked(
                command_runner,
                argv,
                cwd=cwd,
                env=env,
                failure_code="command_failed",
            )
            completed.append(command.stage)
            if command.expected_outputs:
                output_verified = (
                    _verify_expected_outputs(source_root, command.expected_outputs)
                    or output_verified
                )

        lock_hash = _verify_authority(source_root, before)
        cleanup_flutter_state(source_root, state_root)
        cleanup_result = "success"
        assert_zero_flutter_residue(source_root, state_root)
        _assert_clean_source(source_root)
        completed.append(FlutterStage.CLEANUP)
        evidence_id = hashlib.sha256(
            json.dumps(
                {
                    "consumer": plan.request.consumer_contract,
                    "profile": plan.request.validation_profile.value,
                    "sha": plan.request.admitted_sha,
                    "stages": [stage.value for stage in completed],
                    "flutter": identity["flutter_version"],
                    "dart": identity["dart_version"],
                    "lock": lock_hash,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        return FlutterResult(
            plan=plan,
            status="success",
            completed_stages=tuple(completed),
            flutter_version=identity["flutter_version"],
            dart_version=identity["dart_version"],
            framework_revision=identity["framework_revision"],
            engine_revision=identity["engine_revision"],
            lockfile_sha256=lock_hash,
            clean_tree=True,
            cleanup_result=cleanup_result,
            output_verified=output_verified,
            evidence_id=evidence_id,
            device_handoff=plan.device_handoff,
        )
    except FlutterValidationError:
        cleanup_flutter_state(source_root, state_root)
        raise


def cleanup_flutter_state(source_root: Path, state_root: Path) -> None:
    targets = (
        state_root / "flutter-validation",
        source_root / "build",
        source_root / ".dart_tool",
        source_root / "coverage",
        source_root / "ios" / "Pods",
        source_root / "ios" / ".symlinks",
        source_root / "ios" / "Flutter" / "ephemeral",
        source_root / "ios" / "Flutter" / "Generated.xcconfig",
        source_root / "ios" / "Flutter" / "flutter_export_environment.sh",
        source_root / "android" / ".gradle",
    )
    for path in targets:
        if path.is_symlink() or path.is_file():
            path.unlink(missing_ok=True)
        elif path.is_dir():
            shutil.rmtree(path)


def assert_zero_flutter_residue(source_root: Path, state_root: Path) -> None:
    remaining = [
        str(path)
        for path in (
            state_root / "flutter-validation",
            source_root / "build",
            source_root / ".dart_tool",
            source_root / "coverage",
            source_root / "ios" / "Pods",
            source_root / "ios" / ".symlinks",
            source_root / "ios" / "Flutter" / "ephemeral",
            source_root / "ios" / "Flutter" / "Generated.xcconfig",
            source_root / "ios" / "Flutter" / "flutter_export_environment.sh",
            source_root / "android" / ".gradle",
        )
        if path.exists() or path.is_symlink()
    ]
    if remaining:
        fail("cleanup_failed")
