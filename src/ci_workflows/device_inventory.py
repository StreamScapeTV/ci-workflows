"""Bounded synthetic inventory parsing and opaque device selection."""
from __future__ import annotations

import hashlib
import json
import re
from typing import Mapping, Sequence

from .device_contract_common import require, version_tuple
from .device_types import DeviceFamily, DevicePlan, DeviceRecord, DeviceValidationError, SelectedDevice

ANDROID_LINE = re.compile(
    r"^(?P<identifier>[A-Za-z0-9][A-Za-z0-9._:-]{2,127})\t"
    r"(?P<state>online|offline|unauthorized)\t"
    r"model=(?P<model>[a-z][a-z0-9-]{1,63})\t"
    r"api=(?P<api>[1-9][0-9]{0,2})\t"
    r"connection=(?P<connection>usb|network)\t"
    r"capabilities=(?P<capabilities>[a-z0-9-]+(?:,[a-z0-9-]+)*)\t"
    r"personal=(?P<personal>true|false)\t"
    r"conflict=(?P<conflict>true|false)$"
)
IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{2,127}$")
MODEL = re.compile(r"^[a-z][a-z0-9-]{1,63}$")
CAPABILITY = re.compile(r"^[a-z][a-z0-9-]{1,63}$")

def _boolean(value: object) -> bool:
    if value is True:
        return True
    if value is False:
        return False
    raise DeviceValidationError("device_inventory_malformed")

def parse_android_inventory(raw: str) -> tuple[DeviceRecord, ...]:
    require(isinstance(raw, str) and len(raw.encode("utf-8")) <= 65536, "device_inventory_malformed")
    records: list[DeviceRecord] = []
    seen: set[str] = set()
    for line in raw.splitlines():
        if not line.strip():
            continue
        match = ANDROID_LINE.fullmatch(line)
        require(match is not None, "device_inventory_malformed")
        values = match.groupdict()
        identifier = values["identifier"]
        require(identifier not in seen, "device_inventory_malformed")
        seen.add(identifier)
        records.append(
            DeviceRecord(
                raw_identifier=identifier,
                family=DeviceFamily.ANDROID,
                state=values["state"],
                connection=values["connection"],
                model=values["model"],
                capabilities=tuple(sorted(set(values["capabilities"].split(",")))),
                api_level=int(values["api"]),
                personal=values["personal"] == "true",
                conflicting=values["conflict"] == "true",
            )
        )
    require(0 < len(records) <= 64, "device_inventory_malformed")
    return tuple(records)

def parse_apple_inventory(raw: str, family: DeviceFamily) -> tuple[DeviceRecord, ...]:
    require(family in {DeviceFamily.IOS, DeviceFamily.TVOS}, "unsupported_family")
    require(isinstance(raw, str) and len(raw.encode("utf-8")) <= 131072, "device_inventory_malformed")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as error:
        raise DeviceValidationError("device_inventory_malformed") from error
    require(isinstance(payload, Mapping) and set(payload) == {"devices"}, "device_inventory_malformed")
    rows = payload["devices"]
    require(isinstance(rows, list) and 0 < len(rows) <= 64, "device_inventory_malformed")
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
    records: list[DeviceRecord] = []
    seen: set[str] = set()
    for row in rows:
        require(isinstance(row, Mapping) and set(row) == expected_keys, "device_inventory_malformed")
        identifier = row["identifier"]
        capabilities = row["capabilities"]
        require(
            isinstance(identifier, str)
            and IDENTIFIER.fullmatch(identifier) is not None
            and identifier not in seen,
            "device_inventory_malformed",
        )
        seen.add(identifier)
        require(row["family"] == family.value, "device_family_mismatch")
        require(row["state"] in {"online", "offline", "unauthorized"}, "device_inventory_malformed")
        require(row["connection"] in {"usb", "paired", "network"}, "device_inventory_malformed")
        require(isinstance(row["model"], str) and MODEL.fullmatch(row["model"]) is not None, "device_inventory_malformed")
        require(
            isinstance(capabilities, list)
            and capabilities
            and len(capabilities) == len(set(capabilities))
            and all(isinstance(value, str) and CAPABILITY.fullmatch(value) is not None for value in capabilities),
            "device_inventory_malformed",
        )
        os_version = row["os_version"]
        require(isinstance(os_version, str), "device_inventory_malformed")
        version_tuple(os_version)
        records.append(
            DeviceRecord(
                raw_identifier=identifier,
                family=family,
                state=str(row["state"]),
                connection=str(row["connection"]),
                model=str(row["model"]),
                capabilities=tuple(sorted(capabilities)),
                os_version=os_version,
                personal=_boolean(row["personal"]),
                conflicting=_boolean(row["conflict"]),
            )
        )
    return tuple(records)

def parse_inventory(family: DeviceFamily, raw: str) -> tuple[DeviceRecord, ...]:
    if family is DeviceFamily.ANDROID:
        return parse_android_inventory(raw)
    return parse_apple_inventory(raw, family)

def stable_identity_hash(profile_id: str, family: DeviceFamily, raw_identifier: str) -> str:
    return hashlib.sha256(
        f"{profile_id}\0{family.value}\0{raw_identifier}".encode("utf-8")
    ).hexdigest()

def _version_allowed(plan: DevicePlan, record: DeviceRecord) -> bool:
    policy = plan.profile.version_policy
    if record.family is DeviceFamily.ANDROID:
        return record.api_level is not None and int(policy["api_min"]) <= record.api_level <= int(policy["api_max"])
    return record.os_version is not None and version_tuple(str(policy["os_min"])) <= version_tuple(record.os_version) <= version_tuple(str(policy["os_max"]))

def _record_allowed(plan: DevicePlan, record: DeviceRecord) -> bool:
    return (
        record.family is plan.request.family
        and record.state in plan.profile.health_states
        and record.connection in plan.profile.connection_states
        and not record.personal
        and not record.conflicting
        and record.model in plan.profile.models
        and plan.request.capability in record.capabilities
        and _version_allowed(plan, record)
    )

def select_device(plan: DevicePlan, records: Sequence[DeviceRecord]) -> SelectedDevice:
    require(0 < len(records) <= 64, "device_inventory_malformed")
    candidates = [record for record in records if _record_allowed(plan, record)]
    if not candidates:
        if any(record.family is plan.request.family and record.state == "offline" for record in records):
            raise DeviceValidationError("device_offline")
        raise DeviceValidationError("device_no_match")
    decorated = sorted(
        (
            stable_identity_hash(plan.profile.profile_id, record.family, record.raw_identifier),
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
