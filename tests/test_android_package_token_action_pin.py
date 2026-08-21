from __future__ import annotations

from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
PACKAGE_TOKEN_ACTION_CHECKPOINT = "8d8f72c41901e1f1d8fd257e6f7ce71d6c9a0bef"
PACKAGE_TOKEN_SECRET_FORWARD = "maven_package_read_token: ${{ secrets.maven_package_read_token }}"
PACKAGE_TOKEN_ENV_FORWARD = "CIW_MAVEN_PACKAGE_READ_TOKEN: ${{ inputs.maven_package_read_token }}"

CASES = (
    (
        ".github/workflows/reusable-android.yml",
        "StreamScapeTV/ci-workflows/actions/validate-android",
        "actions/validate-android/action.yml",
    ),
    (
        ".github/workflows/reusable-android-live-service.yml",
        "StreamScapeTV/ci-workflows/actions/validate-android-live-service",
        "actions/validate-android-live-service/action.yml",
    ),
    (
        ".github/workflows/reusable-android-release.yml",
        "StreamScapeTV/ci-workflows/actions/validate-android-release",
        "actions/validate-android-release/action.yml",
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
    def test_package_token_is_forwarded_only_to_execution_at_compatible_checkpoint(self) -> None:
        for workflow_path, action_ref, action_path in CASES:
            with self.subTest(workflow=workflow_path):
                workflow = (ROOT / workflow_path).read_text(encoding="utf-8")
                execute = execute_block(workflow)
                action = (ROOT / action_path).read_text(encoding="utf-8")

                self.assertIn(
                    f"uses: {action_ref}@{PACKAGE_TOKEN_ACTION_CHECKPOINT} # issue #443 package-read execution checkpoint",
                    execute,
                )
                self.assertIn(PACKAGE_TOKEN_SECRET_FORWARD, execute)
                self.assertEqual(1, workflow.count(PACKAGE_TOKEN_SECRET_FORWARD))

                self.assertRegex(action, r"(?m)^  maven_package_read_token:\s*$")
                self.assertIn(PACKAGE_TOKEN_ENV_FORWARD, action)

    def test_package_token_does_not_reach_plan_cleanup_or_residue_steps(self) -> None:
        for workflow_path, _, _ in CASES:
            with self.subTest(workflow=workflow_path):
                workflow = (ROOT / workflow_path).read_text(encoding="utf-8")
                execute = execute_block(workflow)
                outside_execute = workflow.replace(execute, "", 1)
                self.assertNotIn(PACKAGE_TOKEN_SECRET_FORWARD, outside_execute)


if __name__ == "__main__":
    unittest.main()
