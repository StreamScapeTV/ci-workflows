from __future__ import annotations

import json
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
CHECKPOINT = "b8b6f7ad2e8ea8b37d12a73df19ed02ff497f971"
RELEASE = "issue #443 package credential process checkpoint"
PACKAGE_TOKEN_ENV_FORWARD = "CIW_MAVEN_PACKAGE_READ_TOKEN: ${{ secrets.maven_package_read_token }}"
PACKAGE_TOKEN_UNKNOWN_INPUT_FORWARD = "maven_package_read_token: ${{ secrets.maven_package_read_token }}"

CASES = (
    (
        ".github/workflows/reusable-android.yml",
        "StreamScapeTV/ci-workflows/actions/validate-android",
    ),
    (
        ".github/workflows/reusable-android-live-service.yml",
        "StreamScapeTV/ci-workflows/actions/validate-android-live-service",
    ),
    (
        ".github/workflows/reusable-android-release.yml",
        "StreamScapeTV/ci-workflows/actions/validate-android-release",
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
    @staticmethod
    def locked_actions() -> dict[str, dict[str, str]]:
        payload = json.loads((ROOT / "contracts/action-tool-lock.json").read_text(encoding="utf-8"))
        return {item["uses"]: item for item in payload["third_party_actions"]}

    def test_package_token_uses_reviewed_runtime_checkpoint_only_at_execution(self) -> None:
        locked = self.locked_actions()
        for workflow_path, action_ref in CASES:
            with self.subTest(workflow=workflow_path):
                workflow = (ROOT / workflow_path).read_text(encoding="utf-8")
                execute = execute_block(workflow)

                self.assertIn(
                    f"uses: {action_ref}@{CHECKPOINT} # {RELEASE}",
                    execute,
                )
                self.assertIn(PACKAGE_TOKEN_ENV_FORWARD, execute)
                self.assertEqual(1, workflow.count(PACKAGE_TOKEN_ENV_FORWARD))
                self.assertNotIn(PACKAGE_TOKEN_UNKNOWN_INPUT_FORWARD, workflow)
                self.assertIn(action_ref, locked)
                self.assertEqual(CHECKPOINT, locked[action_ref]["sha"])
                self.assertEqual(RELEASE, locked[action_ref]["release"])
                self.assertEqual("composite", locked[action_ref]["runtime"])
                self.assertEqual(
                    f"https://github.com/StreamScapeTV/ci-workflows/tree/{CHECKPOINT}/actions/{action_ref.rsplit('/', 1)[1]}",
                    locked[action_ref]["source"],
                )

    def test_package_token_does_not_reach_plan_prebuild_cleanup_residue_or_evidence(self) -> None:
        for workflow_path, _ in CASES:
            with self.subTest(workflow=workflow_path):
                workflow = (ROOT / workflow_path).read_text(encoding="utf-8")
                execute = execute_block(workflow)
                outside_execute = workflow.replace(execute, "", 1)
                self.assertNotIn(PACKAGE_TOKEN_ENV_FORWARD, outside_execute)


if __name__ == "__main__":
    unittest.main()
