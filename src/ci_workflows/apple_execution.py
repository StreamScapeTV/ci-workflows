"""Hermetic Apple toolchain, simulator, build, and cleanup execution."""
from __future__ import annotations

import fcntl
import hashlib
import json
import os
import pwd
import re
import stat
import subprocess
import time
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable, Iterator, Mapping, Protocol, Sequence

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
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_SECRET = re.compile(
    r"(?i)(token|password|authorization|secret|keychain|provisioning)\s*[:=]\s*\S+"
)
_OWNERSHIP_DIRECTORY = ".ciw-apple-simulator-ownership-v1"
_OWNERSHIP_LOCK = "registry.lock"
_OWNERSHIP_REGISTRY = "registry.json"
_OWNERSHIP_SCHEMA = 1
_OWNERSHIP_MAX_ROWS = 8
_OWNERSHIP_ROW_KEYS = {
    "owner_key",
    "status",
    "device_name",
    "udid",
    "platform",
    "runtime_identifier",
    "runtime_version",
    "device_type_identifier",
    "device_family",
}
_SIMULATOR_DELETE_BACKOFF_SECONDS = (0.05, 0.15)
_SIMULATOR_EXTERNAL_DISPLAY_SUFFIX = " – External Display"


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
        except subprocess.TimeoutExpired:
            raise
        except OSError as error:
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


@dataclass(slots=True)
class SimulatorOwnership:
    """One host-user simulator lock and its validated ownership registry."""

    root: Path
    lock_fd: int
    rows: list[dict[str, str]]

    def row(self, owner_key: str) -> dict[str, str] | None:
        matches = [row for row in self.rows if row["owner_key"] == owner_key]
        if len(matches) > 1:
            fail("simulator_ambiguous")
        return matches[0] if matches else None

    def replace(self, owner_key: str, row: Mapping[str, str] | None) -> None:
        self.rows = [item for item in self.rows if item["owner_key"] != owner_key]
        if row is not None:
            self.rows.append(dict(row))
        _write_ownership_registry(self.root, self.rows)


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
    metadata = _lstat(path)
    if metadata is not None and (
        stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode)
    ):
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
        metadata = _lstat(path)
        if metadata is not None and (
            stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode)
        ):
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


def _lstat(path: Path) -> os.stat_result | None:
    try:
        return os.lstat(path)
    except FileNotFoundError:
        return None
    except OSError as error:
        raise AppleValidationError("cleanup_failed") from error


def _verify_absolute_directory_no_follow(path: Path, code: str) -> Path:
    absolute = Path(os.path.abspath(path))
    if not absolute.is_absolute():
        fail(code)
    current = Path(absolute.anchor)
    for part in absolute.parts[1:]:
        current /= part
        metadata = _lstat(current)
        if metadata is None:
            fail(code)
        if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
            fail(code)
    return absolute


def _ownership_base(
    environment: Mapping[str, str],
    state_root: Path | None = None,
) -> Path:
    raw_workspace = environment.get("RUNNER_WORKSPACE", "").strip()
    github_actions = environment.get("GITHUB_ACTIONS") == "true"
    if github_actions:
        if not raw_workspace:
            fail("simulator_ownership_invalid")
        _verify_absolute_directory_no_follow(
            Path(raw_workspace),
            "simulator_ownership_invalid",
        )
        try:
            account_home = Path(pwd.getpwuid(os.geteuid()).pw_dir)
        except (KeyError, OSError) as error:
            raise AppleValidationError("simulator_ownership_invalid") from error
        return _verify_absolute_directory_no_follow(
            account_home,
            "simulator_ownership_invalid",
        )
    if raw_workspace:
        return _verify_absolute_directory_no_follow(
            Path(raw_workspace),
            "simulator_ownership_invalid",
        )
    if environment.get("CI") == "true":
        fail("simulator_ownership_invalid")
    if state_root is None or not state_root.is_absolute():
        fail("simulator_ownership_invalid")
    return _verify_absolute_directory_no_follow(
        state_root.parent,
        "simulator_ownership_invalid",
    )


def _ownership_root(
    environment: Mapping[str, str],
    state_root: Path | None = None,
) -> Path:
    base = _ownership_base(environment, state_root)
    root = base / _OWNERSHIP_DIRECTORY
    metadata = _lstat(root)
    if metadata is None:
        try:
            os.mkdir(root, 0o700)
        except OSError as error:
            raise AppleValidationError("simulator_ownership_invalid") from error
        metadata = _lstat(root)
    if (
        metadata is None
        or stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISDIR(metadata.st_mode)
        or stat.S_IMODE(metadata.st_mode) & 0o077
    ):
        fail("simulator_ownership_invalid")
    return root


def _read_regular_no_follow(path: Path, code: str, maximum_bytes: int) -> bytes:
    metadata = _lstat(path)
    if metadata is None:
        raise FileNotFoundError(path)
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        fail(code)
    if metadata.st_size > maximum_bytes:
        fail(code)
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
        with os.fdopen(fd, "rb", closefd=True) as handle:
            return handle.read(maximum_bytes + 1)
    except OSError as error:
        raise AppleValidationError(code) from error


def _validate_ownership_row(raw: object) -> dict[str, str]:
    if not isinstance(raw, dict) or set(raw) != _OWNERSHIP_ROW_KEYS:
        fail("simulator_ownership_corrupt")
    row = {key: value for key, value in raw.items()}
    if not all(isinstance(value, str) for value in row.values()):
        fail("simulator_ownership_corrupt")
    if _HEX64.fullmatch(row["owner_key"]) is None:
        fail("simulator_ownership_corrupt")
    if row["status"] not in {"pending-create", "owned"}:
        fail("simulator_ownership_corrupt")
    if row["status"] == "pending-create" and row["udid"]:
        fail("simulator_ownership_corrupt")
    if row["status"] == "owned" and _UDID.fullmatch(row["udid"]) is None:
        fail("simulator_ownership_corrupt")
    for key in (
        "device_name",
        "platform",
        "runtime_identifier",
        "runtime_version",
        "device_type_identifier",
        "device_family",
    ):
        if not row[key] or "\x00" in row[key] or "\n" in row[key] or "\r" in row[key]:
            fail("simulator_ownership_corrupt")
    owner_suffix = f" {row['owner_key'][:16]}"
    if not row["device_name"].startswith("CIW ") or not row["device_name"].endswith(owner_suffix):
        fail("simulator_ownership_corrupt")
    return row


def _read_ownership_registry(root: Path) -> list[dict[str, str]]:
    path = root / _OWNERSHIP_REGISTRY
    try:
        raw = _read_regular_no_follow(path, "simulator_ownership_corrupt", 65536)
    except FileNotFoundError:
        return []
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise AppleValidationError("simulator_ownership_corrupt") from error
    if (
        not isinstance(payload, dict)
        or set(payload) != {"schema_version", "owners"}
        or payload.get("schema_version") != _OWNERSHIP_SCHEMA
        or not isinstance(payload.get("owners"), list)
        or len(payload["owners"]) > _OWNERSHIP_MAX_ROWS
    ):
        fail("simulator_ownership_corrupt")
    rows = [_validate_ownership_row(row) for row in payload["owners"]]
    keys = [row["owner_key"] for row in rows]
    names = [row["device_name"] for row in rows]
    udids = [row["udid"] for row in rows if row["udid"]]
    if (
        len(keys) != len(set(keys))
        or len(names) != len(set(names))
        or len(udids) != len(set(udids))
    ):
        fail("simulator_ownership_corrupt")
    return rows


def _write_ownership_registry(root: Path, rows: Sequence[Mapping[str, str]]) -> None:
    validated = [_validate_ownership_row(dict(row)) for row in rows]
    keys = [row["owner_key"] for row in validated]
    names = [row["device_name"] for row in validated]
    udids = [row["udid"] for row in validated if row["udid"]]
    if (
        len(keys) != len(set(keys))
        or len(names) != len(set(names))
        or len(udids) != len(set(udids))
        or len(validated) > _OWNERSHIP_MAX_ROWS
    ):
        fail("simulator_ownership_corrupt")
    path = root / _OWNERSHIP_REGISTRY
    metadata = _lstat(path)
    if metadata is not None and (
        stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode)
    ):
        fail("simulator_ownership_corrupt")
    payload = json.dumps(
        {
            "schema_version": _OWNERSHIP_SCHEMA,
            "owners": sorted(validated, key=lambda row: row["owner_key"]),
        },
        sort_keys=True,
        indent=2,
    ).encode("utf-8") + b"\n"
    temporary = root / f".{_OWNERSHIP_REGISTRY}.{os.getpid()}.tmp"
    if _lstat(temporary) is not None:
        fail("simulator_ownership_corrupt")
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        fd = os.open(temporary, flags, 0o600)
        with os.fdopen(fd, "wb", closefd=True) as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory_fd = os.open(
            root,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0),
        )
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except OSError as error:
        try:
            if _lstat(temporary) is not None:
                os.unlink(temporary)
        except OSError:
            pass
        raise AppleValidationError("simulator_ownership_corrupt") from error


@contextmanager
def _simulator_ownership(
    environment: Mapping[str, str],
    state_root: Path | None = None,
) -> Iterator[SimulatorOwnership]:
    root = _ownership_root(environment, state_root)
    lock_path = root / _OWNERSHIP_LOCK
    metadata = _lstat(lock_path)
    if metadata is not None and (
        stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode)
    ):
        fail("simulator_ownership_invalid")
    flags = (
        os.O_RDWR
        | os.O_CREAT
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        lock_fd = os.open(lock_path, flags, 0o600)
    except OSError as error:
        raise AppleValidationError("simulator_ownership_invalid") from error
    try:
        metadata = os.fstat(lock_fd)
        if not stat.S_ISREG(metadata.st_mode):
            fail("simulator_ownership_invalid")
        operation = fcntl.LOCK_EX
        if environment.get("GITHUB_ACTIONS") != "true":
            operation |= fcntl.LOCK_NB
        try:
            fcntl.flock(lock_fd, operation)
        except BlockingIOError as error:
            raise AppleValidationError("simulator_ownership_locked") from error
        ownership = SimulatorOwnership(
            root=root,
            lock_fd=lock_fd,
            rows=_read_ownership_registry(root),
        )
        try:
            yield ownership
        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
    finally:
        os.close(lock_fd)


def _simulator_owner_key(plan: AppleValidationPlan) -> str:
    simulator = plan.simulator
    if simulator is None:
        fail("unsafe_destination")
    material = "\0".join(
        (
            simulator.device_name_prefix,
            simulator.platform,
            simulator.runtime_identifier,
            simulator.runtime_version,
            simulator.device_type_identifier,
            simulator.device_family,
        )
    ).encode("utf-8")
    return hashlib.sha256(material).hexdigest()


def _simulator_device_name(
    plan: AppleValidationPlan,
    state_root: Path | None = None,
) -> str:
    """Return the contract-owned name; ``state_root`` is ignored for compatibility."""
    simulator = plan.simulator
    if simulator is None:
        fail("unsafe_destination")
    suffix = _simulator_owner_key(plan)[:16]
    return f"{simulator.device_name_prefix} {suffix}"


def _ownership_record(
    plan: AppleValidationPlan,
    *,
    status: str,
    udid: str = "",
) -> dict[str, str]:
    simulator = plan.simulator
    if simulator is None:
        fail("unsafe_destination")
    return {
        "owner_key": _simulator_owner_key(plan),
        "status": status,
        "device_name": _simulator_device_name(plan),
        "udid": udid,
        "platform": simulator.platform,
        "runtime_identifier": simulator.runtime_identifier,
        "runtime_version": simulator.runtime_version,
        "device_type_identifier": simulator.device_type_identifier,
        "device_family": simulator.device_family,
    }


def _require_record_identity(plan: AppleValidationPlan, row: Mapping[str, str]) -> None:
    expected = _ownership_record(
        plan,
        status=row.get("status", ""),
        udid=row.get("udid", ""),
    )
    for key in (
        "owner_key",
        "device_name",
        "platform",
        "runtime_identifier",
        "runtime_version",
        "device_type_identifier",
        "device_family",
    ):
        if row.get(key) != expected[key]:
            fail("simulator_ownership_identity_mismatch")


def _external_display_name(row: Mapping[str, str]) -> str:
    return f"{row['device_name']}{_SIMULATOR_EXTERNAL_DISPLAY_SUFFIX}"


def _recorded_owned_companion_candidates(
    row: Mapping[str, str],
    payload: object,
    *,
    require_available: bool = False,
) -> list[dict[str, str]]:
    """Find only the exact External Display companion derived from one CIW row."""

    devices = payload.get("devices") if isinstance(payload, dict) else None
    if not isinstance(devices, dict):
        fail("simulator_malformed")
    rows = devices.get(row["runtime_identifier"], [])
    if not isinstance(rows, list):
        fail("simulator_malformed")
    expected_name = _external_display_name(row)
    candidates: list[dict[str, str]] = []
    for raw in rows:
        if not isinstance(raw, dict) or raw.get("name") != expected_name:
            continue
        udid = raw.get("udid")
        state = raw.get("state")
        available = raw.get("isAvailable", True)
        device_type_identifier = raw.get("deviceTypeIdentifier")
        availability_invalid = (
            available is not True
            if require_available
            else not isinstance(available, bool)
        )
        if (
            not isinstance(udid, str)
            or _UDID.fullmatch(udid) is None
            or (row.get("udid") and udid == row.get("udid"))
            or state not in {"Shutdown", "Booted"}
            or availability_invalid
            or not isinstance(device_type_identifier, str)
            or not device_type_identifier.startswith(
                "com.apple.CoreSimulator.SimDeviceType."
            )
            or len(device_type_identifier) > 512
            or any(character in device_type_identifier for character in ("\x00", "\n", "\r"))
        ):
            fail("simulator_malformed")
        candidates.append(
            {
                "name": expected_name,
                "udid": udid,
                "state": str(state),
                "runtime_identifier": row["runtime_identifier"],
                "device_type_identifier": device_type_identifier,
            }
        )
    if len(candidates) > 1:
        fail("simulator_ambiguous")
    return candidates


def _exact_owned_candidates(
    plan: AppleValidationPlan,
    payload: object,
    *,
    require_available: bool = True,
) -> list[dict[str, str]]:
    simulator = plan.simulator
    if simulator is None:
        fail("unsafe_destination")
    devices = payload.get("devices") if isinstance(payload, dict) else None
    if not isinstance(devices, dict):
        fail("simulator_malformed")
    expected_name = _simulator_device_name(plan)
    prefix = f"{simulator.device_name_prefix} "
    candidates: list[dict[str, str]] = []
    rows = devices.get(simulator.runtime_identifier, [])
    if not isinstance(rows, list):
        fail("simulator_malformed")
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        name = raw.get("name")
        if not isinstance(name, str) or not name.startswith(prefix):
            continue
        if name != expected_name:
            continue
        udid = raw.get("udid")
        state = raw.get("state")
        available = raw.get("isAvailable", True)
        device_type_identifier = raw.get("deviceTypeIdentifier")
        if device_type_identifier != simulator.device_type_identifier:
            fail("simulator_ownership_identity_mismatch")
        availability_invalid = (
            available is not True
            if require_available
            else not isinstance(available, bool)
        )
        if (
            not isinstance(udid, str)
            or _UDID.fullmatch(udid) is None
            or state not in {"Shutdown", "Booted"}
            or availability_invalid
        ):
            fail("simulator_malformed")
        candidates.append(
            {
                "name": name,
                "udid": udid,
                "state": str(state),
                "runtime_identifier": simulator.runtime_identifier,
                "device_type_identifier": str(device_type_identifier),
            }
        )
    if len(candidates) > 1:
        fail("simulator_ambiguous")
    if candidates:
        return candidates
    return _recorded_owned_companion_candidates(
        _ownership_record(plan, status="pending-create"),
        payload,
        require_available=require_available,
    )


def _recorded_owned_candidates(
    row: Mapping[str, str],
    payload: object,
) -> list[dict[str, str]]:
    """Find only devices described by one validated CIW ownership row."""

    devices = payload.get("devices") if isinstance(payload, dict) else None
    if not isinstance(devices, dict):
        fail("simulator_malformed")
    rows = devices.get(row["runtime_identifier"], [])
    if not isinstance(rows, list):
        fail("simulator_malformed")
    candidates: list[dict[str, str]] = []
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        name = raw.get("name")
        udid = raw.get("udid")
        if row["status"] == "owned":
            name_matches = name == row["device_name"]
            udid_matches = udid == row["udid"]
            if not name_matches and not udid_matches:
                continue
            if not name_matches or not udid_matches:
                fail("simulator_ownership_identity_mismatch")
        elif name != row["device_name"]:
            continue
        state = raw.get("state")
        available = raw.get("isAvailable", True)
        device_type_identifier = raw.get("deviceTypeIdentifier")
        if device_type_identifier != row["device_type_identifier"]:
            fail("simulator_ownership_identity_mismatch")
        if (
            not isinstance(udid, str)
            or _UDID.fullmatch(udid) is None
            or state not in {"Shutdown", "Booted"}
            or not isinstance(available, bool)
        ):
            fail("simulator_malformed")
        candidates.append(
            {
                "name": str(name),
                "udid": udid,
                "state": str(state),
                "runtime_identifier": row["runtime_identifier"],
                "device_type_identifier": str(device_type_identifier),
            }
        )
    if len(candidates) > 1:
        fail("simulator_ambiguous")
    return candidates


def _device_inventory(
    runner: CommandRunner,
    *,
    source_root: Path,
    state_root: Path,
    env: Mapping[str, str],
    available_only: bool,
    failure_code: str,
    record_log: bool = True,
) -> object:
    argv = (
        ("xcrun", "simctl", "list", "devices", "available", "-j")
        if available_only
        else ("xcrun", "simctl", "list", "devices", "-j")
    )
    outcome = _run(
        runner,
        argv,
        cwd=source_root,
        env=env,
        timeout_seconds=60,
        failure_code=failure_code,
        state_root=state_root if record_log else None,
        stage="simulator-devices" if available_only else "simulator-cleanup-inventory",
    )
    try:
        return json.loads(outcome.stdout)
    except json.JSONDecodeError as error:
        raise AppleValidationError(
            "simulator_malformed" if available_only else "cleanup_failed"
        ) from error


def _delete_owned_simulator_with_retry(
    runner: CommandRunner,
    *,
    source_root: Path,
    state_root: Path,
    env: Mapping[str, str],
    udid: str,
    failure_code: str,
    stage_prefix: str,
    candidates_for: Callable[[object], list[dict[str, str]]],
) -> None:
    """Delete one proven simulator with bounded read-back retry/backoff."""

    attempts = len(_SIMULATOR_DELETE_BACKOFF_SECONDS) + 1
    for attempt in range(attempts):
        payload = _device_inventory(
            runner,
            source_root=source_root,
            state_root=state_root,
            env=env,
            available_only=False,
            failure_code=failure_code,
        )
        candidates = candidates_for(payload)
        if not candidates:
            return
        if candidates[0]["udid"] != udid:
            fail(failure_code)
        _run(
            runner,
            ("xcrun", "simctl", "shutdown", udid),
            cwd=source_root,
            env=env,
            timeout_seconds=60,
            failure_code=failure_code,
            state_root=state_root,
            stage=f"{stage_prefix}-shutdown",
            check=False,
        )
        _run(
            runner,
            ("xcrun", "simctl", "delete", udid),
            cwd=source_root,
            env=env,
            timeout_seconds=60,
            failure_code=failure_code,
            state_root=state_root,
            stage=f"{stage_prefix}-delete",
            check=False,
        )
        payload = _device_inventory(
            runner,
            source_root=source_root,
            state_root=state_root,
            env=env,
            available_only=False,
            failure_code=failure_code,
        )
        remaining = candidates_for(payload)
        if not remaining:
            return
        if remaining[0]["udid"] != udid:
            fail(failure_code)
        if attempt < len(_SIMULATOR_DELETE_BACKOFF_SECONDS):
            time.sleep(_SIMULATOR_DELETE_BACKOFF_SECONDS[attempt])
    fail(failure_code)


def _delete_exact_owned_simulator(
    runner: CommandRunner,
    *,
    source_root: Path,
    state_root: Path,
    env: Mapping[str, str],
    udid: str,
    plan: AppleValidationPlan,
    failure_code: str,
) -> None:
    _delete_owned_simulator_with_retry(
        runner,
        source_root=source_root,
        state_root=state_root,
        env=env,
        udid=udid,
        failure_code=failure_code,
        stage_prefix="simulator",
        candidates_for=lambda payload: _exact_owned_candidates(
            plan,
            payload,
            require_available=False,
        ),
    )


def _delete_recorded_owned_simulator(
    runner: CommandRunner,
    *,
    source_root: Path,
    state_root: Path,
    env: Mapping[str, str],
    row: Mapping[str, str],
    udid: str,
    failure_code: str,
) -> None:
    """Delete one exact simulator proven by a persisted CIW ownership row."""

    _delete_owned_simulator_with_retry(
        runner,
        source_root=source_root,
        state_root=state_root,
        env=env,
        udid=udid,
        failure_code=failure_code,
        stage_prefix="simulator-stale",
        candidates_for=lambda payload: _recorded_owned_candidates(row, payload),
    )


def _delete_recorded_owned_companion(
    runner: CommandRunner,
    *,
    source_root: Path,
    state_root: Path,
    env: Mapping[str, str],
    row: Mapping[str, str],
    udid: str,
    failure_code: str,
) -> None:
    """Delete one exact External Display companion derived from a persisted row."""

    _delete_owned_simulator_with_retry(
        runner,
        source_root=source_root,
        state_root=state_root,
        env=env,
        udid=udid,
        failure_code=failure_code,
        stage_prefix="simulator-external-display",
        candidates_for=lambda payload: _recorded_owned_companion_candidates(
            row,
            payload,
        ),
    )


def _delete_recorded_owned_objects(
    runner: CommandRunner,
    *,
    source_root: Path,
    state_root: Path,
    env: Mapping[str, str],
    row: Mapping[str, str],
    failure_code: str,
) -> None:
    """Delete a recorded primary and its exact companion before releasing the row."""

    payload = _device_inventory(
        runner,
        source_root=source_root,
        state_root=state_root,
        env=env,
        available_only=False,
        failure_code=failure_code,
    )
    primary = _recorded_owned_candidates(row, payload)
    if primary:
        _delete_recorded_owned_simulator(
            runner,
            source_root=source_root,
            state_root=state_root,
            env=env,
            row=row,
            udid=primary[0]["udid"],
            failure_code=failure_code,
        )

    payload = _device_inventory(
        runner,
        source_root=source_root,
        state_root=state_root,
        env=env,
        available_only=False,
        failure_code=failure_code,
    )
    companions = _recorded_owned_companion_candidates(row, payload)
    if companions:
        _delete_recorded_owned_companion(
            runner,
            source_root=source_root,
            state_root=state_root,
            env=env,
            row=row,
            udid=companions[0]["udid"],
            failure_code=failure_code,
        )

    payload = _device_inventory(
        runner,
        source_root=source_root,
        state_root=state_root,
        env=env,
        available_only=False,
        failure_code=failure_code,
    )
    if _recorded_owned_candidates(row, payload) or _recorded_owned_companion_candidates(
        row,
        payload,
    ):
        fail(failure_code)


def _reconcile_stale_ownership_rows(
    plan: AppleValidationPlan,
    source_root: Path,
    state_root: Path,
    runner: CommandRunner,
    env: Mapping[str, str],
    ownership: SimulatorOwnership,
) -> None:
    """Terminalize stale CIW rows from older exact heads before new selection."""

    current_owner_key = _simulator_owner_key(plan)
    for row in tuple(ownership.rows):
        if row["owner_key"] == current_owner_key:
            continue
        _delete_recorded_owned_objects(
            runner,
            source_root=source_root,
            state_root=state_root,
            env=env,
            row=row,
            failure_code="cleanup_failed",
        )
        ownership.replace(row["owner_key"], None)


def _recover_stale_owned_simulator(
    plan: AppleValidationPlan,
    source_root: Path,
    state_root: Path,
    runner: CommandRunner,
    env: Mapping[str, str],
    ownership: SimulatorOwnership,
) -> None:
    owner_key = _simulator_owner_key(plan)
    _reconcile_stale_ownership_rows(
        plan,
        source_root,
        state_root,
        runner,
        env,
        ownership,
    )
    row = ownership.row(owner_key)
    payload = _device_inventory(
        runner,
        source_root=source_root,
        state_root=state_root,
        env=env,
        available_only=False,
        failure_code="simulator_unavailable",
    )
    exact_candidates = _exact_owned_candidates(
        plan,
        payload,
        require_available=False,
    )
    if row is None:
        if exact_candidates:
            fail("simulator_unowned")
        return
    _require_record_identity(plan, row)
    _delete_recorded_owned_objects(
        runner,
        source_root=source_root,
        state_root=state_root,
        env=env,
        row=row,
        failure_code="cleanup_failed",
    )
    ownership.replace(owner_key, None)


def _select_simulator_locked(
    plan: AppleValidationPlan,
    source_root: Path,
    state_root: Path,
    runner: CommandRunner,
    env: Mapping[str, str],
    ownership: SimulatorOwnership,
) -> SimulatorLease:
    simulator = plan.simulator
    if simulator is None:
        fail("unsafe_destination")
    _recover_stale_owned_simulator(
        plan,
        source_root,
        state_root,
        runner,
        env,
        ownership,
    )
    owner_key = _simulator_owner_key(plan)
    ownership.replace(
        owner_key,
        _ownership_record(plan, status="pending-create"),
    )
    if not simulator.allow_create:
        fail("simulator_unavailable")
    created_outcome = _run(
        runner,
        (
            "xcrun",
            "simctl",
            "create",
            _simulator_device_name(plan),
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
    ownership.replace(
        owner_key,
        _ownership_record(plan, status="owned", udid=udid),
    )
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
        created=True,
    )


def select_simulator(
    plan: AppleValidationPlan,
    source_root: Path,
    state_root: Path,
    runner: CommandRunner,
    env: Mapping[str, str],
    *,
    ownership: SimulatorOwnership | None = None,
) -> SimulatorLease:
    if ownership is not None:
        return _select_simulator_locked(
            plan,
            source_root,
            state_root,
            runner,
            env,
            ownership,
        )
    with _simulator_ownership(env, state_root) as acquired:
        return _select_simulator_locked(
            plan,
            source_root,
            state_root,
            runner,
            env,
            acquired,
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


def _cleanup_simulator_locked(
    source_root: Path,
    state_root: Path,
    plan: AppleValidationPlan,
    *,
    runner: CommandRunner,
    environment: Mapping[str, str],
    ownership: SimulatorOwnership,
) -> None:
    _reconcile_stale_ownership_rows(
        plan,
        source_root,
        state_root,
        runner,
        environment,
        ownership,
    )
    owner_key = _simulator_owner_key(plan)
    row = ownership.row(owner_key)
    payload = _device_inventory(
        runner,
        source_root=source_root,
        state_root=state_root,
        env=environment,
        available_only=False,
        failure_code="cleanup_failed",
    )
    exact_candidates = _exact_owned_candidates(
        plan,
        payload,
        require_available=False,
    )
    if row is None:
        if exact_candidates:
            fail("cleanup_failed")
        return
    _require_record_identity(plan, row)
    _delete_recorded_owned_objects(
        runner,
        source_root=source_root,
        state_root=state_root,
        env=environment,
        row=row,
        failure_code="cleanup_failed",
    )
    ownership.replace(owner_key, None)


def _cleanup_apple_state_locked(
    source_root: Path,
    state_root: Path,
    plan: AppleValidationPlan,
    *,
    runner: CommandRunner,
    environment: Mapping[str, str],
    ownership: SimulatorOwnership | None,
) -> None:
    if plan.requires_simulator:
        if ownership is None:
            fail("simulator_ownership_invalid")
        _cleanup_simulator_locked(
            source_root,
            state_root,
            plan,
            runner=runner,
            environment=environment,
            ownership=ownership,
        )
    for path in _cleanup_targets(source_root, state_root, plan):
        _remove_no_follow(path)


def cleanup_apple_state(
    source_root: Path,
    state_root: Path,
    plan: AppleValidationPlan,
    *,
    runner: CommandRunner | None = None,
    environment: Mapping[str, str] | None = None,
    ownership: SimulatorOwnership | None = None,
) -> None:
    command_runner = runner or SubprocessCommandRunner()
    env = dict(environment or os.environ)
    if plan.requires_simulator and ownership is None:
        with _simulator_ownership(env, state_root) as acquired:
            _cleanup_apple_state_locked(
                source_root,
                state_root,
                plan,
                runner=command_runner,
                environment=env,
                ownership=acquired,
            )
        return
    _cleanup_apple_state_locked(
        source_root,
        state_root,
        plan,
        runner=command_runner,
        environment=env,
        ownership=ownership,
    )


def _assert_zero_apple_residue_locked(
    source_root: Path,
    state_root: Path,
    plan: AppleValidationPlan,
    *,
    runner: CommandRunner,
    environment: Mapping[str, str],
    ownership: SimulatorOwnership | None,
) -> None:
    remaining = [
        str(path)
        for path in _cleanup_targets(source_root, state_root, plan)
        if _lstat(path) is not None
    ]
    if remaining:
        fail("cleanup_failed")
    if plan.requires_simulator:
        if ownership is None:
            fail("simulator_ownership_invalid")
        if ownership.rows:
            fail("cleanup_failed")
        payload = _device_inventory(
            runner,
            source_root=source_root,
            state_root=state_root,
            env=environment,
            available_only=False,
            failure_code="cleanup_failed",
            record_log=False,
        )
        if _exact_owned_candidates(
            plan,
            payload,
            require_available=False,
        ):
            fail("cleanup_failed")


def assert_zero_apple_residue(
    source_root: Path,
    state_root: Path,
    plan: AppleValidationPlan,
    *,
    runner: CommandRunner | None = None,
    environment: Mapping[str, str] | None = None,
    ownership: SimulatorOwnership | None = None,
) -> None:
    command_runner = runner or SubprocessCommandRunner()
    env = dict(environment or os.environ)
    if plan.requires_simulator and ownership is None:
        with _simulator_ownership(env, state_root) as acquired:
            _assert_zero_apple_residue_locked(
                source_root,
                state_root,
                plan,
                runner=command_runner,
                environment=env,
                ownership=acquired,
            )
        return
    _assert_zero_apple_residue_locked(
        source_root,
        state_root,
        plan,
        runner=command_runner,
        environment=env,
        ownership=ownership,
    )


def _execute_apple_plan_locked(
    *,
    plan: AppleValidationPlan,
    source_root: Path,
    state_root: Path,
    runner: CommandRunner,
    environment: Mapping[str, str],
    ownership: SimulatorOwnership | None,
) -> AppleValidationResult:
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
            runner,
            env,
        )
        completed.extend((AppleStage.TOOLCHAIN_VERIFY, AppleStage.SDK_VERIFY))
        if plan.request.validation_profile is AppleProfile.SOURCE_AUDIT:
            completed.append(AppleStage.SOURCE_AUDIT)
        if plan.requires_simulator:
            if ownership is None:
                fail("simulator_ownership_invalid")
            lease = select_simulator(
                plan,
                source_root,
                state_root,
                runner,
                env,
                ownership=ownership,
            )
            completed.append(AppleStage.SIMULATOR_SELECT)
        for command in plan.commands:
            output_verified = (
                _execute_command(
                    plan,
                    command,
                    source_root,
                    state_root,
                    runner,
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
            runner=runner,
            environment=env,
            ownership=ownership,
        )
        cleanup_result = "success"
        assert_zero_apple_residue(
            source_root,
            state_root,
            plan,
            runner=runner,
            environment=env,
            ownership=ownership,
        )
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
                runner=runner,
                environment=env,
                ownership=ownership,
            )
        except AppleValidationError as cleanup_error:
            raise AppleValidationError(
                primary_error.code,
                cleanup_failed=True,
            ) from cleanup_error
        raise


def execute_apple_plan(
    *,
    plan: AppleValidationPlan,
    source_root: Path,
    state_root: Path,
    runner: CommandRunner | None = None,
    environment: Mapping[str, str] | None = None,
) -> AppleValidationResult:
    command_runner = runner or SubprocessCommandRunner()
    env = dict(environment or os.environ)
    ownership_context = (
        _simulator_ownership(env, state_root)
        if plan.requires_simulator
        else nullcontext(None)
    )
    with ownership_context as ownership:
        return _execute_apple_plan_locked(
            plan=plan,
            source_root=source_root,
            state_root=state_root,
            runner=command_runner,
            environment=env,
            ownership=ownership,
        )
