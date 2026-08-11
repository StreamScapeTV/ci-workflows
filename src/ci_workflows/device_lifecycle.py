"""Restoration-first synthetic device lifecycle orchestration."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Callable, Mapping, Protocol, Sequence

from .device_contract_common import require
from .device_evidence import build_evidence_packet, evidence_id
from .device_inventory import select_device
from .device_test_lock import DeviceLockAdapter
from .device_types import DevicePlan, DeviceRecord, DeviceResult, DeviceValidationError, SelectedDevice

class DeviceRuntime(Protocol):
    def snapshot(self, selected: SelectedDevice) -> Mapping[str, object]: ...
    def prepare(self, plan: DevicePlan, selected: SelectedDevice) -> None: ...
    def test(self, plan: DevicePlan, selected: SelectedDevice) -> Sequence[str]: ...
    def collect_evidence(self, plan: DevicePlan, selected: SelectedDevice) -> Sequence[str]: ...
    def connected(self, selected: SelectedDevice) -> bool: ...
    def restore(self, selected: SelectedDevice, snapshot: Mapping[str, object]) -> None: ...
    def cleanup(self, plan: DevicePlan, selected: SelectedDevice) -> None: ...
    def residue(self, plan: DevicePlan, selected: SelectedDevice) -> Sequence[str]: ...

class SyntheticDeviceRuntime:
    fail_stage: str | None = None
    disconnect_after_test: bool = False
    restored: bool = False
    cleaned: bool = False

    def _maybe_fail(self, stage: str, code: str) -> None:
        if self.fail_stage == stage:
            raise DeviceValidationError(code)

    def snapshot(self, selected: SelectedDevice) -> Mapping[str, object]:
        self._maybe_fail("snapshot", "stage_failed")
        return {"state_hash": hashlib.sha256(selected.identity_hash.encode()).hexdigest()}

    def prepare(self, plan: DevicePlan, selected: SelectedDevice) -> None:
        self._maybe_fail("prepare", "prepare_failed")

    def test(self, plan: DevicePlan, selected: SelectedDevice) -> Sequence[str]:
        self._maybe_fail("test", "stage_failed")
        return ("reviewed-synthetic-test-profile-executed",)

    def collect_evidence(self, plan: DevicePlan, selected: SelectedDevice) -> Sequence[str]:
        self._maybe_fail("evidence", "evidence_policy_failed")
        return ("device-health-verified", "exact-source-verified")

    def connected(self, selected: SelectedDevice) -> bool:
        return not self.disconnect_after_test

    def restore(self, selected: SelectedDevice, snapshot: Mapping[str, object]) -> None:
        self._maybe_fail("restore", "stage_failed")
        self.restored = True

    def cleanup(self, plan: DevicePlan, selected: SelectedDevice) -> None:
        self._maybe_fail("cleanup", "cleanup_failed")
        self.cleaned = True

    def residue(self, plan: DevicePlan, selected: SelectedDevice) -> Sequence[str]:
        return ("owned-residue",) if self.fail_stage == "residue" else ()

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
    require(plan.execution_authorized or synthetic_authorized, "physical_authorization_required")
    require(plan.profile.synthetic_only or not synthetic_authorized, "authorization_rejected")
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
        return DeviceResult(plan.request.request_id, evidence_id(packet), "failure", "lock_collision", "not-started", False, selected.identity_hash, packet)

    snapshot: Mapping[str, object] = {}
    assertions = ["authorization-validated", "exact-source-verified", "physical-family-verified", "synthetic-process-lock-acquired"]
    primary_failure = ""
    cleanup_failures: list[str] = []
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
            cleanup_failures.append("restoration")
            if not primary_failure:
                primary_failure = "device_restoration_failed"
        try:
            runtime.cleanup(plan, selected)
            require(not tuple(runtime.residue(plan, selected)), "cleanup_failed")
            cleanup = "success"
            assertions.append("zero-device-and-runner-residue")
        except Exception:
            cleanup = "failure"
            cleanup_failures.append("cleanup")
            if not primary_failure:
                primary_failure = "cleanup_failed"
        try:
            release_receipt = lock_adapter.release(receipt, now=now())
            assertions.append("synthetic-process-lock-released")
        except Exception:
            cleanup_failures.append("lock-release")
            if not primary_failure:
                primary_failure = "lock_release_failed"
    if cleanup_failures:
        assertions.append("cleanup-failures:" + ",".join(cleanup_failures))
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
    return DeviceResult(plan.request.request_id, evidence_id(packet), result, primary_failure, cleanup, False, selected.identity_hash, packet)
