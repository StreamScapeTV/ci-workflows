from __future__ import annotations

import json
import unittest

from ci_workflows import apple_plan_guard
from ci_workflows.apple_types import AppleValidationError


def protected_stage(platform: str, operation: str) -> dict[str, object]:
    return {
        "platform": platform,
        "operation": operation,
        "xcodebuild_arguments": [],
        "cleanup_paths": [],
    }


def raw_plan(*stages: dict[str, object]) -> str:
    return json.dumps({"stages": list(stages)}, separators=(",", ":"))


class AppleProtectedFullRuntimePolicyTests(unittest.TestCase):
    def test_ios_and_tvos_runtime_tests_are_rejected_at_public_guard(self) -> None:
        for platform in ("ios", "tvos"):
            with self.subTest(platform=platform):
                with self.assertRaisesRegex(AppleValidationError, "forbidden_operation"):
                    apple_plan_guard.validate_protected_full_plan_json(
                        raw_plan(protected_stage(platform, "test"))
                    )

    def test_ios_and_tvos_build_for_testing_remain_non_runtime_requests(self) -> None:
        apple_plan_guard.validate_protected_full_plan_json(
            raw_plan(
                protected_stage("ios", "build-for-testing"),
                protected_stage("tvos", "build-for-testing"),
            )
        )

    def test_macos_host_test_remains_allowed(self) -> None:
        apple_plan_guard.validate_protected_full_plan_json(
            raw_plan(protected_stage("macos", "test"))
        )

    def test_compile_only_build_remains_allowed_for_runtime_platforms(self) -> None:
        apple_plan_guard.validate_protected_full_plan_json(
            raw_plan(
                protected_stage("ios", "build"),
                protected_stage("tvos", "build"),
            )
        )


if __name__ == "__main__":
    unittest.main()
