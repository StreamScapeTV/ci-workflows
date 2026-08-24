from __future__ import annotations

import json
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
ROUTINE_CHECKPOINT = "91e5ba5af11ec717f829000edad062c664fb86f7"
ROUTINE_RELEASE = "issue #534 prefix-isolated protected-full checkpoint"
COMPLETION_CHECKPOINT = "68a6450d6576e0744969cd170cc581856a44312a"
COMPLETION_RELEASE = "issue #443 inherited package credential checkpoint"
PACKAGE_TOKEN_ENV_FORWARD = "CIW_MAVEN_PACKAGE_READ_TOKEN: ${{ secrets.maven_package_read_token }}"
PACKAGE_TOKEN_UNKNOWN_INPUT_FORWARD = "maven_package_read_token: ${{ secrets.maven_package_read_token }}"

CASES = (
    (
        ".github/workflows/reusable-android.yml",
        "StreamScapeTV/ci-workflows/actions/validate-android",
        "actions/validate-android/action.yml",
        ROUTINE_CHECKPOINT,
        ROUTINE_RELEASE,
    ),
    (
        ".github/workflows/reusable-android-live-service.yml",
        "StreamScapeTV/ci-workflows/actions/validate-android-live-service",
        "actions/validate-android-live-service/action.yml",
        COMPLETION_CHECKPOINT,
        COMPLETION_RELEASE,
    ),
    (
        ".github/workflows/reusable-android-release.yml",
        "StreamScapeTV/ci-workflows/actions/validate-android-release",
        "actions/validate-android-release/action.yml",
        COMPLETION_CHECKPOINT,
        COMPLETION_RELEASE,
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
        for workflow_path, action_ref, _, checkpoint, release in CASES:
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
                self.assertIn(action_ref, locked)
                self.assertEqual(checkpoint, locked[action_ref]["sha"])
                self.assertEqual(release, locked[action_ref]["release"])
                self.assertEqual("composite", locked[action_ref]["runtime"])
                self.assertEqual(
                    f"https://github.com/StreamScapeTV/ci-workflows/tree/{checkpoint}/actions/{action_ref.rsplit('/', 1)[1]}",
                    locked[action_ref]["source"],
                )

    def test_composite_actions_do_not_shadow_inherited_package_token(self) -> None:
        for _, _, action_path, _, _ in CASES:
            with self.subTest(action=action_path):
                source = (ROOT / action_path).read_text(encoding="utf-8")
                self.assertNotIn("maven_package_read_token", source)
                self.assertNotIn("CIW_MAVEN_PACKAGE_READ_TOKEN", source)

    def test_package_token_does_not_reach_plan_prebuild_cleanup_residue_or_evidence(self) -> None:
        for workflow_path, _, _, _, _ in CASES:
            with self.subTest(workflow=workflow_path):
                workflow = (ROOT / workflow_path).read_text(encoding="utf-8")
                execute = execute_block(workflow)
                outside_execute = workflow.replace(execute, "", 1)
                self.assertNotIn(PACKAGE_TOKEN_ENV_FORWARD, outside_execute)


if __name__ == "__main__":
    unittest.main()
