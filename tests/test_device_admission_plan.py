from __future__ import annotations

import json
import unittest
from dataclasses import replace
from pathlib import Path

from ci_workflows.device_contract import build_plan, load_device_contract, load_evidence_contract, request_from_environment, validate_typed_plan
from ci_workflows.device_execution import *  # noqa: F401,F403
from ci_workflows.device_types import DeviceFamily, DeviceRecord, DeviceValidationError
from device_test_support import FIX, ROOT, SHA, real_environment, synthetic_environment

class AdmissionAndPlanTests(unittest.TestCase):
    def setUp(self) -> None:
        self.contract = load_device_contract(ROOT)

    def test_contract_identity_and_authorization_boundary(self) -> None:
        self.assertEqual("1.1.0", self.contract["contract_version"])
        self.assertEqual([], self.contract["owner_authorization"]["authorized_families"])
        self.assertFalse(self.contract["owner_authorization"]["runner_or_secret_is_authorization"])
        self.assertFalse(self.contract["lock_contract"]["cross_run_fencing_claimed"])
        self.assertFalse(self.contract["lock_contract"]["agent_state_transport_used"])

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
        for alias in ("not-reviewed", "synthetic-primary-${{github.sha}}", "../../raw"):
            with self.subTest(alias=alias):
                environment = synthetic_environment()
                environment["INPUT_DEVICE_ALIAS"] = alias
                try:
                    request = request_from_environment(environment, self.contract)
                except DeviceValidationError as error:
                    self.assertIn(error.code, {"device_profile_rejected", "invalid_input"})
                else:
                    with self.assertRaisesRegex(DeviceValidationError, "device_profile_rejected"):
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

    def test_real_profile_and_secret_never_authorize_execution(self) -> None:
        environment = real_environment(
            repository="StreamScapeTV/iptv-android",
            family="android",
            capability="instrumentation",
            command_profile="iptv-android-device",
            script_path="build.sh",
            alias="acceptance-primary",
            secret=True,
        )
        plan = build_plan(self.contract, request_from_environment(environment, self.contract))
        self.assertFalse(plan.execution_authorized)
        self.assertEqual("physical_authorization_required", plan.authorization_failure)

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
        plan = build_plan(self.contract, request_from_environment(environment, self.contract))
        self.assertFalse(plan.execution_authorized)

    def test_typed_plan_exact_replay_and_tamper_rejection(self) -> None:
        environment = synthetic_environment()
        plan = build_plan(self.contract, request_from_environment(environment, self.contract))
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
            validate_typed_plan(raw, digest, contract=self.contract, environment=environment)
        with self.assertRaisesRegex(DeviceValidationError, "typed_plan_hash_mismatch"):
            validate_typed_plan(outputs["validated_plan"], "0" * 64, contract=self.contract, environment=environment)

    def test_typed_plan_rebuilds_contract_owned_profile_fields(self) -> None:
        environment = synthetic_environment()
        plan = build_plan(self.contract, request_from_environment(environment, self.contract))
        outputs = plan.planning_outputs(runs_on_json='["mobile"]')
        tampered = json.loads(outputs["validated_plan"])
        tampered["script_path"] = "build.sh"
        raw = json.dumps(tampered, sort_keys=True, separators=(",", ":"))
        digest = __import__("hashlib").sha256(raw.encode()).hexdigest()
        with self.assertRaisesRegex(DeviceValidationError, "device_profile_rejected"):
            validate_typed_plan(raw, digest, contract=self.contract, environment=environment)
