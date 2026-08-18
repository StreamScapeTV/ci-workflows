"""Production physical-device discovery and bounded execution helpers."""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Mapping, Sequence

from .device_contract_common import require, safe_relative
from .device_inventory import select_device
from .device_types import (
    DeviceFamily,
    DevicePlan,
    DeviceRecord,
    DeviceValidationError,
    SelectedDevice,
    canonical_json,
)
from .device_lock import (
    DeviceLockError,
    PosixDeviceLockBackend,
    request_from_environment as lock_request_from_environment,
)

_AUTH_FIELDS = {
    "packet_version",
    "repository",
    "source_sha",
    "device_family",
    "device_capability",
    "request_id",
    "not_after_epoch",
}
_VERSION = re.compile(r"^[0-9]+(?:\.[0-9]+){0,3}$")
_PRIVATE_DIRECTORY = "device-validation"
_SELECTED_STATE = "selected-device-private.json"


def validate_authorization_receipt(
    raw: str,
    *,
    plan: DevicePlan,
    now_epoch: int | None = None,
) -> str:
    """Validate one owner-provisioned exact-request authorization receipt."""

    require(
        isinstance(raw, str) and 1 <= len(raw.encode("utf-8")) <= 4096,
        "authorization_rejected",
    )
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as error:
        raise DeviceValidationError("authorization_rejected") from error
    require(
        isinstance(payload, Mapping) and set(payload) == _AUTH_FIELDS,
        "authorization_rejected",
    )
    require(canonical_json(payload) == raw, "authorization_rejected")
    require(payload["packet_version"] == "device-authorization/1", "authorization_rejected")
    require(payload["repository"] == plan.request.repository, "authorization_rejected")
    require(payload["source_sha"] == plan.request.admitted_sha, "authorization_rejected")
    require(payload["device_family"] == plan.request.family.value, "authorization_rejected")
    require(payload["device_capability"] == plan.request.capability, "authorization_rejected")
    require(payload["request_id"] == plan.request.request_id, "authorization_rejected")
    expires = payload["not_after_epoch"]
    require(type(expires) is int and 0 < expires <= 2**63 - 1, "authorization_rejected")
    current = int(time.time()) if now_epoch is None else now_epoch
    require(0 <= current <= expires, "authorization_rejected")
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _private_command(
    argv: Sequence[str],
    *,
    cwd: Path,
    environment: Mapping[str, str],
    timeout_seconds: int = 30,
    failure_code: str = "device_inventory_malformed",
) -> str:
    """Run a command while intentionally retaining no stdout/stderr in CI logs."""

    require(
        bool(argv) and all(isinstance(item, str) and item for item in argv),
        "invalid_input",
    )
    try:
        completed = subprocess.run(
            list(argv),
            cwd=cwd,
            env=dict(environment),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout_seconds,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise DeviceValidationError(failure_code) from error
    if completed.returncode != 0:
        raise DeviceValidationError(failure_code)
    return completed.stdout or ""


def _android_record(
    plan: DevicePlan,
    *,
    cwd: Path,
    environment: Mapping[str, str],
) -> DeviceRecord:
    output = _private_command(["adb", "devices"], cwd=cwd, environment=environment)
    rows: list[tuple[str, str]] = []
    for raw in output.splitlines()[1:]:
        fields = raw.strip().split()
        if len(fields) >= 2:
            rows.append((fields[0], fields[1]))
    ready = [identifier for identifier, state in rows if state == "device"]
    if not ready:
        if any(state in {"offline", "unauthorized"} for _, state in rows):
            raise DeviceValidationError("device_offline")
        raise DeviceValidationError("device_no_match")
    require(len(ready) == 1, "device_multiple_matches")
    identifier = ready[0]

    def prop(name: str) -> str:
        return _private_command(
            ["adb", "-s", identifier, "shell", "getprop", name],
            cwd=cwd,
            environment=environment,
        ).strip().replace("\r", "")

    sdk = prop("ro.build.version.sdk")
    require(re.fullmatch(r"[1-9][0-9]{0,2}", sdk) is not None, "device_inventory_malformed")
    characteristics = prop("ro.build.characteristics").casefold()
    if "tv" in characteristics or "television" in characteristics:
        model = "television"
    elif "tablet" in characteristics:
        model = "tablet"
    else:
        model = "phone"
    connection = "network" if ":" in identifier else "usb"
    return DeviceRecord(
        raw_identifier=identifier,
        family=DeviceFamily.ANDROID,
        state="online",
        connection=connection,
        model=model,
        capabilities=(plan.request.capability,),
        api_level=int(sdk),
    )


def _apple_record(
    plan: DevicePlan,
    *,
    cwd: Path,
    private_root: Path,
    environment: Mapping[str, str],
) -> DeviceRecord:
    inventory_path = private_root / "apple-devices-private.json"
    if inventory_path.exists() or inventory_path.is_symlink():
        inventory_path.unlink()
    _private_command(
        [
            "xcrun",
            "devicectl",
            "list",
            "devices",
            "--json-output",
            str(inventory_path),
        ],
        cwd=cwd,
        environment=environment,
        timeout_seconds=60,
    )
    require(inventory_path.is_file() and not inventory_path.is_symlink(), "device_inventory_malformed")
    try:
        payload = json.loads(inventory_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise DeviceValidationError("device_inventory_malformed") from error
    finally:
        if inventory_path.exists() and not inventory_path.is_symlink():
            inventory_path.unlink()
    rows = payload.get("result", {}).get("devices", []) if isinstance(payload, Mapping) else []
    require(isinstance(rows, list), "device_inventory_malformed")
    expected_platform = "iOS" if plan.request.family is DeviceFamily.IOS else "tvOS"
    candidates: list[Mapping[str, object]] = []
    for candidate in rows:
        if not isinstance(candidate, Mapping):
            continue
        hardware = candidate.get("hardwareProperties", {})
        properties = candidate.get("deviceProperties", {})
        connection = candidate.get("connectionProperties", {})
        if not (
            isinstance(hardware, Mapping)
            and isinstance(properties, Mapping)
            and isinstance(connection, Mapping)
        ):
            continue
        if hardware.get("platform") != expected_platform or hardware.get("reality") != "physical":
            continue
        if connection.get("pairingState") not in {None, "paired"}:
            continue
        if properties.get("developerModeStatus") == "disabled":
            continue
        candidates.append(candidate)
    if not candidates:
        raise DeviceValidationError("device_no_match")
    require(len(candidates) == 1, "device_multiple_matches")
    candidate = candidates[0]
    hardware = candidate.get("hardwareProperties", {})
    properties = candidate.get("deviceProperties", {})
    connection = candidate.get("connectionProperties", {})
    assert isinstance(hardware, Mapping) and isinstance(properties, Mapping) and isinstance(connection, Mapping)
    identifier = hardware.get("udid") or candidate.get("identifier")
    require(isinstance(identifier, str) and identifier, "device_inventory_malformed")
    version = (
        properties.get("osVersionNumber")
        or properties.get("osVersion")
        or properties.get("operatingSystemVersion")
    )
    require(isinstance(version, str), "device_inventory_malformed")
    version_match = re.search(r"[0-9]+(?:\.[0-9]+){0,3}", version)
    require(
        version_match is not None and _VERSION.fullmatch(version_match.group(0)) is not None,
        "device_inventory_malformed",
    )
    normalized_version = version_match.group(0)
    if plan.request.family is DeviceFamily.TVOS:
        model = "apple-tv"
    else:
        product_type = str(hardware.get("productType", "")).casefold()
        name = str(properties.get("name", "")).casefold()
        model = "ipad" if "ipad" in product_type or "ipad" in name else "iphone"
    transport = str(connection.get("transportType", "")).casefold()
    connection_class = "usb" if "usb" in transport else "paired"
    return DeviceRecord(
        raw_identifier=identifier,
        family=plan.request.family,
        state="online",
        connection=connection_class,
        model=model,
        capabilities=(plan.request.capability,),
        os_version=normalized_version,
    )


def _private_root(state_root: Path) -> Path:
    root = state_root / _PRIVATE_DIRECTORY
    require(not root.is_symlink(), "cleanup_failed")
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    return root


def discover_live_device(
    plan: DevicePlan,
    *,
    source_root: Path,
    state_root: Path,
    environment: Mapping[str, str],
) -> SelectedDevice:
    """Discover exactly one eligible physical device without logging its identity."""

    require(plan.execution_authorized and not plan.profile.synthetic_only, "physical_authorization_required")
    private_root = _private_root(state_root)
    record = (
        _android_record(plan, cwd=source_root, environment=environment)
        if plan.request.family is DeviceFamily.ANDROID
        else _apple_record(
            plan,
            cwd=source_root,
            private_root=private_root,
            environment=environment,
        )
    )
    selected = select_device(plan, (record,))
    private_state = private_root / _SELECTED_STATE
    payload = {
        "packet_version": "device-selected-private/1",
        "identity_hash": selected.identity_hash,
        "family": selected.family.value,
        "raw_identifier": selected._raw_identifier,
        "model_class": selected.model_class,
        "connection_class": selected.connection_class,
        "os_or_api": selected.os_or_api,
        "capabilities": list(selected.capabilities),
    }
    private_state.write_text(canonical_json(payload), encoding="utf-8")
    private_state.chmod(0o600)
    return selected


def load_selected_device(
    *,
    state_root: Path,
    plan: DevicePlan,
    expected_identity_hash: str,
) -> SelectedDevice:
    path = state_root / _PRIVATE_DIRECTORY / _SELECTED_STATE
    require(path.is_file() and not path.is_symlink(), "device_no_match")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise DeviceValidationError("device_inventory_malformed") from error
    required = {
        "packet_version",
        "identity_hash",
        "family",
        "raw_identifier",
        "model_class",
        "connection_class",
        "os_or_api",
        "capabilities",
    }
    require(isinstance(payload, Mapping) and set(payload) == required, "device_inventory_malformed")
    require(payload["packet_version"] == "device-selected-private/1", "device_inventory_malformed")
    require(payload["identity_hash"] == expected_identity_hash, "device_family_mismatch")
    require(payload["family"] == plan.request.family.value, "device_family_mismatch")
    capabilities = payload["capabilities"]
    require(
        isinstance(capabilities, list) and all(isinstance(item, str) for item in capabilities),
        "device_inventory_malformed",
    )
    return SelectedDevice(
        identity_hash=str(payload["identity_hash"]),
        family=plan.request.family,
        model_class=str(payload["model_class"]),
        connection_class=str(payload["connection_class"]),
        os_or_api=str(payload["os_or_api"]),
        capabilities=tuple(capabilities),
        _raw_identifier=str(payload["raw_identifier"]),
    )


def verify_production_lock(
    *,
    contract_root: Path,
    plan: DevicePlan,
    selected: SelectedDevice,
    authorization_receipt: str,
    resource_lock_receipt: str,
    environment: Mapping[str, str],
    minimum_remaining_seconds: int = 1,
) -> None:
    validate_authorization_receipt(authorization_receipt, plan=plan)
    require(resource_lock_receipt, "physical_authorization_required")
    lock_environment = dict(environment)
    lock_environment.update(
        {
            "CIW_LOCK_DEVICE_FAMILY": plan.request.family.value,
            "CIW_LOCK_DEVICE_CAPABILITY": plan.request.capability,
            "CIW_LOCK_DEVICE_IDENTITY_HASH": selected.identity_hash,
            "CIW_LOCK_TESTED_SOURCE_SHA": plan.request.admitted_sha,
            "CIW_LOCK_AUTHORIZATION_RECEIPT": authorization_receipt,
            "CIW_LOCK_REQUEST_ID": plan.request.request_id,
            "CIW_LOCK_LEASE_SECONDS": "18000",
            "CIW_LOCK_RESOURCE_RECEIPT": resource_lock_receipt,
        }
    )
    try:
        request, _owner = lock_request_from_environment(
            contract_root=contract_root,
            environment=lock_environment,
        )
        backend = PosixDeviceLockBackend(
            contract_root=contract_root,
            environment=lock_environment,
        )
        backend.verify(
            resource_lock_receipt,
            request,
            minimum_remaining_seconds=minimum_remaining_seconds,
        )
    except DeviceLockError as error:
        raise DeviceValidationError("physical_authorization_required") from error


def _bounded_script(source_root: Path, relative: str) -> Path:
    normalized = safe_relative(relative, "command_profile_rejected")
    target = source_root.joinpath(*Path(normalized).parts)
    current = source_root
    for part in Path(normalized).parts:
        current /= part
        require(not current.is_symlink(), "command_profile_rejected")
    require(target.is_file() and not target.is_symlink(), "command_profile_rejected")
    return target


def _run_product_stage(
    source_root: Path,
    script_path: str,
    *,
    args: Sequence[str],
    environment: Mapping[str, str],
    timeout_seconds: int,
    failure_code: str,
) -> None:
    script = _bounded_script(source_root, script_path)
    argv = [str(script), *args] if os.access(script, os.X_OK) else ["bash", str(script), *args]
    _private_command(
        argv,
        cwd=source_root,
        environment=environment,
        timeout_seconds=timeout_seconds,
        failure_code=failure_code,
    )
