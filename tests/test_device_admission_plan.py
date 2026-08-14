from __future__ import annotations

import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch

from ci_workflows.ciw_device import _runs_on_json
from ci_workflows.device_contract import (
    build_plan,
    load_device_contract,
    request_from_environment,
    validate_typed_plan,
)
from ci_workflows.device_execution import validate_authorization_receipt
from ci_workflows.device_types import DeviceValidationError
from device_test_support import ROOT, real_environment, synthetic_environment


class AdmissionAndPlanTests(unittest.TestCase):
    def setUp(self) -> None:
        self.contract = load_device_contract(ROOT)

    def test_contract_identity_and_authorization_boundary(self) -> None:
        self.assertEqual("1.1.0", self.contract["contract_version"])
        self.assertEqual(
            "exact-family-runtime-receipt",
            self.contract["owner_authorization"]["mode"],
        )
        self.assertEqual([], self.contract["owner_authorization"]["authorized_families"])
        self.assertFalse(
            self.contract["owner_authorization"]["runner_or_secret_is_authorization"]
        )
        self.assertTrue(self.contract["lock_contract"]["cross_run_fencing_claimed"])
        self.assertEqual(
            "device-lock/1:posix-shared-root-v1",
            self.contract["lock_contract"]["production_adapter"],
        )
        self.assertEqual(
            "in-memory-tests-only",
            self.contract["lock_contract"]["temporary_reference_adapter"],
        )
        self.assertFalse(self.contract["lock_contract"]["agent_state_transport_used"])
        self.assertEqual(
            ["device_authorization_receipt", "live_test_credentials"],
            self.contract["public_secrets"],
        )
        self.assertTrue(self.contract["serialization_contract"]["fencing_token"])

    def test_source_trust_is_derived_and_not_an_input(self) -> None:
        request = request_from_environment(synthetic_environment(), self.contract)
        self.assertEqual("trusted-pr", request.source_trust)
        self.assertNotIn("source_trust", self.contract["public_inputs"])
        environment = synthetic_environment()
        environment["INPUT_SOURCE_TRUST"] = "trusted-pr"
        with self.assertRaisesRegex(DeviceValidationError, "forbidden_input"):
            request_from_environment(environment, self.contract)

    def test_untrusted_fork_and_cross_repository_source_are_rejected(self) -> None:
        for key, value in (
            ("CIW_DEVICE_HEAD_FORK", "true"),
            ("CIW_DEVICE_HEAD_REPOSITORY", "someone/fork"),
            ("CIW_DEVICE_EVENT_REPOSITORY", "someone/fork"),
        ):
            with self.subTest(key=key):
                environment = synthetic_environment()
                environment[key] = value
                with self.assertRaisesRegex(
                    DeviceValidationError, "source_admission_rejected"
                ):
                    request_from_environment(environment, self.contract)

    def test_moved_sha_is_rejected(self) -> None:
        environment = synthetic_environment()
        environment["CIW_DEVICE_EVENT_SHA"] = "b" * 40
        with self.assertRaisesRegex(DeviceValidationError, "source_mismatch"):
            request_from_environment(environment, self.contract)

    def test_raw_identifier_and_group_injection_are_rejected(self) -> None:
        for name in (
            "INPUT_DEVICE_IDENTIFIER",
            "INPUT_SERIAL",
            "INPUT_UDID",
            "INPUT_RAW_IDENTIFIER",
            "INPUT_CONCURRENCY_GROUP",
            "INPUT_CANCEL_IN_PROGRESS",
        ):
            with self.subTest(name=name):
                environment = synthetic_environment()
                environment[name] = "caller-controlled"
                with self.assertRaisesRegex(DeviceValidationError, "forbidden_input"):
                    request_from_environment(environment, self.contract)

    def test_opaque_alias_resolves_to_contract_class(self) -> None:
        plan = build_plan(
            self.contract,
            request_from_environment(synthetic_environment(), self.contract),
        )
        self.assertEqual("synthetic-android-class", plan.alias_class)
        self.assertEqual(
            "device-validation-ciw-synthetic-android-android-synthetic-android-class",
            plan.concurrency_group,
        )
        self.assertNotIn("SYNTHETIC-ANDROID", plan.concurrency_group)

    def test_unknown_or_injected_alias_fails_closed(self) -> None:
        for alias in (
            "not-reviewed",
            "synthetic-primary-${{github.sha}}",
            "../../raw",
        ):
            with self.subTest(alias=alias):
                environment = synthetic_environment()
                environment["INPUT_DEVICE_ALIAS"] = alias
                try:
                    request = request_from_environment(environment, self.contract)
                except DeviceValidationError as error:
                    self.assertIn(
                        error.code,
                        {"device_profile_rejected", "invalid_input"},
                    )
                else:
                    with self.assertRaisesRegex(
                        DeviceValidationError, "device_profile_rejected"
                    ):
                        build_plan(self.contract, request)

    def test_concurrency_is_contract_owned_and_immutable(self) -> None:
        plan = build_plan(
            self.contract,
            request_from_environment(synthetic_environment(), self.contract),
        )
        outputs = plan.planning_outputs(runs_on_json='["mobile"]')
        packet = json.loads(outputs["validated_plan"])
        self.assertEqual(plan.concurrency_group, packet["concurrency_group"])
        self.assertFalse(packet["cancel_in_progress"])
        self.assertEqual("false", outputs["cancel_in_progress"])
        self.assertFalse(self.contract["serialization_contract"]["caller_override"])

    def test_synthetic_runner_selection_uses_approved_concrete_selectors(self) -> None:
        expected = {
            "android": ["linux", "amd64", "mobile"],
            "ios": ["macOS", "ARM64"],
            "tvos": ["macOS", "ARM64"],
        }
        with patch.dict(os.environ, {"CIW_DEVICE_FOCUSED_TEST": "false"}):
            for family, selector in expected.items():
                with self.subTest(family=family):
                    plan = build_plan(
                        self.contract,
                        request_from_environment(
                            synthetic_environment(family), self.contract
                        ),
                    )
                    self.assertEqual(selector, json.loads(_runs_on_json(ROOT, plan)))

    def _authorized_real_plan(self):
        environment = real_environment(
            repository="StreamScapeTV/iptv-android",
            family="android",
            capability="instrumentation",
            command_profile="iptv-android-device",
            script_path="build.sh",
            alias="acceptance-primary",
            secret=True,
        )
        environment["CIW_DEVICE_AUTHORIZATION_PRESENT"] = "true"
        plan = build_plan(
            self.contract,
            request_from_environment(environment, self.contract),
        )
        return environment, plan

    def test_generic_live_backend_secret_never_authorizes_execution(self) -> None:
        environment = real_environment(
            repository="StreamScapeTV/iptv-android",
            family="android",
            capability="instrumentation",
            command_profile="iptv-android-device",
            script_path="build.sh",
            alias="acceptance-primary",
            secret=True,
        )
        plan = build_plan(
            self.contract,
            request_from_environment(environment, self.contract),
        )
        self.assertFalse(plan.execution_authorized)
        self.assertEqual("physical_authorization_required", plan.authorization_failure)

    def test_dedicated_owner_receipt_presence_is_the_only_planning_authority(self) -> None:
        _environment, plan = self._authorized_real_plan()
        self.assertTrue(plan.execution_authorized)
        self.assertEqual("", plan.authorization_failure)

    def test_exact_owner_receipt_is_bound_to_request_and_expiry(self) -> None:
        _environment, plan = self._authorized_real_plan()
        receipt = {
            "packet_version": "device-authorization/1",
            "repository": plan.request.repository,
            "source_sha": plan.request.admitted_sha,
            "device_family": plan.request.family.value,
            "device_capability": plan.request.capability,
            "request_id": plan.request.request_id,
            "not_after_epoch": 2000,
        }
        raw = json.dumps(receipt, sort_keys=True, separators=(",", ":"))
        digest = validate_authorization_receipt(raw, plan=plan, now_epoch=1000)
        self.assertRegex(digest, r"^[0-9a-f]{64}$")
        wrong = dict(receipt)
        wrong["source_sha"] = "b" * 40
        with self.assertRaisesRegex(DeviceValidationError, "authorization_rejected"):
            validate_authorization_receipt(
                json.dumps(wrong, sort_keys=True, separators=(",", ":")),
                plan=plan,
                now_epoch=1000,
            )
        with self.assertRaisesRegex(DeviceValidationError, "authorization_rejected"):
            validate_authorization_receipt(raw, plan=plan, now_epoch=2001)

    def test_backend_profile_requires_secret_but_secret_is_not_authority(self) -> None:
        environment = real_environment(
            repository="StreamScapeTV/iptv-android",
            family="android",
            capability="instrumentation",
            command_profile="iptv-android-device",
            script_path="build.sh",
            alias="acceptance-primary",
        )
        request = request_from_environment(environment, self.contract)
        with self.assertRaisesRegex(DeviceValidationError, "live_backend_rejected"):
            build_plan(self.contract, request)
        environment["CIW_DEVICE_LIVE_BACKEND_PRESENT"] = "true"
        plan = build_plan(
            self.contract,
            request_from_environment(environment, self.contract),
        )
        self.assertFalse(plan.execution_authorized)

    def test_typed_plan_exact_replay_and_tamper_rejection(self) -> None:
        environment = synthetic_environment()
        plan = build_plan(
            self.contract,
            request_from_environment(environment, self.contract),
        )
        outputs = plan.planning_outputs(runs_on_json='["mobile"]')
        packet = validate_typed_plan(
            outputs["validated_plan"],
            outputs["validated_plan_sha256"],
            contract=self.contract,
            environment=environment,
        )
        self.assertEqual(plan.concurrency_group, packet["concurrency_group"])
        tampered = json.loads(outputs["validated_plan"])
        tampered["concurrency_group"] = "caller-group"
        raw = json.dumps(tampered, sort_keys=True, separators=(",", ":"))
        digest = __import__("hashlib").sha256(raw.encode()).hexdigest()
        with self.assertRaisesRegex(DeviceValidationError, "group_injection_rejected"):
            validate_typed_plan(
                raw,
                digest,
                contract=self.contract,
                environment=environment,
            )
        with self.assertRaisesRegex(
            DeviceValidationError, "typed_plan_hash_mismatch"
        ):
            validate_typed_plan(
                outputs["validated_plan"],
                "0" * 64,
                contract=self.contract,
                environment=environment,
            )

    def test_authorized_typed_plan_rebuilds_runtime_receipt_presence(self) -> None:
        environment, plan = self._authorized_real_plan()
        outputs = plan.planning_outputs(runs_on_json='["linux","amd64","mobile"]')
        packet = validate_typed_plan(
            outputs["validated_plan"],
            outputs["validated_plan_sha256"],
            contract=self.contract,
            environment=environment,
        )
        self.assertTrue(packet["execution_authorized"])
        missing = dict(environment)
        missing["CIW_DEVICE_AUTHORIZATION_PRESENT"] = "false"
        with self.assertRaisesRegex(DeviceValidationError, "typed_plan_rejected"):
            validate_typed_plan(
                outputs["validated_plan"],
                outputs["validated_plan_sha256"],
                contract=self.contract,
                environment=missing,
            )

    def test_typed_plan_rebuilds_contract_owned_profile_fields(self) -> None:
        environment = synthetic_environment()
        plan = build_plan(
            self.contract,
            request_from_environment(environment, self.contract),
        )
        outputs = plan.planning_outputs(runs_on_json='["mobile"]')
        tampered = json.loads(outputs["validated_plan"])
        tampered["script_path"] = "build.sh"
        raw = json.dumps(tampered, sort_keys=True, separators=(",", ":"))
        digest = __import__("hashlib").sha256(raw.encode()).hexdigest()
        with self.assertRaisesRegex(DeviceValidationError, "device_profile_rejected"):
            validate_typed_plan(
                raw,
                digest,
                contract=self.contract,
                environment=environment,
            )


if __name__ == "__main__":
    unittest.main()
