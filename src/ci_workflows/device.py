"""Public façade for reusable physical-device validation."""
from __future__ import annotations

from pathlib import Path
from typing import Mapping, Sequence

from .device_contract import (
    build_plan,
    load_device_contract,
    load_evidence_contract,
    request_from_environment,
    source_trust_from_environment,
)
from .device_execution import (
    DeviceLockAdapter,
    DeviceRuntime,
    InMemoryDeviceLockAdapter,
    SyntheticDeviceRuntime,
    assert_zero_device_residue,
    cleanup_device_state,
    execute_device_plan,
    parse_android_inventory,
    parse_apple_inventory,
    parse_inventory,
    select_device,
    stable_identity_hash,
    validate_evidence_packet,
)
from .device_types import (
    DeviceFamily,
    DevicePlan,
    DeviceProfile,
    DeviceRecord,
    DeviceRequest,
    DeviceResult,
    DeviceValidationError,
    LockReceipt,
    SelectedDevice,
)

__all__ = [
    "DeviceFamily",
    "DeviceLockAdapter",
    "DevicePlan",
    "DeviceProfile",
    "DeviceRecord",
    "DeviceRequest",
    "DeviceResult",
    "DeviceRuntime",
    "DeviceValidationError",
    "InMemoryDeviceLockAdapter",
    "LockReceipt",
    "SelectedDevice",
    "SyntheticDeviceRuntime",
    "assert_zero_device_residue",
    "build_plan",
    "cleanup_device_state",
    "execute_device_plan",
    "load_device_contract",
    "load_evidence_contract",
    "parse_android_inventory",
    "parse_apple_inventory",
    "parse_inventory",
    "plan_from_environment",
    "request_from_environment",
    "select_device",
    "source_trust_from_environment",
    "stable_identity_hash",
    "synthetic_validate",
    "validate_evidence_packet",
]


def plan_from_environment(
    contract_root: Path,
    environment: Mapping[str, str],
) -> DevicePlan:
    contract = load_device_contract(contract_root)
    request = request_from_environment(environment, contract)
    return build_plan(contract, request)


def synthetic_validate(
    *,
    contract_root: Path,
    environment: Mapping[str, str],
    inventory_text: str,
    now_values: Sequence[int] = (1000, 1001, 1002),
) -> DeviceResult:
    contract = load_device_contract(contract_root)
    evidence = load_evidence_contract(contract_root)
    request = request_from_environment(environment, contract)
    plan = build_plan(contract, request)
    records = parse_inventory(request.family, inventory_text)
    values = iter(now_values)
    return execute_device_plan(
        plan=plan,
        records=records,
        lock_adapter=InMemoryDeviceLockAdapter(),
        runtime=SyntheticDeviceRuntime(),
        evidence_contract=evidence,
        now=lambda: next(values),
        synthetic_authorized=True,
    )
