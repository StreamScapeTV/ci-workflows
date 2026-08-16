from __future__ import annotations

import unittest

from ci_workflows.device_contract import build_plan, load_device_contract, request_from_environment
from ci_workflows.device_types import DeviceValidationError
from device_test_support import ROOT, real_environment

COMMAND_PROFILE = "streamscape-media-ios-central-device"
TEST_SCRIPT = "scripts/ci/run-ios-central-device-test.sh"
RETAINED_PATH = ".tmp/ci-retained/ios-central-device-evidence.json"

PACKETS = {
    "native-video-output-frame-rates": (
        "streamscape-media-ios-frame-rates",
        (379, 527),
    ),
    "native-video-output-geometry": (
        "streamscape-media-ios-geometry",
        (380, 529),
    ),
    "native-video-output-dynamic-range-evidence": (
        "streamscape-media-ios-dynamic-range",
        (174, 530),
    ),
    "native-switching-policy-preservation": (
        "streamscape-media-ios-switching-policy",
        (358, 528),
    ),
    "native-shared-lifecycle": (
        "streamscape-media-ios-shared-lifecycle",
        (45, 531),
    ),
}


class MediaIOSDeviceProfileTests(unittest.TestCase):
    def setUp(self) -> None:
        self.contract = load_device_contract(ROOT)

    def environment(self, capability: str, issue_number: int) -> dict[str, str]:
        environment = real_environment(
            repository="StreamScapeTV/streamscape-media",
            family="ios",
            capability=capability,
            command_profile=COMMAND_PROFILE,
            script_path=TEST_SCRIPT,
            alias="media-primary",
        )
        environment["INPUT_REQUEST_ID"] = f"issue-{issue_number}-media-ios-packet"
        return environment

    def plan(self, capability: str, issue_number: int, *, authorized: bool = True):
        environment = self.environment(capability, issue_number)
        if authorized:
            environment["CIW_DEVICE_AUTHORIZATION_PRESENT"] = "true"
        request = request_from_environment(environment, self.contract)
        return build_plan(self.contract, request)

    def test_effective_contract_retires_stale_native_failover_shape(self) -> None:
        self.assertNotIn("streamscape-media-ios", self.contract["profiles"])
        self.assertNotIn("streamscape-media-ios-device", self.contract["command_profiles"])
        media_profiles = [
            profile
            for profile in self.contract["profiles"].values()
            if profile["repositories"] == ["StreamScapeTV/streamscape-media"]
            and profile["family"] == "ios"
        ]
        capabilities = {
            capability
            for profile in media_profiles
            for capability in profile["capabilities"]
        }
        self.assertEqual(set(PACKETS), capabilities)
        self.assertNotIn("native-failover", capabilities)

    def test_fixed_command_profile_uses_zero_argv_product_adapter_and_redacted_handoff(self) -> None:
        profile = self.contract["command_profiles"][COMMAND_PROFILE]
        self.assertEqual("scripts/ci/prepare-ios-central-device.sh", profile["prepare_script"])
        self.assertEqual(TEST_SCRIPT, profile["test_script"])
        self.assertEqual(
            "scripts/ci/publish-ios-central-device-evidence.sh",
            profile["evidence_script"],
        )
        self.assertEqual("scripts/ci/cleanup-ios-central-device.sh", profile["cleanup_script"])
        self.assertEqual([], profile["fixed_arguments"])
        self.assertIsNone(profile["live_backend_profile"])
        self.assertEqual(RETAINED_PATH, profile["retained_evidence_path"])
        self.assertEqual("application/json", profile["retained_evidence_media_type"])

    def test_all_five_packet_issue_pairs_resolve_to_exact_apple_profiles(self) -> None:
        for capability, (profile_id, issue_numbers) in PACKETS.items():
            for issue_number in issue_numbers:
                with self.subTest(capability=capability, issue_number=issue_number):
                    plan = self.plan(capability, issue_number)
                    self.assertEqual(profile_id, plan.profile.profile_id)
                    self.assertEqual((issue_numbers[0], issue_numbers[1]), plan.profile.request_issue_numbers)
                    self.assertEqual("ios", plan.request.family.value)
                    self.assertEqual("apple", plan.profile.base_runner_profile)
                    self.assertEqual("media-primary", plan.request.device_alias)
                    self.assertEqual(COMMAND_PROFILE, plan.profile.command_profile.profile_id)
                    self.assertEqual(TEST_SCRIPT, plan.request.script_path)
                    self.assertTrue(plan.execution_authorized)
                    self.assertEqual("", plan.authorization_failure)

    def test_packet_capability_cannot_be_relabelled_under_another_issue(self) -> None:
        environment = self.environment("native-video-output-frame-rates", 529)
        environment["CIW_DEVICE_AUTHORIZATION_PRESENT"] = "true"
        request = request_from_environment(environment, self.contract)
        with self.assertRaises(DeviceValidationError) as caught:
            build_plan(self.contract, request)
        self.assertEqual("device_profile_rejected", caught.exception.code)

    def test_arbitrary_profile_script_alias_and_capability_substitution_fail_closed(self) -> None:
        mutations = (
            ("INPUT_COMMAND_PROFILE", "streamscape-media-tvos-device"),
            ("INPUT_SCRIPT_PATH", "scripts/ci/run-ios-central-device-packet.sh"),
            ("INPUT_DEVICE_ALIAS", "acceptance-primary"),
            ("INPUT_DEVICE_CAPABILITY", "avfoundation"),
        )
        for key, value in mutations:
            with self.subTest(key=key, value=value):
                environment = self.environment("native-video-output-frame-rates", 527)
                environment["CIW_DEVICE_AUTHORIZATION_PRESENT"] = "true"
                environment[key] = value
                request = request_from_environment(environment, self.contract)
                with self.assertRaises(DeviceValidationError) as caught:
                    build_plan(self.contract, request)
                self.assertEqual("device_profile_rejected", caught.exception.code)

    def test_owner_authorization_and_production_fencing_remain_mandatory(self) -> None:
        denied = self.plan("native-video-output-geometry", 529, authorized=False)
        self.assertFalse(denied.execution_authorized)
        self.assertEqual("physical_authorization_required", denied.authorization_failure)
        self.assertEqual(
            "device-lock/1:posix-shared-root-v1",
            self.contract["lock_contract"]["production_adapter"],
        )
        self.assertTrue(self.contract["lock_contract"]["cross_run_fencing_claimed"])
        self.assertFalse(self.contract["serialization_contract"]["cancel_in_progress"])


if __name__ == "__main__":
    unittest.main()
