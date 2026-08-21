from __future__ import annotations

from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_TOKEN_ENV_FORWARD = "CIW_MAVEN_PACKAGE_READ_TOKEN: ${{ secrets.maven_package_read_token }}"
PACKAGE_TOKEN_UNKNOWN_INPUT_FORWARD = "maven_package_read_token: ${{ secrets.maven_package_read_token }}"

CASES = (
    (
        ".github/workflows/reusable-android.yml",
        "StreamScapeTV/ci-workflows/actions/validate-android",
        "8eaa37ad0fe3231b202e878b26f66aa23753e38a",
        "issue #373 compile Gradle isolation checkpoint",
    ),
    (
        ".github/workflows/reusable-android-live-service.yml",
        "StreamScapeTV/ci-workflows/actions/validate-android-live-service",
        "2ecbe22ac6d10aa25d79bc046cc205e4df1e08cc",
        "issue #338 Android completion checkpoint",
    ),
    (
        ".github/workflows/reusable-android-release.yml",
        "StreamScapeTV/ci-workflows/actions/validate-android-release",
        "2ecbe22ac6d10aa25d79bc046cc205e4df1e08cc",
        "issue #338 Android completion checkpoint",
    ),
)


def execute_block(source: str) -> str:
    match = re.search(
        r"(?ms)^\s*- id: execute\n(?P<body>.*?)(?=^\s*- id: [A-Za-z0-9_-]+\n|\Z)",
        source,
    )
    if match is None:
        raise AssertionError("workflow has no execute step")
    return match.group(0)


class AndroidPackageTokenActionPinTest(unittest.TestCase):
    def test_package_token_is_forwarded_only_to_execution_without_advancing_action_pins(self) -> None:
        for workflow_path, action_ref, checkpoint, release in CASES:
            with self.subTest(workflow=workflow_path):
                workflow = (ROOT / workflow_path).read_text(encoding="utf-8")
                execute = execute_block(workflow)

                self.assertIn(
                    f"uses: {action_ref}@{checkpoint} # {release}",
                    execute,
                )
                self.assertIn(PACKAGE_TOKEN_ENV_FORWARD, execute)
                self.assertEqual(1, workflow.count(PACKAGE_TOKEN_ENV_FORWARD))
                self.assertNotIn(PACKAGE_TOKEN_UNKNOWN_INPUT_FORWARD, workflow)

    def test_package_token_does_not_reach_plan_prebuild_cleanup_residue_or_evidence(self) -> None:
        for workflow_path, _, _, _ in CASES:
            with self.subTest(workflow=workflow_path):
                workflow = (ROOT / workflow_path).read_text(encoding="utf-8")
                execute = execute_block(workflow)
                outside_execute = workflow.replace(execute, "", 1)
                self.assertNotIn(PACKAGE_TOKEN_ENV_FORWARD, outside_execute)


if __name__ == "__main__":
    unittest.main()
