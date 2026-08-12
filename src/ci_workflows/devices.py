"""Stable public component bridge for physical-device validation orchestration."""
from __future__ import annotations

from pathlib import Path
from typing import Callable, Mapping, Sequence

from .device_cleanup import assert_zero_device_residue, cleanup_device_state
from .device_lifecycle import DeviceRuntime, execute_device_plan
from .device_test_lock import DeviceLockAdapter
from .device_types import (
    DevicePlan,
    DeviceRecord,
    DeviceResult,
    LockReceipt,
    SelectedDevice,
)


def lock(
    *,
    adapter: DeviceLockAdapter,
    plan: DevicePlan,
    selected: SelectedDevice,
    now: int,
) -> LockReceipt:
    """Acquire the exact plan/device lock through the supplied bounded adapter."""

    return adapter.acquire(plan=plan, selected=selected, now=now)


def validate(
    *,
    plan: DevicePlan,
    records: Sequence[DeviceRecord],
    lock_adapter: DeviceLockAdapter,
    runtime: DeviceRuntime,
    evidence_contract: Mapping[str, object],
    now: Callable[[], int],
    synthetic_authorized: bool = False,
) -> DeviceResult:
    """Execute one typed device plan using the existing restoration-first lifecycle."""

    return execute_device_plan(
        plan=plan,
        records=records,
        lock_adapter=lock_adapter,
        runtime=runtime,
        evidence_contract=evidence_contract,
        now=now,
        synthetic_authorized=synthetic_authorized,
    )


def cleanup(*, state_root: Path) -> None:
    """Remove only registered device state and fail if any owned residue remains."""

    cleanup_device_state(state_root)
    assert_zero_device_residue(state_root)
