from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from ci_workflows.device_lock import (
    DeviceLockError,
    DeviceLockOwner,
    DeviceLockReceipt,
    DeviceLockRequest,
    PosixDeviceLockBackend,
    load_device_lock_contract,
)

ROOT = Path(__file__).resolve().parents[1]


class ExactDeviceFencingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.contract = load_device_lock_contract(ROOT)

    @staticmethod
    def root(directory: str) -> Path:
        path = Path(directory) / "lock-root"
        path.mkdir(mode=0o700)
        return path

    @staticmethod
    def owner(repository: str, run_id: str) -> DeviceLockOwner:
        return DeviceLockOwner.from_environment(
            {
                "GITHUB_REPOSITORY": repository,
                "GITHUB_RUN_ID": run_id,
                "GITHUB_RUN_ATTEMPT": "1",
            }
        )

    def request(
        self,
        *,
        owner: DeviceLockOwner,
        capability: str = "instrumentation",
        device_hash: str = "a" * 64,
        source_sha: str = "b" * 40,
        authorization: str = "issue-136-owner-authorized-android",
        request_id: str = "issue-136-exact-device",
        lease_seconds: int = 300,
    ) -> DeviceLockRequest:
        return DeviceLockRequest.create(
            self.contract,
            device_family="android",
            device_capability=capability,
            device_identity_hash=device_hash,
            tested_source_sha=source_sha,
            authorization_receipt=authorization,
            owner=owner,
            request_id=request_id,
            lease_seconds=lease_seconds,
        )

    @staticmethod
    def environment(root: Path, owner: DeviceLockOwner) -> dict[str, str]:
        return {
            "CIW_DEVICE_LOCK_ROOT": str(root),
            "GITHUB_REPOSITORY": owner.repository,
            "GITHUB_RUN_ID": owner.run_id,
            "GITHUB_RUN_ATTEMPT": owner.run_attempt,
        }

    def backend(
        self,
        root: Path,
        owner: DeviceLockOwner,
        *,
        clock: list[int],
        token: str,
    ) -> PosixDeviceLockBackend:
        return PosixDeviceLockBackend(
            contract_root=ROOT,
            environment=self.environment(root, owner),
            now=lambda: clock[0],
            token_factory=lambda: token,
        )

    def test_capability_is_receipt_metadata_not_a_lock_partition(self) -> None:
        first_owner = self.owner("StreamScapeTV/iptv-android", "1001")
        second_owner = self.owner("StreamScapeTV/streamscape-media", "2002")
        first = self.request(owner=first_owner, capability="instrumentation")
        second = self.request(
            owner=second_owner,
            capability="media3",
            request_id="issue-136-media-capability",
        )
        self.assertEqual(first.resource_key_hash, second.resource_key_hash)
        self.assertNotEqual(first.device_capability, second.device_capability)

        with tempfile.TemporaryDirectory() as directory:
            root = self.root(directory)
            clock = [1000]
            first_backend = self.backend(root, first_owner, clock=clock, token="1" * 64)
            second_backend = self.backend(root, second_owner, clock=clock, token="2" * 64)
            receipt = first_backend.acquire(first)
            self.assertEqual("instrumentation", receipt.device_capability)
            with self.assertRaisesRegex(DeviceLockError, "lock_held"):
                second_backend.acquire(second)
            first_backend.release(receipt.encode(), first)

    def test_different_exact_devices_can_be_locked_independently(self) -> None:
        first_owner = self.owner("StreamScapeTV/iptv-android", "1001")
        second_owner = self.owner("StreamScapeTV/streamscape-media", "2002")
        first = self.request(owner=first_owner, device_hash="a" * 64)
        second = self.request(
            owner=second_owner,
            device_hash="c" * 64,
            request_id="issue-136-second-device",
        )
        self.assertNotEqual(first.resource_key_hash, second.resource_key_hash)

        with tempfile.TemporaryDirectory() as directory:
            root = self.root(directory)
            clock = [1000]
            first_backend = self.backend(root, first_owner, clock=clock, token="1" * 64)
            second_backend = self.backend(root, second_owner, clock=clock, token="2" * 64)
            first_receipt = first_backend.acquire(first)
            second_receipt = second_backend.acquire(second)
            self.assertEqual(first_receipt, first_backend.verify(first_receipt.encode(), first))
            self.assertEqual(second_receipt, second_backend.verify(second_receipt.encode(), second))
            first_backend.release(first_receipt.encode(), first)
            second_backend.release(second_receipt.encode(), second)

    def test_lease_duration_is_part_of_the_exact_request_match(self) -> None:
        owner = self.owner("StreamScapeTV/iptv-android", "1001")
        request = self.request(owner=owner, lease_seconds=300)
        changed = replace(request, lease_seconds=600)
        with tempfile.TemporaryDirectory() as directory:
            root = self.root(directory)
            clock = [1000]
            backend = self.backend(root, owner, clock=clock, token="1" * 64)
            receipt = backend.acquire(request)
            with self.assertRaisesRegex(DeviceLockError, "lock_held"):
                backend.acquire(changed)
            with self.assertRaisesRegex(DeviceLockError, "receipt_mismatch"):
                backend.verify(receipt.encode(), changed)
            with self.assertRaisesRegex(DeviceLockError, "receipt_mismatch"):
                backend.release(receipt.encode(), changed)
            backend.release(receipt.encode(), request)

    def test_decoded_receipt_rejects_out_of_contract_lease_duration(self) -> None:
        owner = self.owner("StreamScapeTV/iptv-android", "1001")
        request = self.request(owner=owner)
        with tempfile.TemporaryDirectory() as directory:
            root = self.root(directory)
            clock = [1000]
            backend = self.backend(root, owner, clock=clock, token="1" * 64)
            receipt = backend.acquire(request)
            invalid = replace(
                receipt,
                expires_at_epoch=(
                    receipt.acquired_at_epoch
                    + self.contract.maximum_lease_seconds
                    + 1
                ),
            )
            with self.assertRaisesRegex(DeviceLockError, "receipt_invalid"):
                DeviceLockReceipt.decode(self.contract, invalid.encode())
            backend.release(receipt.encode(), request)

    def test_old_receipt_cannot_release_or_verify_after_newer_release(self) -> None:
        first_owner = self.owner("StreamScapeTV/iptv-android", "1001")
        second_owner = self.owner("StreamScapeTV/streamscape-media", "2002")
        first = self.request(owner=first_owner)
        second = self.request(
            owner=second_owner,
            request_id="issue-136-newer-holder",
        )
        with tempfile.TemporaryDirectory() as directory:
            root = self.root(directory)
            clock = [1000]
            first_backend = self.backend(root, first_owner, clock=clock, token="1" * 64)
            old = first_backend.acquire(first)
            clock[0] = old.expires_at_epoch
            second_backend = self.backend(root, second_owner, clock=clock, token="2" * 64)
            newer = second_backend.acquire(second)
            second_backend.release(newer.encode(), second)

            with self.assertRaisesRegex(DeviceLockError, "lock_not_current"):
                first_backend.verify(old.encode(), first)
            with self.assertRaisesRegex(DeviceLockError, "release_state_mismatch"):
                first_backend.release(old.encode(), first)
            second_backend.assert_released(newer.encode(), second)


if __name__ == "__main__":
    unittest.main()
