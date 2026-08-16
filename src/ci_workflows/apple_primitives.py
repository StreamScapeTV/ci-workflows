"""Product-neutral Apple build, test, simulator, packaging, and cleanup primitives."""
from __future__ import annotations

import os
import re
import stat
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Mapping, Protocol, Sequence

_VERSION = re.compile(r"^[0-9]+(?:\.[0-9]+){1,2}$")
_XCODE_VERSION = re.compile(r"^Xcode\s+([0-9]+(?:\.[0-9]+){1,2})\s*$", re.MULTILINE)
_XCODE_BUILD = re.compile(r"^Build version\s+([0-9A-Z]+)\s*$", re.MULTILINE)
_SWIFT_VERSION = re.compile(r"Apple Swift version\s+([0-9]+(?:\.[0-9]+){1,2})")
_UDID = re.compile(r"^[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}$")
_SAFE_TEXT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_. +()=,:/@-]{0,511}$")
_CONTAINER_SUFFIX = {"project": ".xcodeproj", "workspace": ".xcworkspace"}
_PLATFORM_SDKS = {
    "ios": {"iphoneos", "iphonesimulator"},
    "tvos": {"appletvos", "appletvsimulator"},
    "macos": {"macosx"},
}
_SIMULATOR_DESTINATION = {"ios": "iOS Simulator", "tvos": "tvOS Simulator"}
_SIGNING_KEYS = {
    "CODE_SIGN_IDENTITY",
    "EXPANDED_CODE_SIGN_IDENTITY",
    "PROVISIONING_PROFILE",
    "PROVISIONING_PROFILE_SPECIFIER",
    "DEVELOPMENT_TEAM",
    "OTHER_CODE_SIGN_FLAGS",
    "KEYCHAIN_PATH",
    "KEYCHAIN_PASSWORD",
    "CI_KEYCHAIN_PATH",
    "CI_KEYCHAIN_PASSWORD",
}


class ApplePrimitiveError(RuntimeError):
    """Stable failure raised by the standalone primitive layer."""

    def __init__(self, code: str) -> None:
        if re.fullmatch(r"[a-z][a-z0-9_]{2,95}", code) is None:
            raise ValueError("invalid Apple primitive error code")
        self.code = code
        super().__init__(code)


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise ApplePrimitiveError(code)


class ApplePlatform(str, Enum):
    IOS = "ios"
    TVOS = "tvos"
    MACOS = "macos"


class XcodeAction(str, Enum):
    BUILD = "build"
    TEST = "test"
    ARCHIVE = "archive"


@dataclass(frozen=True, slots=True)
class CommandOutcome:
    returncode: int
    stdout: str = ""
    stderr: str = ""


class CommandRunner(Protocol):
    def run(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        env: Mapping[str, str],
        timeout_seconds: int,
    ) -> CommandOutcome: ...


@dataclass(frozen=True, slots=True)
class CommandSpec:
    argv: tuple[str, ...]
    cwd: Path
    timeout_seconds: int
    signing_authorized: bool = False


@dataclass(frozen=True, slots=True)
class ToolchainIdentity:
    xcode_version: str
    xcode_build: str
    swift_version: str
    sdk_versions: tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class XcodeBuildRequest:
    platform: ApplePlatform
    action: XcodeAction
    container_kind: str
    container_path: str
    scheme: str
    configuration: str = "Debug"
    destination: str | None = None
    sdk: str | None = None
    derived_data_path: str = "DerivedData"
    result_bundle_path: str | None = None
    archive_path: str | None = None
    test_plan: str | None = None
    only_testing: tuple[str, ...] = ()
    skip_testing: tuple[str, ...] = ()
    signing_authorized: bool = False


@dataclass(frozen=True, slots=True)
class SimulatorRequest:
    platform: ApplePlatform
    name: str
    runtime_identifier: str
    device_type_identifier: str


@dataclass(frozen=True, slots=True)
class SimulatorLease:
    platform: ApplePlatform
    udid: str
    destination: str


def _safe_text(value: str, code: str) -> str:
    _require(
        isinstance(value, str)
        and "\n" not in value
        and "\r" not in value
        and "\x00" not in value
        and _SAFE_TEXT.fullmatch(value) is not None,
        code,
    )
    return value


def _relative_source_path(
    root: Path,
    relative: str,
    *,
    suffix: str | None = None,
    must_exist: bool = True,
) -> Path:
    _require(isinstance(relative, str) and relative and not relative.startswith("/"), "path_rejected")
    _require("\\" not in relative and "\x00" not in relative, "path_rejected")
    parts = Path(relative).parts
    _require(".." not in parts, "path_rejected")
    root_resolved = root.resolve()
    _require(root_resolved.is_dir() and not root.is_symlink(), "path_rejected")
    target = root_resolved.joinpath(*parts)
    resolved = target.resolve(strict=False)
    _require(resolved != root_resolved and root_resolved in resolved.parents, "path_rejected")
    current = root_resolved
    for part in parts:
        current /= part
        if current.is_symlink():
            raise ApplePrimitiveError("path_rejected")
        if not current.exists():
            break
    if suffix is not None:
        _require(str(target).endswith(suffix), "path_rejected")
    if must_exist:
        _require(target.exists() and not target.is_symlink(), "path_rejected")
    return target


def _state_path(root: Path, relative: str) -> Path:
    _require(isinstance(relative, str) and relative and not relative.startswith("/"), "path_rejected")
    _require("\\" not in relative and "\x00" not in relative, "path_rejected")
    parts = Path(relative).parts
    _require(".." not in parts, "path_rejected")
    boundary = root.resolve()
    _require(boundary.is_dir() and not root.is_symlink(), "path_rejected")
    target = boundary.joinpath(*parts)
    current = boundary
    for part in parts:
        current /= part
        if current.is_symlink():
            raise ApplePrimitiveError("path_rejected")
        if not current.exists():
            break
    resolved = target.resolve(strict=False)
    _require(resolved != boundary and boundary in resolved.parents, "path_rejected")
    return target


def _parse_xcode(output: str) -> tuple[str, str]:
    version = _XCODE_VERSION.search(output)
    build = _XCODE_BUILD.search(output)
    _require(version is not None and build is not None, "toolchain_identity_invalid")
    return version.group(1), build.group(1)


def _parse_swift(output: str) -> str:
    match = _SWIFT_VERSION.search(output)
    _require(match is not None, "toolchain_identity_invalid")
    return match.group(1)


def run_command(
    spec: CommandSpec,
    runner: CommandRunner,
    *,
    environment: Mapping[str, str],
    check: bool = True,
) -> CommandOutcome:
    _require(bool(spec.argv) and all(isinstance(item, str) and item for item in spec.argv), "invalid_input")
    if not spec.signing_authorized:
        _require(not (_SIGNING_KEYS & set(environment)), "signing_not_authorized")
    outcome = runner.run(
        spec.argv,
        cwd=spec.cwd,
        env=dict(environment),
        timeout_seconds=spec.timeout_seconds,
    )
    if check and outcome.returncode != 0:
        raise ApplePrimitiveError("command_failed")
    return outcome


def inspect_toolchain(
    runner: CommandRunner,
    *,
    cwd: Path,
    environment: Mapping[str, str],
    sdks: Sequence[str] = ("iphoneos", "iphonesimulator", "appletvos", "appletvsimulator", "macosx"),
) -> ToolchainIdentity:
    _require(cwd.is_dir() and not cwd.is_symlink(), "path_rejected")
    xcode = run_command(CommandSpec(("xcodebuild", "-version"), cwd, 30), runner, environment=environment)
    xcode_version, xcode_build = _parse_xcode(xcode.stdout + xcode.stderr)
    swift = run_command(CommandSpec(("swift", "--version"), cwd, 30), runner, environment=environment)
    swift_version = _parse_swift(swift.stdout + swift.stderr)
    sdk_versions: list[tuple[str, str]] = []
    seen: set[str] = set()
    allowed = {"iphoneos", "iphonesimulator", "appletvos", "appletvsimulator", "macosx"}
    for sdk in sdks:
        _require(sdk in allowed and sdk not in seen, "sdk_rejected")
        seen.add(sdk)
        outcome = run_command(
            CommandSpec(("xcrun", "--sdk", sdk, "--show-sdk-version"), cwd, 30),
            runner,
            environment=environment,
        )
        version = outcome.stdout.strip()
        _require(_VERSION.fullmatch(version) is not None, "sdk_identity_invalid")
        sdk_versions.append((sdk, version))
    return ToolchainIdentity(xcode_version, xcode_build, swift_version, tuple(sdk_versions))


def select_xcode(
    candidates: Sequence[Path],
    *,
    expected_version: str,
    runner: CommandRunner,
    cwd: Path,
    environment: Mapping[str, str],
    expected_build: str | None = None,
) -> Path:
    _require(_VERSION.fullmatch(expected_version) is not None, "toolchain_identity_invalid")
    _require(bool(candidates), "xcode_not_found")
    for candidate in candidates:
        path = candidate.resolve()
        if not path.is_dir() or candidate.is_symlink():
            continue
        env = dict(environment)
        env["DEVELOPER_DIR"] = str(path)
        outcome = runner.run(("xcodebuild", "-version"), cwd=cwd, env=env, timeout_seconds=30)
        if outcome.returncode != 0:
            continue
        try:
            version, build = _parse_xcode(outcome.stdout + outcome.stderr)
        except ApplePrimitiveError:
            continue
        if version == expected_version and (expected_build is None or build == expected_build):
            return path
    raise ApplePrimitiveError("xcode_not_found")


def plan_xcodebuild(request: XcodeBuildRequest, *, source_root: Path, state_root: Path) -> CommandSpec:
    _require(request.container_kind in _CONTAINER_SUFFIX, "container_rejected")
    container = _relative_source_path(source_root, request.container_path, suffix=_CONTAINER_SUFFIX[request.container_kind])
    scheme = _safe_text(request.scheme, "scheme_rejected")
    configuration = _safe_text(request.configuration, "configuration_rejected")
    if request.sdk is not None:
        _require(request.sdk in _PLATFORM_SDKS[request.platform.value], "sdk_rejected")
    destination = None
    if request.destination is not None:
        destination = _safe_text(request.destination, "destination_rejected")
    if request.platform is ApplePlatform.MACOS:
        _require(destination is None or "macOS" in destination, "destination_rejected")
    elif request.action is XcodeAction.TEST:
        _require(destination is not None, "destination_rejected")

    derived_data = _state_path(state_root, request.derived_data_path)
    result_bundle = _state_path(state_root, request.result_bundle_path) if request.result_bundle_path is not None else None
    archive_path = _state_path(state_root, request.archive_path) if request.archive_path is not None else None
    if request.action is XcodeAction.ARCHIVE:
        _require(archive_path is not None, "archive_path_required")
    elif archive_path is not None:
        raise ApplePrimitiveError("archive_path_rejected")

    argv: list[str] = ["xcodebuild"]
    argv += ["-project" if request.container_kind == "project" else "-workspace", str(container)]
    argv += ["-scheme", scheme, "-configuration", configuration]
    if request.sdk is not None:
        argv += ["-sdk", request.sdk]
    if destination is not None:
        argv += ["-destination", destination]
    argv += ["-derivedDataPath", str(derived_data)]
    if result_bundle is not None:
        argv += ["-resultBundlePath", str(result_bundle)]
    if request.test_plan is not None:
        _require(request.action is XcodeAction.TEST, "test_plan_rejected")
        argv += ["-testPlan", _safe_text(request.test_plan, "test_plan_rejected")]
    for value in request.only_testing:
        argv += ["-only-testing:" + _safe_text(value, "test_filter_rejected")]
    for value in request.skip_testing:
        argv += ["-skip-testing:" + _safe_text(value, "test_filter_rejected")]
    if request.action is XcodeAction.ARCHIVE:
        assert archive_path is not None
        argv += ["-archivePath", str(archive_path), "archive"]
    else:
        argv.append(request.action.value)
    if not request.signing_authorized:
        argv += ["CODE_SIGNING_ALLOWED=NO", "CODE_SIGNING_REQUIRED=NO"]
    return CommandSpec(tuple(argv), source_root.resolve(), 7200, request.signing_authorized)


def plan_unsigned_package(source_path: str, output_path: str, *, source_root: Path, state_root: Path) -> CommandSpec:
    source = _relative_source_path(source_root, source_path)
    output = _state_path(state_root, output_path)
    return CommandSpec(
        ("ditto", "-c", "-k", "--sequesterRsrc", "--keepParent", str(source), str(output)),
        source_root.resolve(),
        900,
        False,
    )


def plan_export_archive(
    *,
    archive_path: str,
    export_path: str,
    export_options_plist: str,
    source_root: Path,
    state_root: Path,
    signing_authorized: bool,
) -> CommandSpec:
    _require(signing_authorized, "signing_not_authorized")
    archive = _state_path(state_root, archive_path)
    export = _state_path(state_root, export_path)
    options = _relative_source_path(source_root, export_options_plist, suffix=".plist")
    return CommandSpec(
        ("xcodebuild", "-exportArchive", "-archivePath", str(archive), "-exportPath", str(export), "-exportOptionsPlist", str(options)),
        source_root.resolve(),
        3600,
        True,
    )


def create_boot_simulator(
    request: SimulatorRequest,
    runner: CommandRunner,
    *,
    cwd: Path,
    environment: Mapping[str, str],
) -> SimulatorLease:
    _require(request.platform in {ApplePlatform.IOS, ApplePlatform.TVOS}, "simulator_platform_rejected")
    name = _safe_text(request.name, "simulator_request_rejected")
    runtime = _safe_text(request.runtime_identifier, "simulator_request_rejected")
    device_type = _safe_text(request.device_type_identifier, "simulator_request_rejected")
    create = run_command(CommandSpec(("xcrun", "simctl", "create", name, device_type, runtime), cwd, 60), runner, environment=environment)
    udid = create.stdout.strip()
    _require(_UDID.fullmatch(udid) is not None, "simulator_identity_invalid")
    run_command(CommandSpec(("xcrun", "simctl", "boot", udid), cwd, 60), runner, environment=environment)
    run_command(CommandSpec(("xcrun", "simctl", "bootstatus", udid, "-b"), cwd, 300), runner, environment=environment)
    return SimulatorLease(request.platform, udid, f"platform={_SIMULATOR_DESTINATION[request.platform.value]},id={udid}")


def cleanup_simulator(
    lease: SimulatorLease,
    runner: CommandRunner,
    *,
    cwd: Path,
    environment: Mapping[str, str],
) -> None:
    _require(_UDID.fullmatch(lease.udid) is not None, "simulator_identity_invalid")
    run_command(CommandSpec(("xcrun", "simctl", "shutdown", lease.udid), cwd, 60), runner, environment=environment, check=False)
    run_command(CommandSpec(("xcrun", "simctl", "delete", lease.udid), cwd, 60), runner, environment=environment)


def _remove_no_follow(path: Path) -> None:
    try:
        metadata = os.lstat(path)
    except FileNotFoundError:
        return
    except OSError as error:
        raise ApplePrimitiveError("cleanup_failed") from error
    try:
        if stat.S_ISLNK(metadata.st_mode) or stat.S_ISREG(metadata.st_mode):
            os.unlink(path)
            return
        if stat.S_ISDIR(metadata.st_mode):
            with os.scandir(path) as entries:
                children = [path / entry.name for entry in entries]
            for child in children:
                _remove_no_follow(child)
            os.rmdir(path)
            return
    except OSError as error:
        raise ApplePrimitiveError("cleanup_failed") from error
    raise ApplePrimitiveError("cleanup_failed")


def cleanup_output_paths(state_root: Path, relative_paths: Sequence[str]) -> None:
    boundary = state_root.resolve()
    _require(boundary.is_dir() and not state_root.is_symlink(), "cleanup_failed")
    seen: set[Path] = set()
    for relative in relative_paths:
        target = _state_path(state_root, relative)
        resolved_parent = target.parent.resolve(strict=False)
        _require(resolved_parent == boundary or boundary in resolved_parent.parents, "cleanup_failed")
        _require(target not in seen, "cleanup_failed")
        seen.add(target)
        _remove_no_follow(target)
        _require(not os.path.lexists(target), "cleanup_failed")
