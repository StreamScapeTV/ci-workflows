from __future__ import annotations

import hashlib
import json
import os
import unittest
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

    def test_contract_is_product_neutral_and_authorization_is_separate(self) -> None:
        self.assertEqual("2.0.0", self.contract["contract_version"])
        self.assertEqual(["device_authorization_receipt"], self.contract["public_secrets"])
        for retired in ("profiles", "command_profiles", "live_backend_profiles"):
            self.assertNotIn(retired, self.contract)
        serialized = json.dumps(self.contract, sort_keys=True).casefold()
        for product in ("iptv-android", "iptv-apple", "streamscape-media", "vlc"):
            self.assertNotIn(product, serialized)
        self.assertEqual("exact-family-runtime-receipt", self.contract["owner_authorization"]["mode"])
        self.assertFalse(self.contract["owner_authorization"]["runner_or_secret_is_authorization"])
        self.assertTrue(self.contract["lock_contract"]["cross_run_fencing_claimed"])
        self.assertEqual(
            ["pull_request", "workflow_call", "workflow_dispatch"],
            self.contract["allowed_events"],
        )

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
                with self.assertRaisesRegex(DeviceValidationError, "source_admission_rejected"):
                    request_from_environment(environment, self.contract)

    def test_moved_sha_is_rejected(self) -> None:
        environment = synthetic_environment()
        environment["CIW_DEVICE_EVENT_SHA"] = "b" * 40
        with self.assertRaisesRegex(DeviceValidationError, "source_mismatch"):
            request_from_environment(environment, self.contract)

    def test_same_repository_pull_request_is_trusted_exact_for_reusable_device(self) -> None:
        environment = real_environment(
            repository="StreamScapeTV/example-consumer",
            family="ios",
            capability="terminal-packet",
            host_capacity="apple",
        )
        environment["GITHUB_EVENT_NAME"] = "pull_request"
        environment["CIW_DEVICE_AUTHORIZATION_PRESENT"] = "true"
        request = request_from_environment(environment, self.contract)
        self.assertEqual("trusted-exact", request.source_trust)
        self.assertEqual("pull_request", request.event_name)
        plan = build_plan(self.contract, request)
        self.assertTrue(plan.execution_authorized)
        receipt = json.dumps(
            {
                "packet_version": "device-authorization/1",
                "repository": plan.request.repository,
                "source_sha": plan.request.admitted_sha,
                "device_family": plan.request.family.value,
                "device_capability": plan.request.capability,
                "request_id": plan.request.request_id,
                "not_after_epoch": 2000,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        self.assertRegex(
            validate_authorization_receipt(receipt, plan=plan, now_epoch=1000),
            r"^[0-9a-f]{64}$",
        )

    def test_unapproved_original_caller_event_is_source_rejected(self) -> None:
        environment = real_environment()
        environment["GITHUB_EVENT_NAME"] = "pull_request_target"
        with self.assertRaisesRegex(DeviceValidationError, "source_admission_rejected"):
            request_from_environment(environment, self.contract)

    def test_raw_identifier_runner_and_retired_profile_inputs_are_rejected(self) -> None:
        for name in (
            "INPUT_DEVICE_IDENTIFIER", "INPUT_SERIAL", "INPUT_UDID", "INPUT_RAW_IDENTIFIER",
            "INPUT_RUNS_ON", "INPUT_RUNNER_LABELS", "INPUT_DEVICE_ALIAS", "INPUT_COMMAND_PROFILE",
            "INPUT_SCRIPT_PATH", "INPUT_CONCURRENCY_GROUP", "INPUT_CANCEL_IN_PROGRESS",
        ):
            with self.subTest(name=name):
                environment = synthetic_environment()
                environment[name] = "caller-controlled"
                with self.assertRaisesRegex(DeviceValidationError, "forbidden_input"):
                    request_from_environment(environment, self.contract)

    def test_android_host_capacity_can_select_mobile_or_apple_semantically(self) -> None:
        expected = {
            "mobile": ["linux", "amd64", "mobile"],
            "apple": ["macOS", "ARM64"],
        }
        with patch.dict(os.environ, {"CIW_DEVICE_FOCUSED_TEST": "false"}):
            for host_capacity, selector in expected.items():
                with self.subTest(host_capacity=host_capacity):
                    environment = real_environment(
                        repository="StreamScapeTV/streamscape-media",
                        family="android",
                        capability="full",
                        host_capacity=host_capacity,
                    )
                    plan = build_plan(self.contract, request_from_environment(environment, self.contract))
                    self.assertEqual(host_capacity, plan.request.host_capacity)
                    self.assertEqual(selector, json.loads(_runs_on_json(ROOT, plan)))

    def test_apple_families_reject_mobile_host_capacity(self) -> None:
        for family in ("ios", "tvos"):
            environment = real_environment(family=family, host_capacity="mobile")
            with self.assertRaisesRegex(DeviceValidationError, "device_profile_rejected"):
                build_plan(self.contract, request_from_environment(environment, self.contract))

    def test_bounded_arguments_and_non_secret_environment_are_typed(self) -> None:
        environment = real_environment(
            family="android",
            capability="vlc",
            host_capacity="apple",
            arguments=("vlc-all",),
            caller_environment={"STREAMSCAPE_VLC_PACKET": "all"},
        )
        request = request_from_environment(environment, self.contract)
        self.assertEqual(("vlc-all",), request.arguments)
        self.assertEqual({"STREAMSCAPE_VLC_PACKET": "all"}, dict(request.environment))
        plan = build_plan(self.contract, request)
        packet = plan.packet(runs_on_json='["macOS","ARM64"]')
        self.assertEqual(["vlc-all"], packet["command_plan"]["arguments"])
        self.assertEqual({"STREAMSCAPE_VLC_PACKET": "all"}, packet["command_plan"]["environment"])

    def test_secret_or_authority_environment_keys_are_rejected(self) -> None:
        for key in ("SERVICE_PASSWORD", "BACKEND_TOKEN", "CIW_DEVICE_REQUEST_ID", "GITHUB_TOKEN", "ANDROID_SERIAL"):
            with self.subTest(key=key):
                environment = real_environment()
                environment["INPUT_ENVIRONMENT_JSON"] = json.dumps({key: "value"})
                with self.assertRaisesRegex(DeviceValidationError, "command_profile_rejected"):
                    request_from_environment(environment, self.contract)

    def test_repository_name_no_longer_selects_a_central_profile(self) -> None:
        environment = real_environment(
            repository="Acme/example",
            family="android",
            capability="instrumentation",
            host_capacity="mobile",
        )
        plan = build_plan(self.contract, request_from_environment(environment, self.contract))
        self.assertEqual("Acme/example", plan.request.repository)
        self.assertEqual("android", plan.profile.profile_id)

    def _authorized_real_plan(self):
        environment = real_environment(
            repository="StreamScapeTV/streamscape-media",
            family="android",
            capability="vlc",
            host_capacity="apple",
            test_script_path="scripts/ci/run-central-android-vlc-packet.sh",
            arguments=("vlc-all",),
            caller_environment={"STREAMSCAPE_VLC_PACKET": "all"},
        )
        environment["CIW_DEVICE_AUTHORIZATION_PRESENT"] = "true"
        plan = build_plan(self.contract, request_from_environment(environment, self.contract))
        return environment, plan

    def test_receipt_presence_is_the_only_planning_authority(self) -> None:
        environment = real_environment(family="android", capability="vlc")
        plan = build_plan(self.contract, request_from_environment(environment, self.contract))
        self.assertFalse(plan.execution_authorized)
        self.assertEqual("physical_authorization_required", plan.authorization_failure)
        _environment, authorized = self._authorized_real_plan()
        self.assertTrue(authorized.execution_authorized)
        self.assertEqual("", authorized.authorization_failure)

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
            validate_authorization_receipt(json.dumps(wrong, sort_keys=True, separators=(",", ":")), plan=plan, now_epoch=1000)
        with self.assertRaisesRegex(DeviceValidationError, "authorization_rejected"):
            validate_authorization_receipt(raw, plan=plan, now_epoch=2001)
        noncanonical = json.dumps(receipt, sort_keys=True)
        with self.assertRaisesRegex(DeviceValidationError, "authorization_rejected"):
            validate_authorization_receipt(noncanonical, plan=plan, now_epoch=1000)

    def test_concurrency_is_generic_and_contract_owned(self) -> None:
        environment = real_environment(family="android", capability="vlc", host_capacity="apple")
        plan = build_plan(self.contract, request_from_environment(environment, self.contract))
        self.assertEqual("device-validation-android-vlc-apple", plan.concurrency_group)
        packet = plan.packet(runs_on_json='["macOS","ARM64"]')
        self.assertFalse(packet["cancel_in_progress"])
        self.assertEqual(
            ["device_family", "device_capability", "host_capacity"],
            self.contract["serialization_contract"]["group_scope"],
        )

    def test_typed_plan_exact_replay_and_command_tamper_rejection(self) -> None:
        environment, plan = self._authorized_real_plan()
        outputs = plan.planning_outputs(runs_on_json='["macOS","ARM64"]')
        packet = validate_typed_plan(
            outputs["validated_plan"], outputs["validated_plan_sha256"],
            contract=self.contract, environment=environment,
        )
        self.assertEqual(plan.concurrency_group, packet["concurrency_group"])
        tampered = json.loads(outputs["validated_plan"])
        tampered["command_plan"]["arguments"] = ["different"]
        raw = json.dumps(tampered, sort_keys=True, separators=(",", ":"))
        digest = hashlib.sha256(raw.encode()).hexdigest()
        with self.assertRaisesRegex(DeviceValidationError, "typed_plan_rejected"):
            validate_typed_plan(raw, digest, contract=self.contract, environment=environment)
        with self.assertRaisesRegex(DeviceValidationError, "typed_plan_hash_mismatch"):
            validate_typed_plan(outputs["validated_plan"], "0" * 64, contract=self.contract, environment=environment)

    def test_authorized_typed_plan_rebuilds_runtime_receipt_presence(self) -> None:
        environment, plan = self._authorized_real_plan()
        outputs = plan.planning_outputs(runs_on_json='["macOS","ARM64"]')
        validate_typed_plan(
            outputs["validated_plan"], outputs["validated_plan_sha256"],
            contract=self.contract, environment=environment,
        )
        missing = dict(environment)
        missing["CIW_DEVICE_AUTHORIZATION_PRESENT"] = "false"
        with self.assertRaisesRegex(DeviceValidationError, "typed_plan_rejected"):
            validate_typed_plan(
                outputs["validated_plan"], outputs["validated_plan_sha256"],
                contract=self.contract, environment=missing,
            )


if __name__ == "__main__":
    unittest.main()
