"""Single-process test-only device lock adapter interfaces."""
from __future__ import annotations

import hashlib
from typing import Protocol

from .device_types import DevicePlan, DeviceValidationError, LockReceipt, LockReleaseReceipt, SelectedDevice

class DeviceLockAdapter(Protocol):
    def acquire(self, *, plan: DevicePlan, selected: SelectedDevice, now: int) -> LockReceipt: ...
    def release(self, receipt: LockReceipt, *, now: int) -> LockReleaseReceipt: ...

class InMemoryDeviceLockAdapter:
    """Single-process test double only; it is not cross-run fencing authority."""

    def __init__(self) -> None:
        self._leases: dict[str, LockReceipt] = {}
        self._epochs: dict[str, int] = {}

    @staticmethod
    def resource_key(plan: DevicePlan, selected: SelectedDevice) -> str:
        return f"synthetic:{selected.family.value}:{plan.profile.profile_id}:{selected.identity_hash}"

    @staticmethod
    def owner_hash(plan: DevicePlan) -> str:
        return hashlib.sha256(
            f"{plan.request.request_id}\0{plan.request.run_id}".encode("utf-8")
        ).hexdigest()

    def acquire(self, *, plan: DevicePlan, selected: SelectedDevice, now: int) -> LockReceipt:
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
                next_actor="current-test-owner",
                next_action="wait-for-test-release",
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
            next_actor="synthetic-device-test",
            next_action="execute-synthetic-profile",
        )
        self._leases[resource_key] = receipt
        return receipt

    def release(self, receipt: LockReceipt, *, now: int) -> LockReleaseReceipt:
        current = self._leases.get(receipt.resource_key)
        if (
            current is None
            or current.epoch != receipt.epoch
            or current.token != receipt.token
            or current.request_id != receipt.request_id
        ):
            raise DeviceValidationError("lock_stale_epoch")
        del self._leases[receipt.resource_key]
        digest = hashlib.sha256(
            f"{receipt.resource_key}\0{receipt.request_id}\0{receipt.epoch}\0{now}\0released".encode("utf-8")
        ).hexdigest()
        return LockReleaseReceipt(True, receipt.resource_key, receipt.request_id, receipt.epoch, digest)

    def active_count(self) -> int:
        return len(self._leases)

