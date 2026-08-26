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


class AndroidPackageTokenRuntimeTest(unittest.TestCase):
    def test_package_token_reaches_main_runtime_only_at_execution(self) -> None:
        for workflow_path, action_ref, _ in CASES:
            with self.subTest(workflow=workflow_path):
                workflow = (ROOT / workflow_path).read_text(encoding="utf-8")
                execute = execute_block(workflow)

                self.assertIn(f"uses: {action_ref}@main", execute)
                self.assertIn(PACKAGE_TOKEN_ENV_FORWARD, execute)
                self.assertEqual(1, workflow.count(PACKAGE_TOKEN_ENV_FORWARD))
                self.assertNotIn(PACKAGE_TOKEN_UNKNOWN_INPUT_FORWARD, workflow)

    def test_composite_actions_do_not_shadow_inherited_package_token(self) -> None:
        for _, _, action_path in CASES:
            with self.subTest(action=action_path):
                source = (ROOT / action_path).read_text(encoding="utf-8")
                self.assertNotIn("maven_package_read_token", source)
                self.assertNotIn("CIW_MAVEN_PACKAGE_READ_TOKEN", source)

    def test_package_token_does_not_reach_plan_prebuild_cleanup_residue_or_evidence(self) -> None:
        for workflow_path, _, _ in CASES:
            with self.subTest(workflow=workflow_path):
                workflow = (ROOT / workflow_path).read_text(encoding="utf-8")
                execute = execute_block(workflow)
                outside_execute = workflow.replace(execute, "", 1)
                self.assertNotIn(PACKAGE_TOKEN_ENV_FORWARD, outside_execute)


if __name__ == "__main__":
    unittest.main()
