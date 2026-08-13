from __future__ import annotations

import json
import unittest
from dataclasses import replace
from pathlib import Path

from ci_workflows.device_contract import build_plan, load_device_contract, load_evidence_contract, request_from_environment, validate_typed_plan
from ci_workflows.device_evidence import validate_evidence_packet
from ci_workflows.device_execution import *  # noqa: F401,F403
from ci_workflows.device_types import DeviceFamily, DeviceRecord, DeviceValidationError
from device_test_support import FIX, ROOT, SHA, real_environment, synthetic_environment


class InventoryAndEvidenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.contract = load_device_contract(ROOT)
        self.evidence = load_evidence_contract(ROOT)

    def plan(self, family: str = "android"):
        return build_plan(
            self.contract,
            request_from_environment(synthetic_environment(family), self.contract),
        )

    def synthetic_result(self, family: str = "android"):
        plan = self.plan(family)
        if family == "android":
            records = parse_android_inventory((FIX / "android.txt").read_text())
        else:
            records = parse_apple_inventory(
                (FIX / f"{family}.json").read_text(),
                DeviceFamily(family),
            )
        return execute_device_plan(
            plan=plan,
            records=records,
            lock_adapter=InMemoryDeviceLockAdapter(),
            runtime=SyntheticDeviceRuntime(),
            evidence_contract=self.evidence,
            now=iter((1000, 1001, 1002)).__next__,
            synthetic_authorized=True,
        ), records

    def test_positive_inventory_for_all_families(self) -> None:
        android = parse_android_inventory((FIX / "android.txt").read_text())
        ios = parse_apple_inventory((FIX / "ios.json").read_text(), DeviceFamily.IOS)
        tvos = parse_apple_inventory((FIX / "tvos.json").read_text(), DeviceFamily.TVOS)
        self.assertEqual((2, 1, 1), (len(android), len(ios), len(tvos)))

    def test_malformed_and_wrong_family_fixtures_fail(self) -> None:
        with self.assertRaisesRegex(DeviceValidationError, "device_inventory_malformed"):
            parse_android_inventory((FIX / "malformed-android.txt").read_text())
        with self.assertRaisesRegex(DeviceValidationError, "device_inventory_malformed"):
            parse_apple_inventory((FIX / "malformed.json").read_text(), DeviceFamily.IOS)
        with self.assertRaisesRegex(DeviceValidationError, "device_family_mismatch"):
            parse_apple_inventory((FIX / "wrong-family.json").read_text(), DeviceFamily.IOS)

    def test_offline_personal_and_multiple_cases_fail_closed(self) -> None:
        with self.assertRaisesRegex(DeviceValidationError, "device_offline"):
            select_device(
                self.plan(),
                parse_android_inventory((FIX / "android-offline.txt").read_text()),
            )
        with self.assertRaisesRegex(DeviceValidationError, "device_no_match"):
            select_device(
                self.plan("ios"),
                parse_apple_inventory((FIX / "personal-ios.json").read_text(), DeviceFamily.IOS),
            )
        with self.assertRaisesRegex(DeviceValidationError, "device_multiple_matches"):
            select_device(
                self.plan("ios"),
                parse_apple_inventory((FIX / "multiple-ios.json").read_text(), DeviceFamily.IOS),
            )

    def test_identity_hash_selection_is_deterministic_and_redacted(self) -> None:
        plan = self.plan()
        records = parse_android_inventory((FIX / "ambiguous-android.txt").read_text())
        first = select_device(plan, records)
        second = select_device(plan, tuple(reversed(records)))
        self.assertEqual(first.identity_hash, second.identity_hash)
        projection = json.dumps(first.public_projection(), sort_keys=True)
        self.assertNotIn(first._raw_identifier, projection)

    def test_synthetic_lifecycle_restores_cleans_and_redacts(self) -> None:
        plan = self.plan()
        records = parse_android_inventory((FIX / "android.txt").read_text())
        adapter = InMemoryDeviceLockAdapter()
        runtime = SyntheticDeviceRuntime()
        times = iter((1000, 1001, 1002))
        result = execute_device_plan(
            plan=plan,
            records=records,
            lock_adapter=adapter,
            runtime=runtime,
            evidence_contract=self.evidence,
            now=lambda: next(times),
            synthetic_authorized=True,
        )
        self.assertEqual("success", result.result)
        self.assertRegex(result.evidence_id, r"^[0-9a-f]{64}$")
        self.assertTrue(runtime.restored and runtime.cleaned)
        self.assertEqual(0, adapter.active_count())
        serialized = json.dumps(result.evidence_packet, sort_keys=True)
        for record in records:
            self.assertNotIn(record.raw_identifier, serialized)
        self.assertFalse(result.evidence_packet["serialization"]["cross_run_fencing_claimed"])
        self.assertEqual("in-memory-tests-only", result.evidence_packet["lock"]["adapter"])
        self.assertEqual(
            "synthetic-contract/android",
            result.evidence_packet["certification_scope"],
        )
        self.assertIn(
            "synthetic-contract-evidence-is-not-physical-device-proof",
            result.evidence_packet["limitations"],
        )
        self.assertIn(
            "synthetic-family-contract-verified",
            result.evidence_packet["assertions"],
        )
        self.assertNotIn(
            "physical-family-verified",
            result.evidence_packet["assertions"],
        )
        self.assertNotIn(
            "authorization-validated",
            result.evidence_packet["assertions"],
        )

    def test_synthetic_evidence_cannot_be_relabelled_as_physical(self) -> None:
        result, records = self.synthetic_result()
        packet = dict(result.evidence_packet)
        packet["certification_scope"] = "physical-device/android"
        with self.assertRaisesRegex(DeviceValidationError, "evidence_overclaim"):
            validate_evidence_packet(
                packet,
                self.evidence,
                raw_identifier=records[0].raw_identifier,
            )

    def test_real_physical_execution_is_explicitly_disabled(self) -> None:
        plan = self.plan()
        records = parse_android_inventory((FIX / "android.txt").read_text())
        with self.assertRaisesRegex(DeviceValidationError, "physical_authorization_required"):
            execute_device_plan(
                plan=plan,
                records=records,
                lock_adapter=InMemoryDeviceLockAdapter(),
                runtime=SyntheticDeviceRuntime(),
                evidence_contract=self.evidence,
                now=lambda: 1000,
                synthetic_authorized=False,
            )

    def test_primary_failure_and_cleanup_failure_are_both_reported(self) -> None:
        class DoubleFailure(SyntheticDeviceRuntime):
            def test(self, plan, selected):
                raise DeviceValidationError("stage_failed")

            def cleanup(self, plan, selected):
                raise DeviceValidationError("cleanup_failed")

        times = iter((1000, 1001, 1002))
        result = execute_device_plan(
            plan=self.plan(),
            records=parse_android_inventory((FIX / "android.txt").read_text()),
            lock_adapter=InMemoryDeviceLockAdapter(),
            runtime=DoubleFailure(),
            evidence_contract=self.evidence,
            now=lambda: next(times),
            synthetic_authorized=True,
        )
        self.assertEqual("stage_failed", result.failure_code)
        self.assertEqual("failure", result.cleanup_result)
        self.assertIn("cleanup-failures:cleanup", result.evidence_packet["assertions"])

    def test_in_memory_adapter_is_test_only_and_not_cross_run_authority(self) -> None:
        self.assertIn("test double only", InMemoryDeviceLockAdapter.__doc__ or "")
        self.assertFalse(self.contract["lock_contract"]["cross_run_fencing_claimed"])
        self.assertEqual("none-in-source-package", self.contract["lock_contract"]["production_adapter"])

    def test_evidence_rejects_unreviewed_assertions_that_could_leak_identifiers(self) -> None:
        result, records = self.synthetic_result()
        packet = dict(result.evidence_packet)
        packet["assertions"] = [records[0].raw_identifier]
        with self.assertRaisesRegex(DeviceValidationError, "evidence_policy_failed"):
            validate_evidence_packet(
                packet,
                self.evidence,
                raw_identifier="different-opaque-identifier",
            )


if __name__ == "__main__":
    unittest.main()
