from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ci_workflows import apple
from ci_workflows.apple_contract import build_plan
from ci_workflows.apple_execution import SimulatorLease, _xcodebuild_argv
from ci_workflows.apple_types import AppleProfile, AppleValidationRequest


ROOT = Path(__file__).resolve().parents[1]
SHA = "a" * 40


class AppleReleaseProfilesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.contract = apple.load_apple_contract(ROOT)

    def request(
        self,
        profile: AppleProfile,
        *,
        consumer: str = "iptv-apple-release",
        repository: str = "StreamScapeTV/iptv-apple",
    ) -> AppleValidationRequest:
        return AppleValidationRequest(
            repository=repository,
            admitted_sha=SHA,
            consumer_contract=consumer,
            validation_profile=profile,
            source_trust="trusted-exact",
        )

    def test_existing_debug_iptv_mapping_remains_compatible(self) -> None:
        expected = {
            "ios-simulator": "iptv-ios-simulator",
            "tvos-simulator": "iptv-tvos-simulator",
            "macos": "iptv-macos",
        }
        mapping = self.contract["consumer_contracts"]["iptv-apple"]["profiles"]
        for profile, task_id in expected.items():
            self.assertEqual(mapping[profile], task_id)
            task = self.contract["tasks"][task_id]
            self.assertEqual(task["container"]["configuration"], "Debug")
            self.assertEqual(task["commands"][0]["action"], "build-for-testing")

    def test_release_consumer_maps_exact_three_platform_tasks(self) -> None:
        expected = {
            "ios-simulator": "iptv-ios-release",
            "tvos-simulator": "iptv-tvos-release",
            "macos": "iptv-macos-release",
        }
        consumer = self.contract["consumer_contracts"]["iptv-apple-release"]
        self.assertEqual(consumer["repository"], "StreamScapeTV/iptv-apple")
        self.assertEqual(consumer["profiles"], expected)
        for task_id in expected.values():
            task = self.contract["tasks"][task_id]
            self.assertEqual(task["container"]["configuration"], "Release")
            self.assertEqual(task["commands"][0]["action"], "build")
            self.assertEqual(task["commands"][0]["fixed_arguments"], [])

    def test_release_plans_share_exact_admitted_sha_and_apple_runner(self) -> None:
        plans = [
            build_plan(self.contract, self.request(AppleProfile.IOS_SIMULATOR)),
            build_plan(self.contract, self.request(AppleProfile.TVOS_SIMULATOR)),
            build_plan(self.contract, self.request(AppleProfile.MACOS)),
        ]
        self.assertEqual(
            [plan.task_profile for plan in plans],
            ["iptv-ios-release", "iptv-tvos-release", "iptv-macos-release"],
        )
        self.assertEqual({plan.request.admitted_sha for plan in plans}, {SHA})
        self.assertEqual({plan.runner_profile.value for plan in plans}, {"apple"})
        self.assertEqual(
            {plan.container.configuration for plan in plans if plan.container},
            {"Release"},
        )
        self.assertEqual(
            {plan.planning_outputs()["source_sha"] for plan in plans},
            {SHA},
        )

    def test_release_consumer_is_repository_bound(self) -> None:
        with self.assertRaisesRegex(
            apple.AppleValidationError,
            "consumer_contract_rejected",
        ):
            build_plan(
                self.contract,
                self.request(
                    AppleProfile.MACOS,
                    repository="StreamScapeTV/ci-workflows",
                ),
            )

    def test_configuration_is_not_a_public_input(self) -> None:
        environment = {
            "GITHUB_REPOSITORY": "StreamScapeTV/iptv-apple",
            "INPUT_ADMITTED_SHA": SHA,
            "INPUT_VALIDATION_PROFILE": "macos",
            "INPUT_COMMAND_PROFILE": "iptv-apple-release",
            "INPUT_PLATFORM": "macos",
            "INPUT_CONFIGURATION": "Release",
        }
        with self.assertRaisesRegex(apple.AppleValidationError, "forbidden_input"):
            apple.request_from_environment(environment, self.contract)
        workflow = (ROOT / ".github/workflows/reusable-apple.yml").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("\n      configuration:\n", workflow)

    def test_conflicting_direct_configuration_is_rejected(self) -> None:
        request = self.request(AppleProfile.MACOS)
        with self.assertRaisesRegex(
            apple.AppleValidationError,
            "configuration_rejected",
        ):
            build_plan(
                self.contract,
                AppleValidationRequest(
                    repository=request.repository,
                    admitted_sha=request.admitted_sha,
                    consumer_contract=request.consumer_contract,
                    validation_profile=request.validation_profile,
                    source_trust=request.source_trust,
                    configuration="Debug",
                ),
            )

    def test_release_xcodebuild_argv_is_unsigned_compile_only(self) -> None:
        plan = build_plan(
            self.contract,
            AppleValidationRequest(
                repository="StreamScapeTV/ci-workflows",
                admitted_sha=SHA,
                consumer_contract="ciw-apple-release-smoke",
                validation_profile=AppleProfile.MACOS,
                source_trust="trusted-pr",
            ),
        )
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            directories = {
                "derived-data": state / "derived-data",
                "swiftpm": state / "swiftpm",
                "result-bundles": state / "result-bundles",
            }
            for path in directories.values():
                path.mkdir(parents=True)
            command = plan.commands[0]
            argv = _xcodebuild_argv(
                plan,
                command,
                ROOT,
                directories,
                None,
            )
        self.assertIn("-configuration", argv)
        self.assertEqual(argv[argv.index("-configuration") + 1], "Release")
        self.assertEqual(argv[-1], "build")
        self.assertIn("CODE_SIGNING_ALLOWED=NO", argv)
        self.assertIn("CODE_SIGNING_REQUIRED=NO", argv)
        self.assertIn("CODE_SIGN_IDENTITY=", argv)
        serialized = " ".join(argv).casefold()
        for forbidden in (
            " archive",
            "exportarchive",
            "provisioning_profile",
            "development_team=",
            "notarytool",
            "app-store",
            "testflight",
        ):
            self.assertNotIn(forbidden, serialized)

    def test_release_simulator_tasks_keep_bounded_destinations(self) -> None:
        lease = SimulatorLease(
            udid="11111111-2222-3333-4444-555555555555",
            destination=(
                "platform=iOS Simulator,id="
                "11111111-2222-3333-4444-555555555555"
            ),
            redacted_identity="sim-test",
            created=True,
        )
        plan = build_plan(
            self.contract,
            AppleValidationRequest(
                repository="StreamScapeTV/ci-workflows",
                admitted_sha=SHA,
                consumer_contract="ciw-apple-release-smoke",
                validation_profile=AppleProfile.IOS_SIMULATOR,
                source_trust="trusted-pr",
            ),
        )
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            directories = {
                "derived-data": state / "derived-data",
                "swiftpm": state / "swiftpm",
                "result-bundles": state / "result-bundles",
            }
            for path in directories.values():
                path.mkdir(parents=True)
            argv = _xcodebuild_argv(
                plan,
                plan.commands[0],
                ROOT,
                directories,
                lease,
            )
        self.assertEqual(argv[argv.index("-configuration") + 1], "Release")
        self.assertEqual(argv[argv.index("-destination") + 1], lease.destination)
        self.assertEqual(argv[-1], "build")


if __name__ == "__main__":
    unittest.main()
