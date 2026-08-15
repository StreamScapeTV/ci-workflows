from __future__ import annotations

import json
import unittest
from pathlib import Path

from ci_workflows import android_contract
from ci_workflows.android_types import AndroidValidationError, AndroidValidationRequest


ROOT = Path(__file__).resolve().parents[1]
MEDIA_REPOSITORY = "StreamScapeTV/streamscape-media"
TASK_PROFILE = "media-tvos-source-regressions"
SCRIPT_PATH = "scripts/ci/test-tvos-source-regressions.sh"


class AndroidMediaTvOSSourceRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.contract = android_contract.load_android_contract(ROOT)

    def request(self, **overrides: object) -> AndroidValidationRequest:
        values: dict[str, object] = {
            "repository": MEDIA_REPOSITORY,
            "admitted_sha": "a" * 40,
            "validation_profile": "consumer-script",
            "task_profile": TASK_PROFILE,
            "working_directory": ".",
            "gradle_wrapper_path": "android/gradlew",
            "targeted_test_selector": None,
            "consumer_script_profile": TASK_PROFILE,
            "private_dependency_contract_id": None,
            "private_dependency_sha": None,
            "artifact_exception_id": None,
            "device_family": None,
            "device_request_id": None,
            "source_trust": "trusted-exact",
        }
        values.update(overrides)
        return AndroidValidationRequest(**values)  # type: ignore[arg-type]

    def test_exact_media_task_resolves_to_existing_linux_mobile_contract(self) -> None:
        plan = android_contract.resolve_validation_plan(self.contract, self.request())

        self.assertEqual(plan.repository, MEDIA_REPOSITORY)
        self.assertEqual(plan.validation_profile, "consumer-script")
        self.assertEqual(plan.task_profile, TASK_PROFILE)
        self.assertEqual(plan.planner_runner_profile, "portable")
        self.assertEqual(plan.runner_profile, "mobile")
        self.assertEqual(plan.timeout_minutes, 90)
        self.assertEqual(plan.output_mode, "consumer-owned")
        self.assertEqual(plan.consumer_script_path, SCRIPT_PATH)
        self.assertEqual(len(plan.commands), 1)
        self.assertEqual(plan.commands[0].stage, "consumer-script")
        self.assertEqual(plan.commands[0].argv, ("bash", SCRIPT_PATH))
        self.assertFalse(plan.requires_private_dependency)
        self.assertIsNone(plan.private_dependency_contract_id)
        self.assertIsNone(plan.artifact_exception_id)
        self.assertIsNone(plan.device_family)
        self.assertIsNone(plan.device_request_id)
        self.assertEqual(plan.expected_debug_outputs, ())
        self.assertIn(SCRIPT_PATH, plan.protected_paths)

    def test_caller_cannot_substitute_another_script_profile(self) -> None:
        with self.assertRaises(AndroidValidationError) as caught:
            android_contract.resolve_validation_plan(
                self.contract,
                self.request(consumer_script_profile="media-android-build-script"),
            )
        self.assertEqual(caught.exception.code, "task_profile_rejected")

    def test_task_rejects_private_dependency_authority(self) -> None:
        with self.assertRaises(AndroidValidationError) as caught:
            android_contract.resolve_validation_plan(
                self.contract,
                self.request(
                    private_dependency_contract_id="streamscape-media-android-v1",
                    private_dependency_sha="85b3c7ed9711fa6ac53059e5d3e474d791c45d26",
                ),
            )
        self.assertEqual(caught.exception.code, "private_dependency_rejected")

    def test_task_is_source_only_and_contains_no_apple_or_device_authority(self) -> None:
        task = self.contract["consumers"][MEDIA_REPOSITORY]["tasks"][TASK_PROFILE]
        serialized = json.dumps(task, sort_keys=True).casefold()

        self.assertEqual(
            task["commands"],
            [{"stage": "consumer-script", "argv": ["bash", SCRIPT_PATH]}],
        )
        self.assertEqual(task["consumer_script_path"], SCRIPT_PATH)
        for forbidden in (
            "xcode",
            "simctl",
            "physical-device",
            "signing",
            "provision",
            "mpv",
            "vlc",
            "adb",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, serialized)


if __name__ == "__main__":
    unittest.main()
