from __future__ import annotations

import copy
import hashlib
import json
import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

from ci_workflows.device_contract import (  # noqa: E402
    build_plan,
    load_device_contract,
    load_evidence_contract,
    request_from_environment,
)
from ci_workflows.device_execution import (  # noqa: E402
    InMemoryDeviceLockAdapter,
    SyntheticDeviceRuntime,
    assert_zero_device_residue,
    build_evidence_packet,
    cleanup_device_state,
    evidence_id,
    execute_device_plan,
    parse_android_inventory,
    parse_apple_inventory,
    select_device,
    stable_identity_hash,
    validate_evidence_packet,
)
from ci_workflows.device_types import (  # noqa: E402
    DeviceFamily,
    DeviceRecord,
    DeviceValidationError,
    SerialPolicy,
)


SHA = "a" * 40


def synthetic_environment(
    family: str = "android",
    *,
    capability: str | None = None,
    inventory_mode: bool = True,
) -> dict[str, str]:
    capabilities = {
        "android": "synthetic-android",
        "ios": "synthetic-ios",
        "tvos": "synthetic-tvos",
    }
    environment = {
        "GITHUB_REPOSITORY": "StreamScapeTV/ci-workflows",
        "GITHUB_EVENT_NAME": "pull_request",
        "GITHUB_RUN_ID": "31234567890",
        "GITHUB_RUN_ATTEMPT": "1",
        "INPUT_ADMITTED_SHA": SHA,
        "INPUT_DEVICE_FAMILY": family,
        "INPUT_DEVICE_CAPABILITY": capability or capabilities[family],
        "INPUT_DEVICE_IDENTIFIER": "",
        "INPUT_COMMAND_PROFILE": "ciw-device-synthetic",
        "INPUT_SCRIPT_PATH": "tests/fixtures/device-validation/scripts/test.sh",
        "INPUT_MAX_DURATION_MINUTES": "15",
        "INPUT_EVIDENCE_EXCEPTION_ID": "",
        "INPUT_REQUEST_ID": "issue-14-contract-smoke",
        "INPUT_SOURCE_TRUST": "trusted-pr",
    }
    if inventory_mode:
        environment["CIW_DEVICE_SYNTHETIC_MODE"] = "true"
    return environment


def real_ios_environment(*, secret: bool = False) -> dict[str, str]:
    environment = {
        "GITHUB_REPOSITORY": "StreamScapeTV/streamscape-media",
        "GITHUB_EVENT_NAME": "workflow_dispatch",
        "GITHUB_RUN_ID": "31234567891",
        "GITHUB_RUN_ATTEMPT": "1",
        "INPUT_ADMITTED_SHA": SHA,
        "INPUT_DEVICE_FAMILY": "ios",
        "INPUT_DEVICE_CAPABILITY": "native-failover",
        "INPUT_DEVICE_IDENTIFIER": "TEST-ONLY-DEVICE-001",
        "INPUT_COMMAND_PROFILE": "streamscape-media-ios-device",
        "INPUT_SCRIPT_PATH": "scripts/ci/run-ios-device-packet.sh",
        "INPUT_MAX_DURATION_MINUTES": "60",
        "INPUT_EVIDENCE_EXCEPTION_ID": "",
        "INPUT_REQUEST_ID": "issue-14-ios-physical-request",
        "INPUT_SOURCE_TRUST": "trusted-exact",
    }
    if secret:
        environment["CIW_DEVICE_LIVE_BACKEND_PRESENT"] = "true"
    return environment


class DeviceContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.contract = load_device_contract(ROOT)
        self.evidence = load_evidence_contract(ROOT)

    def test_contract_identity_and_three_families(self) -> None:
        self.assertEqual("validation.device", self.contract["workflow_api"])
        self.assertEqual("1.0.0", self.contract["contract_version"])
        self.assertEqual(
            {"android", "ios", "tvos"},
            set(self.contract["families"]),
        )
        self.assertEqual("physical-device", self.contract["execution_overlay_profile"])

    def test_contract_contains_no_real_fleet_or_endpoint_values(self) -> None:
        text = json.dumps(self.contract).casefold()
        for forbidden in (
            "192.168.",
            "10.0.",
            "private endpoint",
            "personal device",
            "bearer ",
            "password=",
            "token=",
        ):
            self.assertNotIn(forbidden, text)

    def test_synthetic_same_repository_planning_is_allowed_only_for_smoke(self) -> None:
        request = request_from_environment(
            synthetic_environment(),
            self.contract,
        )
        plan = build_plan(self.contract, request)
        self.assertEqual("ciw-synthetic-android", plan.profile.profile_id)
        self.assertFalse(plan.execution_authorized)
        self.assertEqual("trusted-pr", request.source_trust)

    def test_untrusted_or_fork_like_source_is_rejected(self) -> None:
        environment = synthetic_environment()
        environment["INPUT_SOURCE_TRUST"] = "untrusted-fork"
        with self.assertRaisesRegex(DeviceValidationError, "source_trust_rejected"):
            request_from_environment(environment, self.contract)

    def test_missing_issue_request_identity_is_rejected(self) -> None:
        environment = synthetic_environment()
        environment["INPUT_REQUEST_ID"] = "contract-smoke"
        with self.assertRaisesRegex(DeviceValidationError, "request_identity_rejected"):
            request_from_environment(environment, self.contract)

    def test_arbitrary_runner_command_secret_and_deployment_inputs_are_rejected(self) -> None:
        for name in (
            "INPUT_RUNNER",
            "INPUT_ARBITRARY_COMMAND",
            "INPUT_SECRET_NAME",
            "INPUT_SIGNING_IDENTITY",
            "INPUT_STORE",
            "INPUT_DEPLOYMENT",
        ):
            with self.subTest(name=name):
                environment = synthetic_environment()
                environment[name] = "forbidden"
                with self.assertRaisesRegex(DeviceValidationError, "forbidden_input"):
                    request_from_environment(environment, self.contract)

    def test_script_traversal_is_rejected(self) -> None:
        environment = synthetic_environment()
        environment["INPUT_SCRIPT_PATH"] = "../scripts/test.sh"
        with self.assertRaisesRegex(DeviceValidationError, "command_profile_rejected"):
            request_from_environment(environment, self.contract)

    def test_wrong_family_or_command_profile_is_rejected(self) -> None:
        environment = synthetic_environment("ios")
        environment["INPUT_COMMAND_PROFILE"] = "streamscape-media-ios-device"
        request = request_from_environment(environment, self.contract)
        with self.assertRaisesRegex(DeviceValidationError, "device_profile_rejected"):
            build_plan(self.contract, request)

    def test_exact_identifier_is_required_for_real_profile(self) -> None:
        environment = real_ios_environment()
        environment["INPUT_DEVICE_IDENTIFIER"] = ""
        request = request_from_environment(environment, self.contract)
        with self.assertRaisesRegex(DeviceValidationError, "device_identifier_rejected"):
            build_plan(self.contract, request)

    def test_identifier_is_forbidden_for_synthetic_profile(self) -> None:
        environment = synthetic_environment()
        environment["INPUT_DEVICE_IDENTIFIER"] = "SYNTHETIC-ANDROID-A"
        request = request_from_environment(environment, self.contract)
        with self.assertRaisesRegex(DeviceValidationError, "device_identifier_rejected"):
            build_plan(self.contract, request)

    def test_live_backend_profile_fails_without_fixed_secret_contract(self) -> None:
        environment = {
            "GITHUB_REPOSITORY": "StreamScapeTV/iptv-android",
            "GITHUB_EVENT_NAME": "workflow_dispatch",
            "GITHUB_RUN_ID": "31234567892",
            "GITHUB_RUN_ATTEMPT": "1",
            "INPUT_ADMITTED_SHA": SHA,
            "INPUT_DEVICE_FAMILY": "android",
            "INPUT_DEVICE_CAPABILITY": "instrumentation",
            "INPUT_DEVICE_IDENTIFIER": "TEST-ANDROID-001",
            "INPUT_COMMAND_PROFILE": "iptv-android-device",
            "INPUT_SCRIPT_PATH": "build.sh",
            "INPUT_MAX_DURATION_MINUTES": "60",
            "INPUT_EVIDENCE_EXCEPTION_ID": "",
            "INPUT_REQUEST_ID": "issue-14-android-acceptance",
            "INPUT_SOURCE_TRUST": "trusted-exact",
        }
        request = request_from_environment(environment, self.contract)
        with self.assertRaisesRegex(DeviceValidationError, "live_backend_rejected"):
            build_plan(self.contract, request)
        environment["CIW_DEVICE_LIVE_BACKEND_PRESENT"] = "true"
        request = request_from_environment(environment, self.contract)
        self.assertEqual("iptv-android-acceptance", build_plan(self.contract, request).profile.profile_id)

    def test_non_backend_profile_rejects_unexpected_secret_presence(self) -> None:
        environment = real_ios_environment(secret=True)
        request = request_from_environment(environment, self.contract)
        with self.assertRaisesRegex(DeviceValidationError, "live_backend_rejected"):
            build_plan(self.contract, request)

    def test_artifact_exception_allow_and_deny(self) -> None:
        environment = real_ios_environment()
        environment["INPUT_EVIDENCE_EXCEPTION_ID"] = "redacted-device-diagnostics"
        request = request_from_environment(environment, self.contract)
        plan = build_plan(self.contract, request)
        self.assertEqual("redacted-device-diagnostics", plan.request.evidence_exception_id)
        environment["INPUT_EVIDENCE_EXCEPTION_ID"] = "unregistered"
        request = request_from_environment(environment, self.contract)
        with self.assertRaisesRegex(DeviceValidationError, "artifact_exception_rejected"):
            build_plan(self.contract, request)


class DiscoverySelectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.contract = load_device_contract(ROOT)

    def plan(self, family: str = "android"):
        request = request_from_environment(
            synthetic_environment(family),
            self.contract,
        )
        return build_plan(self.contract, request)

    def test_valid_android_inventory(self) -> None:
        records = parse_android_inventory(
            (ROOT / "tests/fixtures/device-validation/android.txt").read_text()
        )
        self.assertEqual(2, len(records))
        self.assertTrue(all(record.family is DeviceFamily.ANDROID for record in records))

    def test_valid_ios_and_tvos_inventory(self) -> None:
        ios = parse_apple_inventory(
            (ROOT / "tests/fixtures/device-validation/ios.json").read_text(),
            DeviceFamily.IOS,
        )
        tvos = parse_apple_inventory(
            (ROOT / "tests/fixtures/device-validation/tvos.json").read_text(),
            DeviceFamily.TVOS,
        )
        self.assertEqual("synthetic-iphone", ios[0].model)
        self.assertEqual("synthetic-apple-tv", tvos[0].model)

    def test_malformed_inventory_is_rejected(self) -> None:
        with self.assertRaisesRegex(DeviceValidationError, "device_inventory_malformed"):
            parse_apple_inventory(
                (ROOT / "tests/fixtures/device-validation/malformed.json").read_text(),
                DeviceFamily.IOS,
            )
        with self.assertRaisesRegex(DeviceValidationError, "device_inventory_malformed"):
            parse_android_inventory("not a bounded record\n")

    def test_offline_inventory_projects_stable_failure(self) -> None:
        records = parse_android_inventory(
            (ROOT / "tests/fixtures/device-validation/android-offline.txt").read_text()
        )
        with self.assertRaisesRegex(DeviceValidationError, "device_offline"):
            select_device(self.plan(), records)

    def test_deterministic_tie_break_uses_redacted_identity_hash(self) -> None:
        plan = self.plan()
        records = parse_android_inventory(
            (ROOT / "tests/fixtures/device-validation/android.txt").read_text()
        )
        selected = select_device(plan, records)
        expected = min(
            stable_identity_hash(plan.profile.profile_id, record.family, record.raw_identifier)
            for record in records
        )
        self.assertEqual(expected, selected.identity_hash)
        self.assertNotIn(selected._raw_identifier, json.dumps(selected.public_projection()))

    def test_multiple_matches_without_reviewed_tie_break_are_rejected(self) -> None:
        plan = self.plan()
        profile = replace(
            plan.profile,
            serial_policy=SerialPolicy.CONTRACT_OWNED,
            selection_policy="unique",
        )
        plan = replace(plan, profile=profile)
        records = parse_android_inventory(
            (ROOT / "tests/fixtures/device-validation/android.txt").read_text()
        )
        with self.assertRaisesRegex(DeviceValidationError, "device_multiple_matches"):
            select_device(plan, records)

    def test_wrong_version_model_capability_and_connection_do_not_match(self) -> None:
        plan = self.plan()
        base = DeviceRecord(
            raw_identifier="TEST-DEVICE-001",
            family=DeviceFamily.ANDROID,
            state="online",
            connection="usb",
            model="synthetic-phone",
            capabilities=("synthetic-android",),
            api_level=37,
        )
        for changed in (
            replace(base, api_level=28),
            replace(base, model="other-phone"),
            replace(base, capabilities=("other",)),
            replace(base, connection="network"),
            replace(base, personal=True),
            replace(base, conflicting=True),
        ):
            with self.subTest(record=changed):
                with self.assertRaises(DeviceValidationError):
                    select_device(plan, (changed,))

    def test_wrong_family_is_not_physical_certification(self) -> None:
        plan = self.plan("ios")
        android = parse_android_inventory(
            (ROOT / "tests/fixtures/device-validation/android.txt").read_text()
        )
        with self.assertRaisesRegex(DeviceValidationError, "device_no_match"):
            select_device(plan, android)


class LockLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        contract = load_device_contract(ROOT)
        request = request_from_environment(synthetic_environment(), contract)
        self.plan = build_plan(contract, request)
        records = parse_android_inventory(
            (ROOT / "tests/fixtures/device-validation/android.txt").read_text()
        )
        self.selected = select_device(self.plan, records)

    def test_lock_success_collision_release_and_epoch_increment(self) -> None:
        adapter = InMemoryDeviceLockAdapter()
        first = adapter.acquire(plan=self.plan, selected=self.selected, now=100)
        self.assertTrue(first.accepted)
        collision = adapter.acquire(plan=self.plan, selected=self.selected, now=101)
        self.assertFalse(collision.accepted)
        self.assertEqual("wait-for-release-or-expiry", collision.next_action)
        released = adapter.release(first, now=102)
        self.assertTrue(released.released)
        second = adapter.acquire(plan=self.plan, selected=self.selected, now=103)
        self.assertEqual(first.epoch + 1, second.epoch)

    def test_expired_lock_converges_to_new_owner(self) -> None:
        adapter = InMemoryDeviceLockAdapter()
        first = adapter.acquire(plan=self.plan, selected=self.selected, now=100)
        second = adapter.acquire(
            plan=self.plan,
            selected=self.selected,
            now=first.expires_at,
        )
        self.assertTrue(second.accepted)
        self.assertGreater(second.epoch, first.epoch)

    def test_stale_epoch_release_fails(self) -> None:
        adapter = InMemoryDeviceLockAdapter()
        receipt = adapter.acquire(plan=self.plan, selected=self.selected, now=100)
        adapter.release(receipt, now=101)
        with self.assertRaisesRegex(DeviceValidationError, "lock_stale_epoch"):
            adapter.release(receipt, now=102)


class EvidenceAndExecutionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.contract = load_device_contract(ROOT)
        self.evidence = load_evidence_contract(ROOT)
        self.request = request_from_environment(
            synthetic_environment(),
            self.contract,
        )
        self.plan = build_plan(self.contract, self.request)
        self.records = parse_android_inventory(
            (ROOT / "tests/fixtures/device-validation/android.txt").read_text()
        )

    @staticmethod
    def clock(values=(1000, 1001, 1002)):
        iterator = iter(values)
        return lambda: next(iterator)

    def test_successful_synthetic_lifecycle_releases_lock_and_redacts(self) -> None:
        adapter = InMemoryDeviceLockAdapter()
        runtime = SyntheticDeviceRuntime()
        result = execute_device_plan(
            plan=self.plan,
            records=self.records,
            lock_adapter=adapter,
            runtime=runtime,
            evidence_contract=self.evidence,
            now=self.clock(),
            synthetic_authorized=True,
        )
        self.assertEqual("success", result.result)
        self.assertTrue(runtime.restored)
        self.assertTrue(runtime.cleaned)
        self.assertEqual(0, adapter.active_count())
        serialized = json.dumps(result.evidence_packet, sort_keys=True)
        for record in self.records:
            self.assertNotIn(record.raw_identifier, serialized)
        self.assertEqual(result.evidence_id, evidence_id(result.evidence_packet))

    def test_prepare_test_evidence_and_disconnect_failures_propagate(self) -> None:
        cases = {
            "prepare": "prepare_failed",
            "test": "stage_failed",
            "evidence": "evidence_policy_failed",
        }
        for stage, expected in cases.items():
            with self.subTest(stage=stage):
                adapter = InMemoryDeviceLockAdapter()
                runtime = SyntheticDeviceRuntime(fail_stage=stage)
                result = execute_device_plan(
                    plan=self.plan,
                    records=self.records,
                    lock_adapter=adapter,
                    runtime=runtime,
                    evidence_contract=self.evidence,
                    now=self.clock(),
                    synthetic_authorized=True,
                )
                self.assertEqual(expected, result.failure_code)
                self.assertEqual(0, adapter.active_count())
        adapter = InMemoryDeviceLockAdapter()
        runtime = SyntheticDeviceRuntime(disconnect_after_test=True)
        result = execute_device_plan(
            plan=self.plan,
            records=self.records,
            lock_adapter=adapter,
            runtime=runtime,
            evidence_contract=self.evidence,
            now=self.clock(),
            synthetic_authorized=True,
        )
        self.assertEqual("device_disconnected", result.failure_code)

    def test_primary_failure_is_not_hidden_by_cleanup_failure(self) -> None:
        class DoubleFailureRuntime(SyntheticDeviceRuntime):
            def test(self, plan, selected):
                raise DeviceValidationError("stage_failed")

            def cleanup(self, plan, selected):
                raise DeviceValidationError("cleanup_failed")

        adapter = InMemoryDeviceLockAdapter()
        result = execute_device_plan(
            plan=self.plan,
            records=self.records,
            lock_adapter=adapter,
            runtime=DoubleFailureRuntime(),
            evidence_contract=self.evidence,
            now=self.clock(),
            synthetic_authorized=True,
        )
        self.assertEqual("stage_failed", result.failure_code)
        self.assertEqual("failure", result.cleanup_result)
        self.assertEqual(0, adapter.active_count())

    def test_restoration_and_residue_failures_are_visible(self) -> None:
        for stage, expected in (
            ("restore", "device_restoration_failed"),
            ("residue", "cleanup_failed"),
        ):
            with self.subTest(stage=stage):
                adapter = InMemoryDeviceLockAdapter()
                result = execute_device_plan(
                    plan=self.plan,
                    records=self.records,
                    lock_adapter=adapter,
                    runtime=SyntheticDeviceRuntime(fail_stage=stage),
                    evidence_contract=self.evidence,
                    now=self.clock(),
                    synthetic_authorized=True,
                )
                self.assertEqual(expected, result.failure_code)
                self.assertEqual(0, adapter.active_count())

    def test_lock_collision_returns_bounded_failure_packet(self) -> None:
        adapter = InMemoryDeviceLockAdapter()
        selected = select_device(self.plan, self.records)
        adapter.acquire(plan=self.plan, selected=selected, now=999)
        result = execute_device_plan(
            plan=self.plan,
            records=self.records,
            lock_adapter=adapter,
            runtime=SyntheticDeviceRuntime(),
            evidence_contract=self.evidence,
            now=self.clock(),
            synthetic_authorized=True,
        )
        self.assertEqual("lock_collision", result.failure_code)
        self.assertEqual("not-started", result.cleanup_result)

    def test_evidence_overclaim_and_raw_identifier_are_rejected(self) -> None:
        adapter = InMemoryDeviceLockAdapter()
        selected = select_device(self.plan, self.records)
        receipt = adapter.acquire(plan=self.plan, selected=selected, now=100)
        with self.assertRaisesRegex(DeviceValidationError, "evidence_overclaim"):
            build_evidence_packet(
                plan=self.plan,
                selected=selected,
                lock_receipt=receipt,
                release_receipt=None,
                evidence_contract=self.evidence,
                started_at=100,
                ended_at=101,
                result="success",
                failure_code="",
                assertions=("exact-source-verified",),
                restoration="success",
                cleanup="success",
                certification_scope="physical-device/all-products",
            )
        packet = build_evidence_packet(
            plan=self.plan,
            selected=selected,
            lock_receipt=receipt,
            release_receipt=None,
            evidence_contract=self.evidence,
            started_at=100,
            ended_at=101,
            result="success",
            failure_code="",
            assertions=("exact-source-verified",),
            restoration="success",
            cleanup="success",
        )
        packet["classification"]["serial"] = selected._raw_identifier
        with self.assertRaisesRegex(DeviceValidationError, "evidence_policy_failed"):
            validate_evidence_packet(
                packet,
                self.evidence,
                raw_identifier=selected._raw_identifier,
            )

    def test_packet_is_deterministic(self) -> None:
        def run():
            return execute_device_plan(
                plan=self.plan,
                records=self.records,
                lock_adapter=InMemoryDeviceLockAdapter(),
                runtime=SyntheticDeviceRuntime(),
                evidence_contract=self.evidence,
                now=self.clock(),
                synthetic_authorized=True,
            )

        first = run()
        second = run()
        self.assertEqual(first.evidence_packet, second.evidence_packet)
        self.assertEqual(first.evidence_id, second.evidence_id)

    def test_live_execution_is_not_authorized_by_source_package(self) -> None:
        with self.assertRaisesRegex(DeviceValidationError, "authorization_rejected"):
            execute_device_plan(
                plan=self.plan,
                records=self.records,
                lock_adapter=InMemoryDeviceLockAdapter(),
                runtime=SyntheticDeviceRuntime(),
                evidence_contract=self.evidence,
                now=self.clock(),
                synthetic_authorized=False,
            )


class CleanupTests(unittest.TestCase):
    def test_no_follow_cleanup_preserves_outside_sentinel(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state = root / "state"
            outside = root / "outside.txt"
            outside.write_text("keep", encoding="utf-8")
            target = state / "device-validation"
            target.mkdir(parents=True)
            (target / "nested").mkdir()
            (target / "nested/file.txt").write_text("remove", encoding="utf-8")
            (target / "escape").symlink_to(outside)
            cleanup_device_state(state)
            assert_zero_device_residue(state)
            self.assertEqual("keep", outside.read_text(encoding="utf-8"))

    def test_residue_check_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary) / "state"
            target = state / "device-results"
            target.mkdir(parents=True)
            with self.assertRaisesRegex(DeviceValidationError, "cleanup_failed"):
                assert_zero_device_residue(state)


if __name__ == "__main__":
    unittest.main()
