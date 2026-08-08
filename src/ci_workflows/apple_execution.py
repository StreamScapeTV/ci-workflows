"""Hermetic Apple toolchain, simulator, build, and cleanup execution."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Mapping, Protocol, Sequence

from .apple_contract import bounded_path, fail, regular_path, safe_relative
from .apple_types import (
    AppleCommand,
    AppleProfile,
    AppleStage,
    AppleValidationError,
    AppleValidationPlan,
    AppleValidationResult,
)

_XCODE = re.compile(r"^Xcode\s+([0-9]+(?:\.[0-9]+){1,2})\s*$", re.MULTILINE)
_XCODE_BUILD = re.compile(r"^Build version\s+([0-9A-Z]+)\s*$", re.MULTILINE)
_SWIFT = re.compile(r"Apple Swift version\s+([0-9]+(?:\.[0-9]+){1,2})")
_UDID = re.compile(
    r"^[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-"
    r"[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}$"
)
_SECRET = re.compile(
    r"(?i)(token|password|authorization|secret|keychain|provisioning)\s*[:=]\s*\S+"
)


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
        timeout_seconds: int,
    ) -> CommandOutcome: ...


class SubprocessCommandRunner:
    def run(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        env: Mapping[str, str],
        timeout_seconds: int,
    ) -> CommandOutcome:
        try:
            completed = subprocess.run(
                list(argv),
                cwd=cwd,
                env=dict(env),
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=timeout_seconds,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise AppleValidationError("command_failed") from error
        return CommandOutcome(
            completed.returncode,
            completed.stdout or "",
            completed.stderr or "",
        )


@dataclass(frozen=True, slots=True)
class SimulatorLease:
    udid: str
    destination: str
    redacted_identity: str
    created: bool


def sanitize(text: str, roots: Sequence[Path] = ()) -> str:
    sanitized = text
    for root in roots:
        sanitized = sanitized.replace(str(root), "<state>")
    sanitized = re.sub(
        r"(?i)https?://[^\s/@]+:[^\s/@]+@",
        "https://<redacted>@",
        sanitized,
    )
    return "\n".join(_SECRET.sub(r"\1=<redacted>", sanitized).splitlines()[-160:])


def _run(
    runner: CommandRunner,
    argv: Sequence[str],
    *,
    cwd: Path,
    env: Mapping[str, str],
    timeout_seconds: int,
    failure_code: str,
    state_root: Path | None = None,
    stage: str = "command",
    check: bool = True,
) -> CommandOutcome:
    if not argv or any(not isinstance(item, str) or not item for item in argv):
        fail("invalid_input")
    try:
        outcome = runner.run(
            argv,
            cwd=cwd,
            env=env,
            timeout_seconds=timeout_seconds,
        )
    except AppleValidationError:
        raise
    except Exception as error:  # pragma: no cover - defensive adapter boundary
        raise AppleValidationError(failure_code) from error
    if state_root is not None:
        if re.fullmatch(r"[a-z][a-z0-9-]{1,63}", stage) is None:
            fail("invalid_input")
        logs = _state_directory(state_root) / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        (logs / f"{stage}.log").write_text(
            sanitize(outcome.stdout + outcome.stderr, (cwd, state_root)),
            encoding="utf-8",
        )
    if check and outcome.returncode != 0:
        fail(failure_code)
    return outcome


def _state_directory(state_root: Path) -> Path:
    if not state_root.is_absolute():
        fail("invalid_input")
    path = state_root / "apple-validation"
    if path.is_symlink():
        fail("cleanup_failed")
    path.mkdir(parents=True, exist_ok=True)
    return path


def isolated_environment(
    state_root: Path,
    base: Mapping[str, str] | None = None,
) -> tuple[dict[str, str], dict[str, Path]]:
    root = _state_directory(state_root)
    names = (
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
    )
    directories = {name: root / name for name in names}
    for path in directories.values():
        if path.is_symlink():
            fail("cleanup_failed")
        path.mkdir(parents=True, exist_ok=True)
    blocked = {
        "DEVELOPER_DIR",
        "CODE_SIGN_IDENTITY",
        "EXPANDED_CODE_SIGN_IDENTITY",
        "PROVISIONING_PROFILE",
        "PROVISIONING_PROFILE_SPECIFIER",
        "DEVELOPMENT_TEAM",
        "OTHER_CODE_SIGN_FLAGS",
        "FASTLANE_SESSION",
        "MATCH_PASSWORD",
        "APP_STORE_CONNECT_API_KEY",
        "APPLE_CERTIFICATE",
        "APPLE_CERTIFICATE_PASSWORD",
        "NOTARYTOOL_PASSWORD",
        "CI_KEYCHAIN_PATH",
        "CI_KEYCHAIN_PASSWORD",
        "DEVICE_UDID",
        "DESTINATION",
        "RUNNER_LABELS",
        "KUBECONFIG",
        "DATABASE_URL",
        "SUPABASE_ACCESS_TOKEN",
    }
    env = {
        key: value
        for key, value in (base or os.environ).items()
        if key not in blocked
    }
    env.update(
        HOME=str(directories["home"]),
        TMPDIR=str(directories["tmp"]),
        CFFIXED_USER_HOME=str(directories["home"]),
        XDG_CACHE_HOME=str(directories["caches"]),
        SWIFTPM_MODULECACHE_OVERRIDE=str(directories["swiftpm"] / "modules"),
        CLANG_MODULE_CACHE_PATH=str(directories["swiftpm"] / "clang-modules"),
        COCOAPODS_HOME=str(directories["cocoapods"]),
        LANG="en_US.UTF-8",
        LC_ALL="en_US.UTF-8",
        TZ="UTC",
        CI="true",
        GITHUB_ACTIONS="true",
        NSUnbufferedIO="YES",
    )
    return env, directories


def parse_xcode_identity(output: str) -> tuple[str, str]:
    version = _XCODE.search(output)
    build = _XCODE_BUILD.search(output)
    if version is None or build is None:
        fail("toolchain_identity_invalid")
    return version.group(1), build.group(1)


def parse_swift_identity(output: str) -> str:
    match = _SWIFT.search(output)
    if match is None:
        fail("toolchain_identity_invalid")
    return match.group(1)


def verify_toolchain(
    plan: AppleValidationPlan,
    source_root: Path,
    state_root: Path,
    runner: CommandRunner,
    env: Mapping[str, str],
) -> tuple[str, str, str, dict[str, str], dict[str, object] | None]:
    xcode = _run(
        runner,
        ("xcodebuild", "-version"),
        cwd=source_root,
        env=env,
        timeout_seconds=30,
        failure_code="toolchain_mismatch",
        state_root=state_root,
        stage="xcode-version",
    )
    xcode_version, xcode_build = parse_xcode_identity(xcode.stdout + xcode.stderr)
    if (
        xcode_version != plan.toolchain.xcode_version
        or xcode_build != plan.toolchain.xcode_build
    ):
        fail("toolchain_mismatch")
    swift = _run(
        runner,
        ("swift", "--version"),
        cwd=source_root,
        env=env,
        timeout_seconds=30,
        failure_code="toolchain_mismatch",
        state_root=state_root,
        stage="swift-version",
    )
    swift_version = parse_swift_identity(swift.stdout + swift.stderr)
    if swift_version != plan.toolchain.swift_version:
        fail("toolchain_mismatch")
    sdk_versions: dict[str, str] = {}
    for sdk, expected in plan.toolchain.sdk_versions:
        outcome = _run(
            runner,
            ("xcrun", "--sdk", sdk, "--show-sdk-version"),
            cwd=source_root,
            env=env,
            timeout_seconds=30,
            failure_code="sdk_missing",
            state_root=state_root,
            stage=f"sdk-{sdk}",
        )
        actual = outcome.stdout.strip()
        if actual != expected:
            fail("sdk_mismatch")
        sdk_versions[sdk] = actual
    runtime_payload: dict[str, object] | None = None
    if plan.requires_simulator:
        outcome = _run(
            runner,
            ("xcrun", "simctl", "list", "runtimes", "-j"),
            cwd=source_root,
            env=env,
            timeout_seconds=30,
            failure_code="runtime_missing",
            state_root=state_root,
            stage="simulator-runtimes",
        )
        try:
            runtime_payload = json.loads(outcome.stdout)
        except json.JSONDecodeError as error:
            raise AppleValidationError("runtime_malformed") from error
        rows = runtime_payload.get("runtimes") if isinstance(runtime_payload, dict) else None
        if not isinstance(rows, list):
            fail("runtime_malformed")
        available = {
            (row.get("identifier"), row.get("version"))
            for row in rows
            if isinstance(row, dict) and row.get("isAvailable", True) is True
        }
        simulator = plan.simulator
        assert simulator is not None
        if (simulator.runtime_identifier, simulator.runtime_version) not in available:
            fail("runtime_missing")
        device_types = _run(
            runner,
            ("xcrun", "simctl", "list", "devicetypes", "-j"),
            cwd=source_root,
            env=env,
            timeout_seconds=30,
            failure_code="simulator_contract_invalid",
            state_root=state_root,
            stage="simulator-device-types",
        )
        try:
            type_payload = json.loads(device_types.stdout)
        except json.JSONDecodeError as error:
            raise AppleValidationError("simulator_malformed") from error
        type_rows = type_payload.get("devicetypes") if isinstance(type_payload, dict) else None
        if not isinstance(type_rows, list):
            fail("simulator_malformed")
        exact_types = [
            row
            for row in type_rows
            if isinstance(row, dict)
            and row.get("identifier") == simulator.device_type_identifier
            and row.get("name") == simulator.device_type
            and row.get("productFamily") == simulator.device_family
        ]
        if len(exact_types) != 1:
            fail("simulator_contract_invalid")
    return xcode_version, xcode_build, swift_version, sdk_versions, runtime_payload


def _simulator_registry(state_root: Path) -> Path:
    return _state_directory(state_root) / "simulators.json"


def _read_simulator_registry(state_root: Path) -> list[dict[str, str]]:
    path = _simulator_registry(state_root)
    if not path.exists():
        return []
    if path.is_symlink() or not path.is_file():
        fail("cleanup_failed")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise AppleValidationError("cleanup_failed") from error
    if not isinstance(raw, list):
        fail("cleanup_failed")
    rows: list[dict[str, str]] = []
    for row in raw:
        if (
            not isinstance(row, dict)
            or set(row)
            != {
                "udid",
                "platform",
                "runtime_identifier",
                "device_name",
                "device_type_identifier",
            }
            or _UDID.fullmatch(str(row.get("udid", ""))) is None
        ):
            fail("cleanup_failed")
        rows.append({key: str(value) for key, value in row.items()})
    return rows


def _write_simulator_registry(
    state_root: Path,
    rows: Sequence[Mapping[str, str]],
) -> None:
    path = _simulator_registry(state_root)
    if path.is_symlink():
        fail("cleanup_failed")
    path.write_text(
        json.dumps(list(rows), sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )


def _simulator_device_name(plan: AppleValidationPlan, state_root: Path) -> str:
    simulator = plan.simulator
    if simulator is None:
        fail("unsafe_destination")
    state_identity = hashlib.sha256(
        "\0".join(
            (
                plan.request.admitted_sha,
                simulator.runtime_identifier,
                simulator.device_type_identifier,
                str(state_root.resolve()),
            )
        ).encode("utf-8")
    ).hexdigest()[:12]
    return f"{simulator.device_name_prefix} {state_identity}"


def select_simulator(
    plan: AppleValidationPlan,
    source_root: Path,
    state_root: Path,
    runner: CommandRunner,
    env: Mapping[str, str],
) -> SimulatorLease:
    simulator = plan.simulator
    if simulator is None:
        fail("unsafe_destination")
    device_name = _simulator_device_name(plan, state_root)
    outcome = _run(
        runner,
        ("xcrun", "simctl", "list", "devices", "available", "-j"),
        cwd=source_root,
        env=env,
        timeout_seconds=30,
        failure_code="simulator_unavailable",
        state_root=state_root,
        stage="simulator-devices",
    )
    try:
        payload = json.loads(outcome.stdout)
    except json.JSONDecodeError as error:
        raise AppleValidationError("simulator_malformed") from error
    devices = payload.get("devices") if isinstance(payload, dict) else None
    if not isinstance(devices, dict):
        fail("simulator_malformed")
    runtime_rows = devices.get(simulator.runtime_identifier, [])
    if not isinstance(runtime_rows, list):
        fail("simulator_malformed")
    matching: list[dict[str, object]] = []
    for row in runtime_rows:
        if not isinstance(row, dict):
            fail("simulator_malformed")
        if row.get("name") != device_name:
            continue
        udid = row.get("udid")
        state = row.get("state")
        available = row.get("isAvailable", True)
        device_type_identifier = row.get("deviceTypeIdentifier")
        if (
            not isinstance(udid, str)
            or _UDID.fullmatch(udid) is None
            or state not in {"Shutdown", "Booted"}
            or available is not True
            or device_type_identifier != simulator.device_type_identifier
        ):
            fail("simulator_malformed")
        matching.append(row)
    if len(matching) > 1:
        fail("simulator_ambiguous")
    owned = {row["udid"] for row in _read_simulator_registry(state_root)}
    if matching:
        candidate = matching[0]
        udid = str(candidate["udid"])
        if udid not in owned:
            fail("simulator_unowned")
        created = True
    else:
        if not simulator.allow_create:
            fail("simulator_unavailable")
        created_outcome = _run(
            runner,
            (
                "xcrun",
                "simctl",
                "create",
                device_name,
                simulator.device_type_identifier,
                simulator.runtime_identifier,
            ),
            cwd=source_root,
            env=env,
            timeout_seconds=60,
            failure_code="simulator_create_failed",
            state_root=state_root,
            stage="simulator-create",
        )
        udid = created_outcome.stdout.strip()
        if _UDID.fullmatch(udid) is None:
            fail("simulator_malformed")
        rows = _read_simulator_registry(state_root)
        rows.append(
            {
                "udid": udid,
                "platform": simulator.platform,
                "runtime_identifier": simulator.runtime_identifier,
                "device_name": device_name,
                "device_type_identifier": simulator.device_type_identifier,
            }
        )
        _write_simulator_registry(state_root, rows)
        created = True
    if not matching or matching[0].get("state") != "Booted":
        _run(
            runner,
            ("xcrun", "simctl", "boot", udid),
            cwd=source_root,
            env=env,
            timeout_seconds=60,
            failure_code="simulator_boot_failed",
            state_root=state_root,
            stage="simulator-boot",
        )
    _run(
        runner,
        ("xcrun", "simctl", "bootstatus", udid, "-b"),
        cwd=source_root,
        env=env,
        timeout_seconds=180,
        failure_code="simulator_boot_failed",
        state_root=state_root,
        stage="simulator-bootstatus",
    )
    identity = hashlib.sha256(
        "\0".join(
            (
                simulator.platform,
                simulator.runtime_identifier,
                simulator.device_type_identifier,
                udid,
            )
        ).encode("utf-8")
    ).hexdigest()[:20]
    return SimulatorLease(
        udid=udid,
        destination=f"platform={simulator.platform},id={udid}",
        redacted_identity=f"sim-{identity}",
        created=created,
    )


def _git_output(root: Path, arguments: Sequence[str], code: str) -> str:
    try:
        completed = subprocess.run(
            ["git", *arguments],
            cwd=root,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise AppleValidationError(code) from error
    if completed.returncode != 0:
        fail(code)
    return completed.stdout.strip()


def _has_git(source_root: Path) -> bool:
    marker = source_root / ".git"
    return marker.exists() or marker.is_file()


def verify_exact_source(source_root: Path, admitted_sha: str) -> None:
    if not source_root.is_dir() or source_root.is_symlink():
        fail("source_mismatch")
    if not _has_git(source_root):
        return
    if _git_output(source_root, ("rev-parse", "HEAD"), "source_mismatch") != admitted_sha:
        fail("source_mismatch")
    if _git_output(
        source_root,
        ("status", "--porcelain=v1", "--untracked-files=all"),
        "dirty_source",
    ):
        fail("dirty_source")


def _assert_tracked_regular(source_root: Path, relative: str) -> Path:
    path = regular_path(source_root, relative, "script_rejected")
    if not path.is_file():
        fail("script_rejected")
    if _has_git(source_root):
        tracked = _git_output(
            source_root,
            ("ls-files", "--error-unmatch", "--", relative),
            "script_rejected",
        )
        if tracked != relative:
            fail("script_rejected")
    return path


def _tree_hash(path: Path) -> str:
    if not path.exists():
        return hashlib.sha256(b"").hexdigest()
    if path.is_symlink():
        fail("path_rejected")
    if path.is_file():
        return hashlib.sha256(path.read_bytes()).hexdigest()
    if not path.is_dir():
        fail("path_rejected")
    digest = hashlib.sha256()
    for child in sorted(path.rglob("*")):
        if child.is_symlink():
            fail("path_rejected")
        if child.is_file():
            digest.update(child.relative_to(path).as_posix().encode("utf-8"))
            digest.update(b"\0")
            digest.update(hashlib.sha256(child.read_bytes()).digest())
    return digest.hexdigest()


def protected_hashes(source_root: Path, plan: AppleValidationPlan) -> dict[str, str]:
    result: dict[str, str] = {}
    for relative in plan.protected_paths:
        path = bounded_path(source_root, relative, must_exist=True)
        result[relative] = _tree_hash(path)
    if plan.container is not None:
        for relative in plan.container.resolved_files:
            path = regular_path(source_root, relative, "package_resolution_rejected")
            result[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def _validate_container_files(source_root: Path, plan: AppleValidationPlan) -> None:
    container = plan.container
    if container is None:
        return
    path = regular_path(source_root, container.path, "container_invalid")
    if container.kind in {"project", "workspace"} and not path.is_dir():
        fail("container_invalid")
    if container.kind == "package" and not path.is_file():
        fail("container_invalid")
    if container.test_plan:
        test_plan = regular_path(source_root, container.test_plan, "test_plan_rejected")
        if not test_plan.is_file():
            fail("test_plan_rejected")


def _verify_outputs(source_root: Path, command: AppleCommand) -> bool:
    verified = False
    for relative in command.expected_outputs:
        path = bounded_path(source_root, relative, must_exist=True)
        if path.is_symlink() or not (path.is_file() or path.is_dir()):
            fail("output_invalid")
        verified = True
    return verified


def _destination(plan: AppleValidationPlan, lease: SimulatorLease | None) -> str:
    profile = plan.request.validation_profile
    if profile in {AppleProfile.IOS_SIMULATOR, AppleProfile.TVOS_SIMULATOR}:
        if lease is None:
            fail("unsafe_destination")
        return lease.destination
    if profile is AppleProfile.MACOS:
        return "platform=macOS"
    fail("unsafe_destination")


def _xcodebuild_argv(
    plan: AppleValidationPlan,
    command: AppleCommand,
    source_root: Path,
    directories: Mapping[str, Path],
    lease: SimulatorLease | None,
) -> tuple[str, ...]:
    container = plan.container
    if container is None or container.kind not in {"project", "workspace"}:
        fail("container_invalid")
    flag = "-project" if container.kind == "project" else "-workspace"
    argv = [
        "xcodebuild",
        flag,
        str(regular_path(source_root, container.path, "container_invalid")),
        "-scheme",
        container.scheme,
        "-configuration",
        container.configuration,
        "-destination",
        _destination(plan, lease),
        "-derivedDataPath",
        str(directories["derived-data"]),
        "-clonedSourcePackagesDirPath",
        str(directories["swiftpm"]),
        "-resultBundlePath",
        str(directories["result-bundles"] / "validation.xcresult"),
        "-parallel-testing-enabled",
        "NO",
        "CODE_SIGNING_ALLOWED=NO",
        "CODE_SIGNING_REQUIRED=NO",
        "CODE_SIGN_IDENTITY=",
    ]
    if container.test_plan:
        argv.extend(("-testPlan", Path(container.test_plan).stem))
    if container.package_resolution_mode == "locked":
        argv.extend(
            (
                "-disableAutomaticPackageResolution",
                "-onlyUsePackageVersionsFromResolvedFile",
            )
        )
    elif container.package_resolution_mode == "disabled":
        argv.append("-disableAutomaticPackageResolution")
    argv.extend(command.fixed_arguments)
    argv.append(command.action)
    serialized = " ".join(argv).casefold()
    for forbidden in (
        " archive",
        "exportarchive",
        "code_signing_allowed=yes",
        "provisioning_profile",
        "development_team=",
        "notarytool",
        "app-store",
        "testflight",
    ):
        if forbidden in serialized:
            fail("forbidden_operation")
    return tuple(argv)


def _swift_argv(
    plan: AppleValidationPlan,
    command: AppleCommand,
    source_root: Path,
    directories: Mapping[str, Path],
) -> tuple[str, ...]:
    container = plan.container
    if container is None or container.kind != "package":
        fail("container_invalid")
    package_directory = str(regular_path(source_root, container.path, "container_invalid").parent)
    if command.action == "resolve":
        argv = ["swift", "package", "--package-path", package_directory, "resolve"]
    elif command.action == "test":
        argv = [
            "swift",
            "test",
            "--package-path",
            package_directory,
            "--scratch-path",
            str(directories["swiftpm-build"]),
        ]
    elif command.action == "build":
        argv = [
            "swift",
            "build",
            "--package-path",
            package_directory,
            "--scratch-path",
            str(directories["swiftpm-build"]),
        ]
    else:
        fail("command_profile_rejected")
    argv.extend(command.fixed_arguments)
    return tuple(argv)


def _execute_command(
    plan: AppleValidationPlan,
    command: AppleCommand,
    source_root: Path,
    state_root: Path,
    runner: CommandRunner,
    env: Mapping[str, str],
    directories: Mapping[str, Path],
    lease: SimulatorLease | None,
) -> bool:
    working = bounded_path(source_root, plan.working_directory, must_exist=True)
    if not working.is_dir() or working.is_symlink():
        fail("path_rejected")
    if command.kind == "xcodebuild":
        argv = _xcodebuild_argv(plan, command, source_root, directories, lease)
        failure = "xcodebuild_failed"
    elif command.kind == "swift":
        argv = _swift_argv(plan, command, source_root, directories)
        failure = "swift_command_failed"
    else:
        if command.script_path is None:
            fail("script_rejected")
        script = _assert_tracked_regular(source_root, command.script_path)
        interpreter = "python3" if command.kind == "python3-script" else "bash"
        argv = (interpreter, str(script), *command.fixed_arguments)
        failure = (
            "dependency_preparation_failed"
            if command.stage is AppleStage.DEPENDENCY_PREPARATION
            else "repository_recovery_failed"
        )
    _run(
        runner,
        argv,
        cwd=working,
        env=env,
        timeout_seconds=plan.timeout_minutes * 60,
        failure_code=failure,
        state_root=state_root,
        stage=command.stage.value,
    )
    return _verify_outputs(source_root, command)


def execute_apple_plan(
    *,
    plan: AppleValidationPlan,
    source_root: Path,
    state_root: Path,
    runner: CommandRunner | None = None,
    environment: Mapping[str, str] | None = None,
) -> AppleValidationResult:
    command_runner = runner or SubprocessCommandRunner()
    verify_exact_source(source_root, plan.request.admitted_sha)
    _validate_container_files(source_root, plan)
    before = protected_hashes(source_root, plan)
    env, directories = isolated_environment(state_root, environment)
    for key, directory_name in plan.environment_bindings:
        env[key] = str(directories[directory_name])
    completed: list[AppleStage] = []
    lease: SimulatorLease | None = None
    output_verified = False
    cleanup_result = "not-run"
    try:
        (
            xcode_version,
            xcode_build,
            swift_version,
            sdk_versions,
            _,
        ) = verify_toolchain(
            plan,
            source_root,
            state_root,
            command_runner,
            env,
        )
        completed.extend((AppleStage.TOOLCHAIN_VERIFY, AppleStage.SDK_VERIFY))
        if plan.request.validation_profile is AppleProfile.SOURCE_AUDIT:
            completed.append(AppleStage.SOURCE_AUDIT)
        if plan.requires_simulator:
            lease = select_simulator(
                plan,
                source_root,
                state_root,
                command_runner,
                env,
            )
            completed.append(AppleStage.SIMULATOR_SELECT)
        for command in plan.commands:
            output_verified = (
                _execute_command(
                    plan,
                    command,
                    source_root,
                    state_root,
                    command_runner,
                    env,
                    directories,
                    lease,
                )
                or output_verified
            )
            completed.append(command.stage)
        after = protected_hashes(source_root, plan)
        if before != after:
            resolved = set(plan.container.resolved_files) if plan.container else set()
            if any(before.get(path) != after.get(path) for path in resolved):
                fail("package_resolution_mutation")
            fail("source_mutation")
        verify_exact_source(source_root, plan.request.admitted_sha)
        cleanup_apple_state(
            source_root,
            state_root,
            plan,
            runner=command_runner,
            environment=env,
        )
        cleanup_result = "success"
        assert_zero_apple_residue(source_root, state_root, plan)
        verify_exact_source(source_root, plan.request.admitted_sha)
        completed.append(AppleStage.CLEANUP)
        evidence_id = hashlib.sha256(
            json.dumps(
                {
                    "repository": plan.request.repository,
                    "consumer": plan.request.consumer_contract,
                    "profile": plan.request.validation_profile.value,
                    "task": plan.task_profile,
                    "sha": plan.request.admitted_sha,
                    "stages": [stage.value for stage in completed],
                    "xcode": xcode_version,
                    "xcode_build": xcode_build,
                    "swift": swift_version,
                    "sdks": sdk_versions,
                    "simulator": lease.redacted_identity if lease else None,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        return AppleValidationResult(
            plan=plan,
            status="success",
            completed_stages=tuple(completed),
            xcode_version=xcode_version,
            xcode_build=xcode_build,
            swift_version=swift_version,
            sdk_versions=sdk_versions,
            simulator_identity=lease.redacted_identity if lease else None,
            output_verified=output_verified,
            clean_tree=True,
            cleanup_result=cleanup_result,
            artifact_exception_used=False,
            evidence_id=evidence_id,
        )
    except AppleValidationError as primary_error:
        try:
            cleanup_apple_state(
                source_root,
                state_root,
                plan,
                runner=command_runner,
                environment=env,
            )
        except AppleValidationError as cleanup_error:
            raise AppleValidationError(
                primary_error.code,
                cleanup_failed=True,
            ) from cleanup_error
        raise


def _lstat(path: Path) -> os.stat_result | None:
    try:
        return os.lstat(path)
    except FileNotFoundError:
        return None
    except OSError as error:
        raise AppleValidationError("cleanup_failed") from error


def _lexical_target(root: Path, relative: str) -> Path:
    root = Path(os.path.abspath(root))
    metadata = _lstat(root)
    if (
        metadata is None
        or not stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
    ):
        fail("cleanup_failed")
    parts = PurePosixPath(safe_relative(relative, "cleanup_failed")).parts
    target = root.joinpath(*parts)
    current = root
    for part in parts[:-1]:
        current /= part
        current_metadata = _lstat(current)
        if current_metadata is None:
            break
        if (
            not stat.S_ISDIR(current_metadata.st_mode)
            or stat.S_ISLNK(current_metadata.st_mode)
        ):
            fail("cleanup_failed")
    return target


def _remove_no_follow(path: Path) -> None:
    metadata = _lstat(path)
    if metadata is None:
        return
    try:
        if stat.S_ISLNK(metadata.st_mode) or stat.S_ISREG(metadata.st_mode):
            os.unlink(path)
        elif stat.S_ISDIR(metadata.st_mode):
            with os.scandir(path) as entries:
                children = [path / entry.name for entry in entries]
            for child in children:
                _remove_no_follow(child)
            os.rmdir(path)
        else:
            fail("cleanup_failed")
    except AppleValidationError:
        raise
    except OSError as error:
        raise AppleValidationError("cleanup_failed") from error
    if _lstat(path) is not None:
        fail("cleanup_failed")


def _cleanup_targets(
    source_root: Path,
    state_root: Path,
    plan: AppleValidationPlan,
) -> tuple[Path, ...]:
    targets = [_lexical_target(state_root, "apple-validation")]
    targets.extend(_lexical_target(source_root, path) for path in plan.cleanup_paths)
    return tuple(targets)


def cleanup_apple_state(
    source_root: Path,
    state_root: Path,
    plan: AppleValidationPlan,
    *,
    runner: CommandRunner | None = None,
    environment: Mapping[str, str] | None = None,
) -> None:
    command_runner = runner or SubprocessCommandRunner()
    registry_rows = _read_simulator_registry(state_root)
    if registry_rows:
        env = dict(environment or os.environ)
        for row in registry_rows:
            udid = row["udid"]
            _run(
                command_runner,
                ("xcrun", "simctl", "shutdown", udid),
                cwd=source_root,
                env=env,
                timeout_seconds=60,
                failure_code="cleanup_failed",
                check=False,
            )
            _run(
                command_runner,
                ("xcrun", "simctl", "delete", udid),
                cwd=source_root,
                env=env,
                timeout_seconds=60,
                failure_code="cleanup_failed",
            )
        inventory = _run(
            command_runner,
            ("xcrun", "simctl", "list", "devices", "-j"),
            cwd=source_root,
            env=env,
            timeout_seconds=60,
            failure_code="cleanup_failed",
        )
        try:
            payload = json.loads(inventory.stdout)
        except json.JSONDecodeError as error:
            raise AppleValidationError("cleanup_failed") from error
        devices = payload.get("devices") if isinstance(payload, dict) else None
        if not isinstance(devices, dict):
            fail("cleanup_failed")
        remaining_udids = {
            str(device.get("udid"))
            for rows in devices.values()
            if isinstance(rows, list)
            for device in rows
            if isinstance(device, dict)
        }
        if any(row["udid"] in remaining_udids for row in registry_rows):
            fail("cleanup_failed")
    for path in _cleanup_targets(source_root, state_root, plan):
        _remove_no_follow(path)


def assert_zero_apple_residue(
    source_root: Path,
    state_root: Path,
    plan: AppleValidationPlan,
) -> None:
    remaining = [
        str(path)
        for path in _cleanup_targets(source_root, state_root, plan)
        if _lstat(path) is not None
    ]
    if remaining:
        fail("cleanup_failed")
