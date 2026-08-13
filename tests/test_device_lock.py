from __future__ import annotations

import hashlib
import json
import multiprocessing
import os
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
    locked_device_session,
    resolve_backend_root,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/device-lock/two-runs.json"


def _environment(root: Path, owner: dict[str, str]) -> dict[str, str]:
    return {
        "CIW_DEVICE_LOCK_ROOT": str(root),
        "GITHUB_REPOSITORY": owner["repository"],
        "GITHUB_RUN_ID": owner["run_id"],
        "GITHUB_RUN_ATTEMPT": owner["run_attempt"],
    }


def _owner(root: Path, owner: dict[str, str]) -> DeviceLockOwner:
    return DeviceLockOwner.from_environment(_environment(root, owner))


def _request(
    contract_root: Path,
    root: Path,
    fixture: dict[str, object],
    owner_payload: dict[str, str],
) -> DeviceLockRequest:
    contract = load_device_lock_contract(contract_root)
    return DeviceLockRequest.create(
        contract,
        device_family=str(fixture["device_family"]),
        device_capability=str(fixture["device_capability"]),
        device_identity_hash=str(fixture["device_identity_hash"]),
        tested_source_sha=str(fixture["tested_source_sha"]),
        authorization_receipt=str(fixture["authorization_receipt"]),
        owner=_owner(root, owner_payload),
        request_id=str(fixture["request_id"]),
        lease_seconds=int(fixture["lease_seconds"]),
    )


def _cross_run_worker(
    contract_root: str,
    lock_root: str,
    fixture: dict[str, object],
    owner_payload: dict[str, str],
    barrier,
    queue,
) -> None:
    root = Path(lock_root)
    request = _request(Path(contract_root), root, fixture, owner_payload)
    backend = PosixDeviceLockBackend(
        contract_root=Path(contract_root),
        environment=_environment(root, owner_payload),
        now=lambda: 1000,
    )
    barrier.wait()
    try:
        receipt = backend.acquire(request)
        backend.verify(receipt.encode(), request, minimum_remaining_seconds=30)
        queue.put(("acquired", owner_payload["repository"], receipt.encode()))
    except DeviceLockError as error:
        queue.put((error.code, owner_payload["repository"], ""))


class DeviceLockTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
        self.contract = load_device_lock_contract(ROOT)

    def make_root(self, parent: str) -> Path:
        root = Path(parent) / "device-lock-root"
        root.mkdir(mode=0o700)
        return root

    def backend(
        self,
        root: Path,
        owner_payload: dict[str, str],
        *,
        now,
        token: str,
    ) -> PosixDeviceLockBackend:
        return PosixDeviceLockBackend(
            contract_root=ROOT,
            environment=_environment(root, owner_payload),
            now=now,
            token_factory=lambda: token,
        )

    def test_contract_owns_backend_and_forbids_caller_selection(self) -> None:
        payload = json.loads((ROOT / "contracts/device-lock.json").read_text(encoding="utf-8"))
        self.assertEqual("posix-shared-root-v1", self.contract.backend_id)
        self.assertEqual("CIW_DEVICE_LOCK_ROOT", self.contract.root_environment)
        self.assertFalse(payload["backend"]["caller_selectable"])
        self.assertEqual(("android", "ios", "tvos"), self.contract.families)
        self.assertTrue(payload["identity"]["raw_device_identity_forbidden"])
        self.assertTrue(payload["identity"]["infrastructure_identity_forbidden"])

    def test_acquire_verify_release_and_residue_are_exact_state_protected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_root(directory)
            owner_payload = self.fixture["owners"][0]
            request = _request(ROOT, root, self.fixture, owner_payload)
            backend = self.backend(root, owner_payload, now=lambda: 1000, token="1" * 64)
            receipt = backend.acquire(request)
            self.assertEqual("1" * 64, receipt.fencing_token)
            self.assertEqual(request.resource_key_hash, receipt.resource_key_hash)
            self.assertEqual(request.owner_identity_hash, receipt.owner_identity_hash)
            self.assertEqual(1300, receipt.expires_at_epoch)
            self.assertEqual(receipt, backend.verify(receipt.encode(), request, minimum_remaining_seconds=30))

            released = backend.release(receipt.encode(), request)
            self.assertFalse(released.idempotent)
            self.assertRegex(released.release_evidence, r"^[0-9a-f]{64}$")
            self.assertRegex(released.cleanup_evidence, r"^[0-9a-f]{64}$")
            verified_release = backend.assert_released(receipt.encode(), request)
            self.assertEqual(released.marker_payload(), verified_release.marker_payload())
            self.assertFalse((root / "leases" / f"{request.resource_key_hash}.json").exists())

    def test_active_contention_is_cross_owner_and_cross_repository(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_root(directory)
            first_owner = self.fixture["owners"][0]
            second_owner = self.fixture["owners"][1]
            first_request = _request(ROOT, root, self.fixture, first_owner)
            second_request = _request(ROOT, root, self.fixture, second_owner)
            first = self.backend(root, first_owner, now=lambda: 1000, token="1" * 64)
            second = self.backend(root, second_owner, now=lambda: 1000, token="2" * 64)
            first.acquire(first_request)
            with self.assertRaisesRegex(DeviceLockError, "lock_held"):
                second.acquire(second_request)

    def test_two_independent_processes_cannot_both_gain_mutation_authority(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_root(directory)
            context = multiprocessing.get_context("spawn")
            barrier = context.Barrier(2)
            queue = context.Queue()
            processes = [
                context.Process(
                    target=_cross_run_worker,
                    args=(
                        str(ROOT),
                        str(root),
                        self.fixture,
                        owner_payload,
                        barrier,
                        queue,
                    ),
                )
                for owner_payload in self.fixture["owners"]
            ]
            for process in processes:
                process.start()
            results = [queue.get(timeout=20) for _ in processes]
            for process in processes:
                process.join(timeout=20)
                self.assertEqual(0, process.exitcode)
            self.assertEqual(1, sum(result[0] == "acquired" for result in results))
            self.assertEqual(1, sum(result[0] == "lock_held" for result in results))

            winner = next(result for result in results if result[0] == "acquired")
            owner_payload = next(
                owner
                for owner in self.fixture["owners"]
                if owner["repository"] == winner[1]
            )
            request = _request(ROOT, root, self.fixture, owner_payload)
            backend = PosixDeviceLockBackend(
                contract_root=ROOT,
                environment=_environment(root, owner_payload),
                now=lambda: 1000,
            )
            backend.release(winner[2], request)
            backend.assert_released(winner[2], request)

    def test_same_owner_acquire_is_idempotent_not_a_second_fence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_root(directory)
            owner_payload = self.fixture["owners"][0]
            request = _request(ROOT, root, self.fixture, owner_payload)
            tokens = iter(("1" * 64, "2" * 64))
            backend = PosixDeviceLockBackend(
                contract_root=ROOT,
                environment=_environment(root, owner_payload),
                now=lambda: 1000,
                token_factory=lambda: next(tokens),
            )
            first = backend.acquire(request)
            second = backend.acquire(request)
            self.assertEqual(first, second)
            self.assertEqual("1" * 64, second.fencing_token)

    def test_expiry_and_newer_holder_supersede_the_old_fence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_root(directory)
            clock = [1000]
            first_owner = self.fixture["owners"][0]
            second_owner = self.fixture["owners"][1]
            first_request = _request(ROOT, root, self.fixture, first_owner)
            second_request = _request(ROOT, root, self.fixture, second_owner)
            first = self.backend(root, first_owner, now=lambda: clock[0], token="1" * 64)
            old = first.acquire(first_request)
            clock[0] = 1300
            with self.assertRaisesRegex(DeviceLockError, "lock_expired"):
                first.verify(old.encode(), first_request)

            second = self.backend(root, second_owner, now=lambda: clock[0], token="2" * 64)
            current = second.acquire(second_request)
            self.assertNotEqual(old.fencing_token, current.fencing_token)
            with self.assertRaisesRegex(DeviceLockError, "lock_stale"):
                first.verify(old.encode(), first_request)
            with self.assertRaisesRegex(DeviceLockError, "release_state_mismatch"):
                first.release(old.encode(), first_request)
            self.assertEqual(current, second.verify(current.encode(), second_request))
            second.release(current.encode(), second_request)

    def test_wrong_family_device_source_authorization_owner_and_request_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_root(directory)
            owner_payload = self.fixture["owners"][0]
            request = _request(ROOT, root, self.fixture, owner_payload)
            backend = self.backend(root, owner_payload, now=lambda: 1000, token="1" * 64)
            receipt = backend.acquire(request)
            variants = (
                replace(request, device_family="ios"),
                replace(request, device_capability="media3"),
                replace(request, device_identity_hash="c" * 64),
                replace(request, tested_source_sha="d" * 40),
                replace(request, authorization_receipt_hash="e" * 64),
                replace(request, owner_identity_hash="f" * 64),
                replace(request, request_id="issue-136-other-request"),
            )
            for variant in variants:
                with self.subTest(variant=variant):
                    with self.assertRaisesRegex(DeviceLockError, "receipt_mismatch"):
                        backend.verify(receipt.encode(), variant)

    def test_fabricated_and_replayed_receipts_never_regain_current_authority(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_root(directory)
            first_owner = self.fixture["owners"][0]
            second_owner = self.fixture["owners"][1]
            first_request = _request(ROOT, root, self.fixture, first_owner)
            second_request = _request(ROOT, root, self.fixture, second_owner)
            first = self.backend(root, first_owner, now=lambda: 1000, token="1" * 64)
            receipt = first.acquire(first_request)
            fabricated = replace(receipt, fencing_token="f" * 64)
            with self.assertRaisesRegex(DeviceLockError, "lock_stale"):
                first.verify(fabricated.encode(), first_request)

            released = first.release(receipt.encode(), first_request)
            repeated = first.release(receipt.encode(), first_request)
            self.assertTrue(repeated.idempotent)
            self.assertEqual(released.marker_payload(), repeated.marker_payload())

            second = self.backend(root, second_owner, now=lambda: 1001, token="2" * 64)
            newer = second.acquire(second_request)
            with self.assertRaisesRegex(DeviceLockError, "lock_stale"):
                first.verify(receipt.encode(), first_request)
            with self.assertRaisesRegex(DeviceLockError, "release_state_mismatch"):
                first.release(receipt.encode(), first_request)
            second.release(newer.encode(), second_request)

    def test_exception_path_expected_state_releases_the_lock(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_root(directory)
            owner_payload = self.fixture["owners"][0]
            request = _request(ROOT, root, self.fixture, owner_payload)
            backend = self.backend(root, owner_payload, now=lambda: 1000, token="1" * 64)
            captured: list[DeviceLockReceipt] = []
            with self.assertRaisesRegex(RuntimeError, "cancelled"):
                with locked_device_session(backend, request) as receipt:
                    captured.append(receipt)
                    backend.verify(receipt.encode(), request)
                    raise RuntimeError("cancelled")
            self.assertEqual(1, len(captured))
            backend.assert_released(captured[0].encode(), request)

    def test_receipt_and_backend_state_are_redacted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self.make_root(directory)
            owner_payload = self.fixture["owners"][0]
            raw_device = "SERIAL-DEVICE-1234"
            fixture = dict(self.fixture)
            fixture["device_identity_hash"] = hashlib.sha256(raw_device.encode()).hexdigest()
            request = _request(ROOT, root, fixture, owner_payload)
            backend = self.backend(root, owner_payload, now=lambda: 1000, token="1" * 64)
            receipt = backend.acquire(request)
            serialized = receipt.encode()
            persisted = "\n".join(
                path.read_text(encoding="utf-8")
                for path in root.rglob("*.json")
            )
            for forbidden in (
                raw_device,
                str(fixture["authorization_receipt"]),
                str(owner_payload["repository"]),
                str(root),
            ):
                self.assertNotIn(forbidden, serialized)
                self.assertNotIn(forbidden, persisted)
            self.assertIn(request.device_identity_hash, persisted)
            self.assertIn(request.authorization_receipt_hash, persisted)
            self.assertIn(request.owner_identity_hash, persisted)

    def test_backend_root_rejects_relative_symlink_and_permissive_paths(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            root = self.make_root(directory)
            environment = {"CIW_DEVICE_LOCK_ROOT": str(root)}
            self.assertEqual(root, resolve_backend_root(self.contract, environment))
            with self.assertRaisesRegex(DeviceLockError, "backend_unavailable"):
                resolve_backend_root(self.contract, {"CIW_DEVICE_LOCK_ROOT": "relative"})

            root.chmod(0o755)
            with self.assertRaisesRegex(DeviceLockError, "backend_unavailable"):
                resolve_backend_root(self.contract, environment)
            root.chmod(0o700)

            target = parent / "target"
            target.mkdir(mode=0o700)
            link = parent / "link"
            link.symlink_to(target, target_is_directory=True)
            with self.assertRaisesRegex(DeviceLockError, "backend_unavailable"):
                resolve_backend_root(self.contract, {"CIW_DEVICE_LOCK_ROOT": str(link)})


if __name__ == "__main__":
    unittest.main()
