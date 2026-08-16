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
    validate_typed_plan,
)
from .device_execution import (
    DeviceLockAdapter,
    DeviceRuntime,
    InMemoryDeviceLockAdapter,
    SyntheticDeviceRuntime,
    assert_zero_device_residue,
    cleanup_checkout_path,
    cleanup_device_state,
    cleanup_live_device,
    discover_live_device,
    execute_device_plan,
    execute_live_device,
    load_selected_device,
    parse_android_inventory,
    parse_apple_inventory,
    parse_inventory,
    select_device,
    stable_identity_hash,
    validate_authorization_receipt,
    validate_evidence_packet,
    validate_exact_checkout,
    verify_production_lock,
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
    name
    for name in globals()
    if name.startswith("Device")
    or name
    in {
        "InMemoryDeviceLockAdapter",
        "SyntheticDeviceRuntime",
        "LockReceipt",
        "SelectedDevice",
        "build_plan",
        "load_device_contract",
        "load_evidence_contract",
        "request_from_environment",
        "source_trust_from_environment",
        "validate_typed_plan",
        "assert_zero_device_residue",
        "cleanup_checkout_path",
        "cleanup_device_state",
        "cleanup_live_device",
        "discover_live_device",
        "execute_device_plan",
        "execute_live_device",
        "load_selected_device",
        "parse_android_inventory",
        "parse_apple_inventory",
        "parse_inventory",
        "select_device",
        "stable_identity_hash",
        "validate_authorization_receipt",
        "validate_evidence_packet",
        "validate_exact_checkout",
        "verify_production_lock",
        "plan_from_environment",
        "synthetic_validate",
    }
]


def plan_from_environment(
    contract_root: Path, environment: Mapping[str, str]
) -> DevicePlan:
    contract = load_device_contract(contract_root)
    return build_plan(contract, request_from_environment(environment, contract))


def synthetic_validate(
    *,
    contract_root: Path,
    environment: Mapping[str, str],
    inventory_text: str,
    now_values: Sequence[int] = (1000, 1001, 1002),
) -> DeviceResult:
    contract = load_device_contract(contract_root)
    evidence = load_evidence_contract(contract_root)
    plan = build_plan(contract, request_from_environment(environment, contract))
    records = parse_inventory(plan.request.family, inventory_text)
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
