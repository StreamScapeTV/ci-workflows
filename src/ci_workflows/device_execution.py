"""Synthetic discovery, lock, evidence, lifecycle, and cleanup primitives."""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Protocol, Sequence

from .device_contract import require, version_tuple
from .device_types import (
    DeviceFamily,
    DevicePlan,
    DeviceRecord,
    DeviceResult,
    DeviceValidationError,
    LockReceipt,
    LockReleaseReceipt,
    SelectedDevice,
    SerialPolicy,
)

_ANDROID_LINE = re.compile(
    r"^(?P<identifier>[A-Za-z0-9][A-Za-z0-9._:-]{2,127})\t"
    r"(?P<state>online|offline|unauthorized)\t"
    r"model=(?P<model>[a-z][a-z0-9-]{1,63})\t"
    r"api=(?P<api>[1-9][0-9]{0,2})\t"
    r"connection=(?P<connection>usb|network)\t"
    r"capabilities=(?P<capabilities>[a-z0-9-]+(?:,[a-z0-9-]+)*)\t"
    r"personal=(?P<personal>true|false)\t"
    r"conflict=(?P<conflict>true|false)$"
)
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{2,127}$")
_MODEL = re.compile(r"^[a-z][a-z0-9-]{1,63}$")
_CAPABILITY = re.compile(r"^[a-z][a-z0-9-]{1,63}$")
_EVIDENCE_NAME = re.compile(r"^[a-z][a-z0-9._-]{1,95}$")
_MEDIA_TYPE = re.compile(r"^(?:application/json|text/plain)$")


def _boolean(value: object) -> bool:
    if value is True:
        return True
    if value is False:
        return False
    raise DeviceValidationError("device_inventory_malformed")


def parse_android_inventory(raw: str) -> tuple[DeviceRecord, ...]:
    """Parse one bounded synthetic ``adb devices -l`` projection."""

    require(isinstance(raw, str) and len(raw.encode("utf-8")) <= 65536, "device_inventory_malformed")
    records: list[DeviceRecord] = []
    seen: set[str] = set()
    for line in raw.splitlines():
        if not line.strip():
            continue
        match = _ANDROID_LINE.fullmatch(line)
        require(match is not None, "device_inventory_malformed")
        values = match.groupdict()
        identifier = values["identifier"]
        require(identifier not in seen, "device_inventory_malformed")
        seen.add(identifier)
        capabilities = tuple(sorted(set(values["capabilities"].split(","))))
        records.append(
            DeviceRecord(
                raw_identifier=identifier,
                family=DeviceFamily.ANDROID,
                state=values["state"],
                connection=values["connection"],
                model=values["model"],
                capabilities=capabilities,
                api_level=int(values["api"]),
                personal=values["personal"] == "true",
                conflicting=values["conflict"] == "true",
            )
        )
    require(0 < len(records) <= 64, "device_inventory_malformed")
    return tuple(records)


def parse_apple_inventory(
    raw: str,
    family: DeviceFamily,
) -> tuple[DeviceRecord, ...]:
    """Parse a bounded synthetic projection of ``xcrun devicectl list``."""

    require(family in {DeviceFamily.IOS, DeviceFamily.TVOS}, "unsupported_family")
    require(isinstance(raw, str) and len(raw.encode("utf-8")) <= 131072, "device_inventory_malformed")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as error:
        raise DeviceValidationError("device_inventory_malformed") from error
    require(isinstance(payload, Mapping) and set(payload) == {"devices"}, "device_inventory_malformed")
    rows = payload["devices"]
    require(isinstance(rows, list) and 0 < len(rows) <= 64, "device_inventory_malformed")
    records: list[DeviceRecord] = []
    seen: set[str] = set()
    expected_keys = {
        "identifier",
        "family",
        "state",
        "connection",
        "model",
        "os_version",
        "capabilities",
        "personal",
        "conflict",
    }
    for row in rows:
        require(isinstance(row, Mapping) and set(row) == expected_keys, "device_inventory_malformed")
        identifier = row["identifier"]
        model = row["model"]
        capabilities = row["capabilities"]
        require(
            isinstance(identifier, str)
            and _IDENTIFIER.fullmatch(identifier) is not None
            and identifier not in seen,
            "device_inventory_malformed",
        )
        seen.add(identifier)
        require(row["family"] == family.value, "device_family_mismatch")
        require(row["state"] in {"online", "offline", "unauthorized"}, "device_inventory_malformed")
        require(row["connection"] in {"usb", "paired", "network"}, "device_inventory_malformed")
        require(isinstance(model, str) and _MODEL.fullmatch(model) is not None, "device_inventory_malformed")
        require(
            isinstance(capabilities, list)
            and capabilities
            and len(capabilities) == len(set(capabilities))
            and all(
                isinstance(value, str) and _CAPABILITY.fullmatch(value) is not None
                for value in capabilities
            ),
            "device_inventory_malformed",
        )
        os_version = row["os_version"]
        version_tuple(os_version)
        records.append(
            DeviceRecord(
                raw_identifier=identifier,
                family=family,
                state=str(row["state"]),
                connection=str(row["connection"]),
                model=model,
                capabilities=tuple(sorted(capabilities)),
                os_version=os_version,
                personal=_boolean(row["personal"]),
                conflicting=_boolean(row["conflict"]),
            )
        )
    return tuple(records)


def parse_inventory(
    family: DeviceFamily,
    raw: str,
) -> tuple[DeviceRecord, ...]:
    if family is DeviceFamily.ANDROID:
        return parse_android_inventory(raw)
    return parse_apple_inventory(raw, family)


def stable_identity_hash(
    profile_id: str,
    family: DeviceFamily,
    raw_identifier: str,
) -> str:
    material = f"{profile_id}\0{family.value}\0{raw_identifier}".encode("utf-8")
    return hashlib.sha256(material).hexdigest()


def _version_allowed(plan: DevicePlan, record: DeviceRecord) -> bool:
    policy = plan.profile.version_policy
    if record.family is DeviceFamily.ANDROID:
        if record.api_level is None:
            return False
        return int(policy["api_min"]) <= record.api_level <= int(policy["api_max"])
    if record.os_version is None:
        return False
    return version_tuple(str(policy["os_min"])) <= version_tuple(
        record.os_version
    ) <= version_tuple(str(policy["os_max"]))


def _record_allowed(plan: DevicePlan, record: DeviceRecord) -> bool:
    if record.family is not plan.request.family:
        return False
    if record.state not in plan.profile.health_states:
        return False
    if record.connection not in plan.profile.connection_states:
        return False
    if record.personal or record.conflicting:
        return False
    if record.model not in plan.profile.models:
        return False
    if plan.request.capability not in record.capabilities:
        return False
    if not _version_allowed(plan, record):
        return False
    policy = plan.profile.serial_policy
    if policy is SerialPolicy.EXACT_CALLER:
        return record.raw_identifier == plan.request.device_identifier
    if policy is SerialPolicy.FORBIDDEN:
        return plan.request.device_identifier is None
    return plan.request.device_identifier is None


def select_device(
    plan: DevicePlan,
    records: Sequence[DeviceRecord],
) -> SelectedDevice:
    require(0 < len(records) <= 64, "device_inventory_malformed")
    candidates = [record for record in records if _record_allowed(plan, record)]
    if not candidates:
        if any(record.family is plan.request.family and record.state == "offline" for record in records):
            raise DeviceValidationError("device_offline")
        raise DeviceValidationError("device_no_match")
    decorated = sorted(
        (
            stable_identity_hash(
                plan.profile.profile_id,
                record.family,
                record.raw_identifier,
            ),
            record,
        )
        for record in candidates
    )
    if len(decorated) > 1 and plan.profile.selection_policy != "identity-hash":
        raise DeviceValidationError("device_multiple_matches")
    identity_hash, selected = decorated[0]
    os_or_api = (
        f"api-{selected.api_level}"
        if selected.family is DeviceFamily.ANDROID
        else f"os-{selected.os_version}"
    )
    return SelectedDevice(
        identity_hash=identity_hash,
        family=selected.family,
        model_class=selected.model,
        connection_class=selected.connection,
        os_or_api=os_or_api,
        capabilities=selected.capabilities,
        _raw_identifier=selected.raw_identifier,
    )


class DeviceLockAdapter(Protocol):
    def acquire(
        self,
        *,
        plan: DevicePlan,
        selected: SelectedDevice,
        now: int,
    ) -> LockReceipt: ...

    def release(
        self,
        receipt: LockReceipt,
        *,
        now: int,
    ) -> LockReleaseReceipt: ...


class InMemoryDeviceLockAdapter:
    """Deterministic test-only reference implementation of the lock boundary."""

    def __init__(self) -> None:
        self._leases: dict[str, LockReceipt] = {}
        self._epochs: dict[str, int] = {}

    @staticmethod
    def resource_key(plan: DevicePlan, selected: SelectedDevice) -> str:
        return (
            f"device:{selected.family.value}:"
            f"{plan.profile.profile_id}:{selected.identity_hash}"
        )

    @staticmethod
    def owner_hash(plan: DevicePlan) -> str:
        return hashlib.sha256(
            f"{plan.request.request_id}\0{plan.request.run_id}".encode("utf-8")
        ).hexdigest()

    def acquire(
        self,
        *,
        plan: DevicePlan,
        selected: SelectedDevice,
        now: int,
    ) -> LockReceipt:
        resource_key = self.resource_key(plan, selected)
        current = self._leases.get(resource_key)
        if current is not None and current.expires_at > now:
            return LockReceipt(
                accepted=False,
                resource_key=resource_key,
                request_id=plan.request.request_id,
                run_id=plan.request.run_id,
                device_family=selected.family,
                device_profile=plan.profile.profile_id,
                epoch=current.epoch,
                token="",
                owner_hash=current.owner_hash,
                expires_at=current.expires_at,
                next_actor="current-lock-owner",
                next_action="wait-for-release-or-expiry",
            )
        epoch = self._epochs.get(resource_key, 0) + 1
        self._epochs[resource_key] = epoch
        owner_hash = self.owner_hash(plan)
        expires_at = now + plan.request.max_duration_minutes * 60
        token = hashlib.sha256(
            f"{resource_key}\0{owner_hash}\0{epoch}\0{expires_at}".encode("utf-8")
        ).hexdigest()
        receipt = LockReceipt(
            accepted=True,
            resource_key=resource_key,
            request_id=plan.request.request_id,
            run_id=plan.request.run_id,
            device_family=selected.family,
            device_profile=plan.profile.profile_id,
            epoch=epoch,
            token=token,
            owner_hash=owner_hash,
            expires_at=expires_at,
            next_actor="device-validation-owner",
            next_action="execute-reviewed-profile",
        )
        self._leases[resource_key] = receipt
        return receipt

    def release(
        self,
        receipt: LockReceipt,
        *,
        now: int,
    ) -> LockReleaseReceipt:
        current = self._leases.get(receipt.resource_key)
        if (
            current is None
            or current.epoch != receipt.epoch
            or current.token != receipt.token
            or current.request_id != receipt.request_id
        ):
            raise DeviceValidationError("lock_stale_epoch")
        del self._leases[receipt.resource_key]
        release_receipt = hashlib.sha256(
            f"{receipt.resource_key}\0{receipt.request_id}\0"
            f"{receipt.epoch}\0{now}\0released".encode("utf-8")
        ).hexdigest()
        return LockReleaseReceipt(
            released=True,
            resource_key=receipt.resource_key,
            request_id=receipt.request_id,
            epoch=receipt.epoch,
            release_receipt=release_receipt,
        )

    def active_count(self) -> int:
        return len(self._leases)


class DeviceRuntime(Protocol):
    def snapshot(self, selected: SelectedDevice) -> Mapping[str, object]: ...

    def prepare(self, plan: DevicePlan, selected: SelectedDevice) -> None: ...

    def test(self, plan: DevicePlan, selected: SelectedDevice) -> Sequence[str]: ...

    def collect_evidence(
        self,
        plan: DevicePlan,
        selected: SelectedDevice,
    ) -> Sequence[str]: ...

    def connected(self, selected: SelectedDevice) -> bool: ...

    def restore(
        self,
        selected: SelectedDevice,
        snapshot: Mapping[str, object],
    ) -> None: ...

    def cleanup(self, plan: DevicePlan, selected: SelectedDevice) -> None: ...

    def residue(self, plan: DevicePlan, selected: SelectedDevice) -> Sequence[str]: ...


@dataclass
class SyntheticDeviceRuntime:
    """Stateful fake runtime used by contract smoke and unit tests only."""

    fail_stage: str | None = None
    disconnect_after_test: bool = False
    restored: bool = False
    cleaned: bool = False
    prepared: bool = False
    tested: bool = False
    evidence_collected: bool = False

    def _maybe_fail(self, stage: str, code: str) -> None:
        if self.fail_stage == stage:
            raise DeviceValidationError(code)

    def snapshot(self, selected: SelectedDevice) -> Mapping[str, object]:
        self._maybe_fail("snapshot", "stage_failed")
        return {
            "owned_install_present": False,
            "test_process_running": False,
            "state_hash": hashlib.sha256(
                selected.identity_hash.encode("ascii")
            ).hexdigest(),
        }

    def prepare(self, plan: DevicePlan, selected: SelectedDevice) -> None:
        self._maybe_fail("prepare", "prepare_failed")
        self.prepared = True

    def test(self, plan: DevicePlan, selected: SelectedDevice) -> Sequence[str]:
        self._maybe_fail("test", "stage_failed")
        self.tested = True
        return ("reviewed-test-profile-executed",)

    def collect_evidence(
        self,
        plan: DevicePlan,
        selected: SelectedDevice,
    ) -> Sequence[str]:
        self._maybe_fail("evidence", "evidence_policy_failed")
        self.evidence_collected = True
        return ("device-health-verified", "exact-source-verified")

    def connected(self, selected: SelectedDevice) -> bool:
        return not self.disconnect_after_test

    def restore(
        self,
        selected: SelectedDevice,
        snapshot: Mapping[str, object],
    ) -> None:
        self._maybe_fail("restore", "device_restoration_failed")
        self.restored = True

    def cleanup(self, plan: DevicePlan, selected: SelectedDevice) -> None:
        self._maybe_fail("cleanup", "cleanup_failed")
        self.cleaned = True

    def residue(self, plan: DevicePlan, selected: SelectedDevice) -> Sequence[str]:
        if self.fail_stage == "residue":
            return ("owned-residue",)
        return ()


def _resource_key_hash(receipt: LockReceipt) -> str:
    return hashlib.sha256(receipt.resource_key.encode("utf-8")).hexdigest()


def _timestamp(value: int) -> str:
    require(isinstance(value, int) and value >= 0, "evidence_policy_failed")
    return f"epoch:{value}"


def _artifact_inventory(
    retained: Sequence[Mapping[str, object]],
) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for item in retained:
        require(
            set(item) == {"name", "media_type", "bytes", "sha256"},
            "evidence_policy_failed",
        )
        name = item["name"]
        media_type = item["media_type"]
        byte_count = item["bytes"]
        digest = item["sha256"]
        require(
            isinstance(name, str)
            and _EVIDENCE_NAME.fullmatch(name) is not None
            and isinstance(media_type, str)
            and _MEDIA_TYPE.fullmatch(media_type) is not None
            and isinstance(byte_count, int)
            and 0 <= byte_count <= 8 * 1024 * 1024
            and isinstance(digest, str)
            and re.fullmatch(r"[0-9a-f]{64}", digest) is not None,
            "evidence_policy_failed",
        )
        result.append(
            {
                "name": name,
                "media_type": media_type,
                "bytes": byte_count,
                "sha256": digest,
            }
        )
    return sorted(result, key=lambda item: str(item["name"]))


def build_evidence_packet(
    *,
    plan: DevicePlan,
    selected: SelectedDevice,
    lock_receipt: LockReceipt,
    release_receipt: LockReleaseReceipt | None,
    evidence_contract: Mapping[str, object],
    started_at: int,
    ended_at: int,
    result: str,
    failure_code: str,
    assertions: Sequence[str],
    restoration: str,
    cleanup: str,
    retained_evidence: Sequence[Mapping[str, object]] = (),
    certification_scope: str | None = None,
) -> dict[str, object]:
    require(result in {"success", "failure"}, "evidence_policy_failed")
    require(ended_at >= started_at, "evidence_policy_failed")
    scopes = evidence_contract["certification_scope_by_family"]
    require(isinstance(scopes, Mapping), "evidence_policy_failed")
    expected_scope = str(scopes[selected.family.value])
    actual_scope = certification_scope or expected_scope
    require(actual_scope == expected_scope, "evidence_overclaim")
    limitations = list(evidence_contract["required_limitations"])
    packet = {
        "packet_version": evidence_contract["packet_version"],
        "request_id": plan.request.request_id,
        "issue_number": plan.request.issue_number,
        "repository": plan.request.repository,
        "source_sha": plan.request.admitted_sha,
        "device_family": selected.family.value,
        "device_profile": plan.profile.profile_id,
        "device_identity_hash": selected.identity_hash,
        "classification": {
            "family": selected.family.value,
            "os_or_api": selected.os_or_api,
            "model_class": selected.model_class,
            "capabilities": list(selected.capabilities),
            "connection_class": selected.connection_class,
        },
        "lock": {
            "accepted": lock_receipt.accepted,
            "epoch": lock_receipt.epoch,
            "owner_hash": lock_receipt.owner_hash,
            "resource_key_hash": _resource_key_hash(lock_receipt),
            "release_receipt": (
                release_receipt.release_receipt if release_receipt else ""
            ),
        },
        "command_profiles": {
            "prepare": plan.profile.command_profile.prepare_script,
            "test": plan.profile.command_profile.test_script,
            "evidence": plan.profile.command_profile.evidence_script,
            "cleanup": plan.profile.command_profile.cleanup_script,
        },
        "started_at": _timestamp(started_at),
        "ended_at": _timestamp(ended_at),
        "duration_seconds": ended_at - started_at,
        "result": result,
        "failure_code": failure_code,
        "assertions": sorted(set(assertions)),
        "restoration": restoration,
        "cleanup": cleanup,
        "artifact_exception_id": plan.request.evidence_exception_id or "",
        "retained_evidence": _artifact_inventory(retained_evidence),
        "certification_scope": actual_scope,
        "limitations": limitations,
    }
    validate_evidence_packet(
        packet,
        evidence_contract,
        raw_identifier=selected._raw_identifier,
    )
    return packet


def _walk_keys(value: object) -> Sequence[str]:
    keys: list[str] = []
    if isinstance(value, Mapping):
        for key, nested in value.items():
            keys.append(str(key))
            keys.extend(_walk_keys(nested))
    elif isinstance(value, list):
        for nested in value:
            keys.extend(_walk_keys(nested))
    return keys


def validate_evidence_packet(
    packet: Mapping[str, object],
    evidence_contract: Mapping[str, object],
    *,
    raw_identifier: str,
) -> None:
    required = set(evidence_contract["required_fields"])
    allowed = set(evidence_contract["allowed_fields"])
    require(set(packet) == required == allowed, "evidence_policy_failed")
    forbidden_fields = {
        str(value).casefold() for value in evidence_contract["forbidden_fields"]
    }
    for key in _walk_keys(packet):
        require(key.casefold() not in forbidden_fields, "evidence_policy_failed")
    serialized = json.dumps(packet, sort_keys=True, separators=(",", ":"))
    require(
        len(serialized.encode("utf-8"))
        <= int(evidence_contract["maximum_packet_bytes"]),
        "evidence_policy_failed",
    )
    require(raw_identifier not in serialized, "evidence_policy_failed")
    for pattern in evidence_contract["forbidden_value_patterns"]:
        require(re.search(str(pattern), serialized) is None, "evidence_policy_failed")
    require(
        re.fullmatch(
            str(evidence_contract["identity_hash_regex"]),
            str(packet["device_identity_hash"]),
        )
        is not None,
        "evidence_policy_failed",
    )
    assertions = packet["assertions"]
    require(
        isinstance(assertions, list)
        and len(assertions) <= int(evidence_contract["maximum_assertions"]),
        "evidence_policy_failed",
    )
    retained = packet["retained_evidence"]
    require(
        isinstance(retained, list)
        and len(retained) <= int(evidence_contract["maximum_retained_files"]),
        "evidence_policy_failed",
    )


def evidence_id(packet: Mapping[str, object]) -> str:
    return hashlib.sha256(
        json.dumps(packet, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def execute_device_plan(
    *,
    plan: DevicePlan,
    records: Sequence[DeviceRecord],
    lock_adapter: DeviceLockAdapter,
    runtime: DeviceRuntime,
    evidence_contract: Mapping[str, object],
    now: Callable[[], int],
    synthetic_authorized: bool = False,
) -> DeviceResult:
    """Execute the reviewed lifecycle through injected synthetic/test adapters."""

    require(
        plan.execution_authorized or synthetic_authorized,
        "authorization_rejected",
    )
    selected = select_device(plan, records)
    started_at = now()
    receipt = lock_adapter.acquire(plan=plan, selected=selected, now=started_at)
    if not receipt.accepted:
        packet = build_evidence_packet(
            plan=plan,
            selected=selected,
            lock_receipt=receipt,
            release_receipt=None,
            evidence_contract=evidence_contract,
            started_at=started_at,
            ended_at=started_at,
            result="failure",
            failure_code="lock_collision",
            assertions=("exact-source-verified",),
            restoration="not-started",
            cleanup="not-started",
        )
        return DeviceResult(
            request_id=plan.request.request_id,
            evidence_id=evidence_id(packet),
            result="failure",
            failure_code="lock_collision",
            cleanup_result="not-started",
            artifact_exception_used=False,
            selected_device_hash=selected.identity_hash,
            evidence_packet=packet,
        )

    snapshot: Mapping[str, object] = {}
    assertions: list[str] = [
        "authorization-validated",
        "exact-source-verified",
        "physical-family-verified",
        "resource-lock-acquired",
    ]
    primary_failure = ""
    restoration = "not-started"
    cleanup = "not-started"
    release_receipt: LockReleaseReceipt | None = None
    try:
        snapshot = runtime.snapshot(selected)
        runtime.prepare(plan, selected)
        assertions.append("prepare-profile-completed")
        assertions.extend(runtime.test(plan, selected))
        if not runtime.connected(selected):
            raise DeviceValidationError("device_disconnected")
        assertions.extend(runtime.collect_evidence(plan, selected))
    except DeviceValidationError as error:
        primary_failure = error.code
    except Exception:
        primary_failure = "stage_failed"
    finally:
        try:
            runtime.restore(selected, snapshot)
            restoration = "success"
            assertions.append("captured-state-restored")
        except Exception:
            restoration = "failure"
            if not primary_failure:
                primary_failure = "device_restoration_failed"
        try:
            runtime.cleanup(plan, selected)
            residue = tuple(runtime.residue(plan, selected))
            require(not residue, "cleanup_failed")
            cleanup = "success"
            assertions.append("zero-device-and-runner-residue")
        except Exception:
            cleanup = "failure"
            if not primary_failure:
                primary_failure = "cleanup_failed"
        try:
            release_receipt = lock_adapter.release(receipt, now=now())
            assertions.append("resource-lock-released")
        except Exception:
            if not primary_failure:
                primary_failure = "lock_release_failed"

    ended_at = now()
    result = "failure" if primary_failure else "success"
    packet = build_evidence_packet(
        plan=plan,
        selected=selected,
        lock_receipt=receipt,
        release_receipt=release_receipt,
        evidence_contract=evidence_contract,
        started_at=started_at,
        ended_at=ended_at,
        result=result,
        failure_code=primary_failure,
        assertions=assertions,
        restoration=restoration,
        cleanup=cleanup,
    )
    return DeviceResult(
        request_id=plan.request.request_id,
        evidence_id=evidence_id(packet),
        result=result,
        failure_code=primary_failure,
        cleanup_result=cleanup,
        artifact_exception_used=False,
        selected_device_hash=selected.identity_hash,
        evidence_packet=packet,
    )


def _lstat(path: Path) -> os.stat_result | None:
    try:
        return os.lstat(path)
    except FileNotFoundError:
        return None
    except OSError as error:
        raise DeviceValidationError("cleanup_failed") from error


def remove_no_follow(path: Path) -> None:
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
                remove_no_follow(child)
            os.rmdir(path)
        else:
            raise DeviceValidationError("cleanup_failed")
    except DeviceValidationError:
        raise
    except OSError as error:
        raise DeviceValidationError("cleanup_failed") from error
    require(_lstat(path) is None, "cleanup_failed")


def registered_state_paths(state_root: Path) -> tuple[Path, ...]:
    root = Path(os.path.abspath(state_root))
    metadata = _lstat(root)
    if metadata is not None:
        require(
            stat.S_ISDIR(metadata.st_mode) and not stat.S_ISLNK(metadata.st_mode),
            "cleanup_failed",
        )
    return (
        root / "device-validation",
        root / "device-evidence",
        root / "device-credentials",
        root / "device-results",
    )


def cleanup_device_state(state_root: Path) -> None:
    for path in registered_state_paths(state_root):
        remove_no_follow(path)


def assert_zero_device_residue(state_root: Path) -> None:
    remaining = [
        str(path)
        for path in registered_state_paths(state_root)
        if _lstat(path) is not None
    ]
    require(not remaining, "cleanup_failed")
