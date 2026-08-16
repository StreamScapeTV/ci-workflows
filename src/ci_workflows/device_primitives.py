"""Product-neutral device discovery, selection, lease, execution, and cleanup primitives."""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Protocol, Sequence

_SAFE_CODE = re.compile(r"^[a-z][a-z0-9_]{2,95}$")
_SLUG = re.compile(r"^[a-z][a-z0-9._-]{0,63}$")
_RUNTIME = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_CAPABILITY = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
_READY_STATES = {"ready", "online", "booted"}
_OFFLINE_STATES = {"offline", "shutdown"}
_ALLOWED_KINDS = {"physical", "simulator"}
_MISSING = object()


class DevicePrimitiveError(RuntimeError):
    """Fail closed with one stable, non-sensitive public error code."""

    def __init__(self, code: str) -> None:
        if _SAFE_CODE.fullmatch(code) is None:
            raise ValueError("device primitive error code must be safe")
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class DeviceDescriptor:
    """Normalized device metadata with a runner-private identifier held only in memory."""

    identity_hash: str
    platform: str
    family: str
    runtime: str
    kind: str
    state: str
    capabilities: tuple[str, ...]
    shared: bool
    _private_identifier: str = field(repr=False, compare=False)

    def public_projection(self) -> dict[str, object]:
        return {
            "identity_hash": self.identity_hash,
            "platform": self.platform,
            "family": self.family,
            "runtime": self.runtime,
            "kind": self.kind,
            "state": self.state,
            "capabilities": list(self.capabilities),
            "shared": self.shared,
        }


@dataclass(frozen=True, slots=True)
class DeviceQuery:
    platform: str
    family: str
    capabilities: tuple[str, ...]
    runtime: str | None = None
    kind: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "platform", _slug(self.platform, "device_query_invalid"))
        object.__setattr__(self, "family", _slug(self.family, "device_query_invalid"))
        object.__setattr__(
            self,
            "capabilities",
            _capabilities(self.capabilities, "device_query_invalid"),
        )
        if self.runtime is not None:
            object.__setattr__(self, "runtime", _runtime(self.runtime, "device_query_invalid"))
        if self.kind is not None:
            kind = _slug(self.kind, "device_query_invalid")
            _require(kind in _ALLOWED_KINDS, "device_query_invalid")
            object.__setattr__(self, "kind", kind)


@dataclass(frozen=True, slots=True)
class CommandOutcome:
    returncode: int
    elapsed_seconds: int = 0

    def __post_init__(self) -> None:
        _require(type(self.returncode) is int, "process_result_invalid")
        _require(
            type(self.elapsed_seconds) is int
            and 0 <= self.elapsed_seconds <= 7 * 24 * 60 * 60,
            "process_result_invalid",
        )


@dataclass(frozen=True, slots=True)
class DeviceLease:
    resource_hash: str
    lease_id: str
    _token: str = field(repr=False, compare=False)

    def public_projection(self) -> dict[str, str]:
        return {"resource_hash": self.resource_hash, "lease_id": self.lease_id}


@dataclass(frozen=True, slots=True)
class DeviceOperationResult:
    result: str
    failure_code: str
    device: Mapping[str, object]
    command_returncode: int | None
    elapsed_seconds: int
    restoration: str
    cleanup: str
    lease: str
    lease_id: str

    def public_projection(self) -> dict[str, object]:
        return {
            "result": self.result,
            "failure_code": self.failure_code,
            "device": dict(self.device),
            "command_returncode": self.command_returncode,
            "elapsed_seconds": self.elapsed_seconds,
            "restoration": self.restoration,
            "cleanup": self.cleanup,
            "lease": self.lease,
            "lease_id": self.lease_id,
        }


class DeviceDiscoveryBackend(Protocol):
    def discover(self, *, environment: Mapping[str, str]) -> Sequence[Mapping[str, object]]: ...


class DeviceLeaseBackend(Protocol):
    def acquire(
        self,
        *,
        resource_hash: str,
        owner_hash: str,
        ttl_seconds: int,
        environment: Mapping[str, str],
    ) -> str: ...

    def release(self, token: str, *, environment: Mapping[str, str]) -> None: ...


class DeviceProcessBackend(Protocol):
    def run(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        environment: Mapping[str, str],
        timeout_seconds: int,
    ) -> CommandOutcome: ...


class DeviceStateBackend(Protocol):
    def snapshot(
        self,
        device: DeviceDescriptor,
        *,
        environment: Mapping[str, str],
    ) -> object: ...

    def restore(
        self,
        device: DeviceDescriptor,
        snapshot: object,
        *,
        environment: Mapping[str, str],
    ) -> None: ...

    def cleanup(
        self,
        device: DeviceDescriptor,
        *,
        environment: Mapping[str, str],
    ) -> None: ...

    def residue(
        self,
        device: DeviceDescriptor,
        *,
        environment: Mapping[str, str],
    ) -> Sequence[str]: ...


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise DevicePrimitiveError(code)


def _slug(value: object, code: str) -> str:
    _require(isinstance(value, str), code)
    text = value.strip().casefold()
    _require(_SLUG.fullmatch(text) is not None, code)
    return text


def _runtime(value: object, code: str) -> str:
    _require(isinstance(value, str), code)
    text = value.strip().casefold()
    _require(_RUNTIME.fullmatch(text) is not None, code)
    return text


def _capabilities(values: object, code: str) -> tuple[str, ...]:
    _require(
        isinstance(values, Sequence) and not isinstance(values, (str, bytes)),
        code,
    )
    result: set[str] = set()
    for value in values:
        _require(isinstance(value, str), code)
        item = value.strip().casefold()
        _require(_CAPABILITY.fullmatch(item) is not None, code)
        result.add(item)
    _require(0 < len(result) <= 64, code)
    return tuple(sorted(result))


def _state(value: object) -> str:
    state = _slug(value, "device_discovery_invalid")
    if state in _READY_STATES:
        return "ready"
    if state in _OFFLINE_STATES:
        return "offline"
    _require(state in {"busy", "unauthorized"}, "device_discovery_invalid")
    return state


def _private_identifier(value: object) -> str:
    _require(isinstance(value, str), "device_discovery_invalid")
    encoded = value.encode("utf-8")
    _require(
        1 <= len(encoded) <= 512
        and "\x00" not in value
        and "\r" not in value
        and "\n" not in value,
        "device_discovery_invalid",
    )
    return value


def normalize_device_record(raw: Mapping[str, object]) -> DeviceDescriptor:
    """Normalize one backend record without exposing its private identifier."""

    _require(isinstance(raw, Mapping), "device_discovery_invalid")
    _require(
        {
            "identifier",
            "platform",
            "family",
            "runtime",
            "kind",
            "state",
            "capabilities",
            "shared",
        }
        <= set(raw),
        "device_discovery_invalid",
    )
    identifier = _private_identifier(raw["identifier"])
    platform = _slug(raw["platform"], "device_discovery_invalid")
    family = _slug(raw["family"], "device_discovery_invalid")
    runtime = _runtime(raw["runtime"], "device_discovery_invalid")
    kind = _slug(raw["kind"], "device_discovery_invalid")
    _require(kind in _ALLOWED_KINDS, "device_discovery_invalid")
    _require(type(raw["shared"]) is bool, "device_discovery_invalid")
    identity_hash = hashlib.sha256(
        f"ciw-device-v1\0{platform}\0{kind}\0{identifier}".encode("utf-8")
    ).hexdigest()
    return DeviceDescriptor(
        identity_hash=identity_hash,
        platform=platform,
        family=family,
        runtime=runtime,
        kind=kind,
        state=_state(raw["state"]),
        capabilities=_capabilities(raw["capabilities"], "device_discovery_invalid"),
        shared=bool(raw["shared"]),
        _private_identifier=identifier,
    )


def discover_devices(
    backend: DeviceDiscoveryBackend,
    *,
    environment: Mapping[str, str],
) -> tuple[DeviceDescriptor, ...]:
    """Discover and normalize a bounded physical/simulator inventory."""

    try:
        rows = backend.discover(environment=environment)
    except DevicePrimitiveError:
        raise
    except Exception as error:
        raise DevicePrimitiveError("device_discovery_failed") from error
    _require(
        isinstance(rows, Sequence)
        and not isinstance(rows, (str, bytes))
        and 0 < len(rows) <= 128,
        "device_discovery_invalid",
    )
    devices = tuple(normalize_device_record(row) for row in rows)
    identities = [device.identity_hash for device in devices]
    _require(len(identities) == len(set(identities)), "device_discovery_invalid")
    return devices


def select_device(
    devices: Sequence[DeviceDescriptor],
    query: DeviceQuery,
) -> DeviceDescriptor:
    """Select one ready device deterministically by capability metadata."""

    _require(
        isinstance(devices, Sequence) and 0 < len(devices) <= 128,
        "device_selection_invalid",
    )
    required = set(query.capabilities)
    candidates = [
        device
        for device in devices
        if device.state == "ready"
        and device.platform == query.platform
        and device.family == query.family
        and (query.runtime is None or device.runtime == query.runtime)
        and (query.kind is None or device.kind == query.kind)
        and required <= set(device.capabilities)
    ]
    if not candidates:
        if any(
            device.platform == query.platform
            and device.family == query.family
            and device.state == "offline"
            for device in devices
        ):
            raise DevicePrimitiveError("device_offline")
        raise DevicePrimitiveError("device_no_match")
    return min(candidates, key=lambda device: device.identity_hash)


def lease_owner_hash(environment: Mapping[str, str]) -> str:
    """Derive an opaque lease owner from runner environment."""

    explicit = str(environment.get("CIW_DEVICE_LEASE_OWNER", "")).strip()
    if explicit:
        material = explicit
    else:
        repository = str(environment.get("GITHUB_REPOSITORY", "")).strip()
        run_id = str(environment.get("GITHUB_RUN_ID", "")).strip()
        attempt = str(environment.get("GITHUB_RUN_ATTEMPT", "")).strip()
        _require(repository and run_id and attempt, "device_lease_owner_missing")
        material = f"{repository}\0{run_id}\0{attempt}"
    _require(
        1 <= len(material.encode("utf-8")) <= 1024
        and "\x00" not in material
        and "\r" not in material
        and "\n" not in material,
        "device_lease_owner_invalid",
    )
    return hashlib.sha256(
        f"ciw-device-owner-v1\0{material}".encode("utf-8")
    ).hexdigest()


def acquire_device_lease(
    device: DeviceDescriptor,
    backend: DeviceLeaseBackend,
    *,
    environment: Mapping[str, str],
    ttl_seconds: int,
) -> DeviceLease:
    """Fence shared hardware using only hashed device/run identities."""

    _require(device.shared, "device_lease_not_required")
    _require(type(ttl_seconds) is int and 1 <= ttl_seconds <= 86400, "device_lease_invalid")
    resource_hash = hashlib.sha256(
        f"ciw-device-resource-v1\0{device.platform}\0{device.identity_hash}".encode("utf-8")
    ).hexdigest()
    try:
        token = backend.acquire(
            resource_hash=resource_hash,
            owner_hash=lease_owner_hash(environment),
            ttl_seconds=ttl_seconds,
            environment=environment,
        )
    except DevicePrimitiveError:
        raise
    except Exception as error:
        raise DevicePrimitiveError("device_lease_failed") from error
    _require(
        isinstance(token, str)
        and 8 <= len(token.encode("utf-8")) <= 4096
        and "\x00" not in token
        and "\r" not in token
        and "\n" not in token,
        "device_lease_invalid",
    )
    return DeviceLease(
        resource_hash=resource_hash,
        lease_id=hashlib.sha256(f"ciw-device-lease-v1\0{token}".encode("utf-8")).hexdigest(),
        _token=token,
    )


def release_device_lease(
    lease: DeviceLease,
    backend: DeviceLeaseBackend,
    *,
    environment: Mapping[str, str],
) -> None:
    try:
        backend.release(lease._token, environment=environment)
    except DevicePrimitiveError:
        raise
    except Exception as error:
        raise DevicePrimitiveError("device_lease_release_failed") from error


def _checked_in_command(source_root: Path, command_path: str) -> Path:
    _require(isinstance(command_path, str) and command_path, "device_command_invalid")
    relative = Path(command_path)
    _require(
        not relative.is_absolute()
        and "\\" not in command_path
        and all(part not in {"", ".", ".."} for part in relative.parts),
        "device_command_invalid",
    )
    try:
        root = source_root.resolve(strict=True)
    except OSError as error:
        raise DevicePrimitiveError("device_command_invalid") from error
    current = root
    for part in relative.parts:
        current /= part
        _require(not current.is_symlink(), "device_command_invalid")
    _require(current.is_file() and not current.is_symlink(), "device_command_invalid")
    _require(os.access(current, os.X_OK), "device_command_not_executable")
    try:
        resolved = current.resolve(strict=True)
    except OSError as error:
        raise DevicePrimitiveError("device_command_invalid") from error
    _require(resolved.is_relative_to(root), "device_command_invalid")
    return resolved


def _arguments(values: Sequence[str]) -> tuple[str, ...]:
    _require(
        isinstance(values, Sequence)
        and not isinstance(values, (str, bytes))
        and len(values) <= 64,
        "device_command_invalid",
    )
    result: list[str] = []
    for value in values:
        _require(
            isinstance(value, str)
            and len(value.encode("utf-8")) <= 4096
            and "\x00" not in value
            and "\r" not in value
            and "\n" not in value,
            "device_command_invalid",
        )
        result.append(value)
    return tuple(result)


def execute_checked_in_command(
    device: DeviceDescriptor,
    *,
    source_root: Path,
    command_path: str,
    arguments: Sequence[str],
    process: DeviceProcessBackend,
    environment: Mapping[str, str],
    timeout_seconds: int,
) -> CommandOutcome:
    """Run a checked-in command; the private device ID is environment-only."""

    _require(type(timeout_seconds) is int and 1 <= timeout_seconds <= 86400, "device_command_invalid")
    command = _checked_in_command(source_root, command_path)
    process_environment = dict(environment)
    process_environment.update(
        {
            "CIW_DEVICE_IDENTIFIER": device._private_identifier,
            "CIW_DEVICE_IDENTITY_SHA256": device.identity_hash,
            "CIW_DEVICE_PLATFORM": device.platform,
            "CIW_DEVICE_FAMILY": device.family,
            "CIW_DEVICE_RUNTIME": device.runtime,
            "CIW_DEVICE_KIND": device.kind,
            "CIW_DEVICE_CAPABILITIES": ",".join(device.capabilities),
        }
    )
    try:
        outcome = process.run(
            (str(command), *_arguments(arguments)),
            cwd=source_root.resolve(strict=True),
            environment=process_environment,
            timeout_seconds=timeout_seconds,
        )
    except DevicePrimitiveError:
        raise
    except Exception as error:
        raise DevicePrimitiveError("device_command_failed") from error
    _require(isinstance(outcome, CommandOutcome), "process_result_invalid")
    return outcome


def execute_device_operation(
    *,
    discovery: DeviceDiscoveryBackend,
    query: DeviceQuery,
    source_root: Path,
    command_path: str,
    arguments: Sequence[str],
    process: DeviceProcessBackend,
    state: DeviceStateBackend,
    environment: Mapping[str, str],
    timeout_seconds: int,
    lease_backend: DeviceLeaseBackend | None = None,
    lease_ttl_seconds: int = 3600,
) -> DeviceOperationResult:
    """Execute with restoration/cleanup and never mutate shared hardware before fencing."""

    selected = select_device(discover_devices(discovery, environment=environment), query)
    lease: DeviceLease | None = None
    snapshot: object = _MISSING
    outcome: CommandOutcome | None = None
    failure_code = ""
    restoration = "not-started"
    cleanup = "not-run"
    lease_result = "not-required" if not selected.shared else "not-acquired"

    try:
        if selected.shared:
            _require(lease_backend is not None, "device_lease_required")
            lease = acquire_device_lease(
                selected,
                lease_backend,
                environment=environment,
                ttl_seconds=lease_ttl_seconds,
            )
            lease_result = "acquired"
        snapshot = state.snapshot(selected, environment=environment)
        outcome = execute_checked_in_command(
            selected,
            source_root=source_root,
            command_path=command_path,
            arguments=arguments,
            process=process,
            environment=environment,
            timeout_seconds=timeout_seconds,
        )
        if outcome.returncode != 0:
            failure_code = "device_command_failed"
    except DevicePrimitiveError as error:
        failure_code = error.code
    except Exception:
        failure_code = "device_operation_failed"
    finally:
        # A shared device may be restored or cleaned only after this run owns its lease.
        may_manage_device_state = not selected.shared or lease is not None
        cleanup_failed = False
        if may_manage_device_state:
            if snapshot is not _MISSING:
                try:
                    state.restore(selected, snapshot, environment=environment)
                    restoration = "success"
                except Exception:
                    restoration = "failure"
                    cleanup_failed = True
                    if not failure_code:
                        failure_code = "device_restoration_failed"
            try:
                state.cleanup(selected, environment=environment)
                residue = state.residue(selected, environment=environment)
                _require(
                    isinstance(residue, Sequence) and not isinstance(residue, (str, bytes)),
                    "device_cleanup_failed",
                )
                _require(not tuple(residue), "device_cleanup_failed")
                cleanup = "success"
            except Exception:
                cleanup = "failure"
                cleanup_failed = True
                if not failure_code:
                    failure_code = "device_cleanup_failed"
        else:
            # No lease means no run-owned device state exists to restore or clean.
            cleanup = "success"
        if lease is not None and lease_backend is not None:
            try:
                release_device_lease(lease, lease_backend, environment=environment)
                lease_result = "released"
            except Exception:
                lease_result = "release-failed"
                cleanup_failed = True
                if not failure_code:
                    failure_code = "device_lease_release_failed"
        if cleanup_failed and cleanup == "success":
            cleanup = "failure"

    return DeviceOperationResult(
        result="failure" if failure_code else "success",
        failure_code=failure_code,
        device=selected.public_projection(),
        command_returncode=None if outcome is None else outcome.returncode,
        elapsed_seconds=0 if outcome is None else outcome.elapsed_seconds,
        restoration=restoration,
        cleanup=cleanup,
        lease=lease_result,
        lease_id="" if lease is None else lease.lease_id,
    )
