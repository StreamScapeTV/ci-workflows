from __future__ import annotations

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ARCHITECTURE_DOC = ROOT / "docs/architecture/android-validation.md"
WORKFLOW_DOC = ROOT / "docs/workflows/android.md"
ACTION_LOCK = ROOT / "contracts/action-tool-lock.json"
WORKFLOW = ROOT / ".github/workflows/reusable-android.yml"

VALIDATE_ANDROID = "StreamScapeTV/ci-workflows/actions/validate-android"
UPLOAD_GRADLE_SEED = "StreamScapeTV/ci-workflows/actions/upload-gradle-seed"
EXACT_CHECKOUT = "StreamScapeTV/ci-workflows/actions/exact-checkout"
PREPARE_WORKSPACE = "StreamScapeTV/ci-workflows/actions/prepare-workspace"
PRIVATE_DEPENDENCY = "StreamScapeTV/ci-workflows/actions/checkout-private-dependency"
RENDER_EVIDENCE = "StreamScapeTV/ci-workflows/actions/render-evidence"
CLEANUP_WORKSPACE = "StreamScapeTV/ci-workflows/actions/cleanup-workspace"


class AndroidArchitectureDocumentationTests(unittest.TestCase):
    def test_helper_guidance_and_workflow_match_action_lock(self) -> None:
        required = {
            VALIDATE_ANDROID,
            UPLOAD_GRADLE_SEED,
            EXACT_CHECKOUT,
            PREPARE_WORKSPACE,
            PRIVATE_DEPENDENCY,
            RENDER_EVIDENCE,
            CLEANUP_WORKSPACE,
        }
        lock = json.loads(ACTION_LOCK.read_text(encoding="utf-8"))
        actions = {
            item["uses"]: item
            for item in lock["third_party_actions"]
            if item["uses"] in required
        }
        guide = ARCHITECTURE_DOC.read_text(encoding="utf-8")
        workflow_guide = WORKFLOW_DOC.read_text(encoding="utf-8")
        workflow = WORKFLOW.read_text(encoding="utf-8")

        self.assertEqual(required, set(actions))
        for action_name, item in actions.items():
            self.assertEqual("composite", item["runtime"])
            self.assertIn(item["sha"], guide)
            self.assertIn(item["release"], guide)
            self.assertIn(f"uses: {action_name}@{item['sha']}", workflow)

        self.assertNotIn("id-token", workflow)
        self.assertNotIn("ACTIONS_ID_TOKEN_REQUEST", workflow)
        self.assertIn("GRADLE_RO_DEP_CACHE", guide)
        self.assertIn("private writable `GRADLE_USER_HOME`", guide)
        self.assertIn("best-effort cache-sync call", guide)
        self.assertIn("does not invoke Gradle again", guide)
        self.assertIn("Registered workspace cleanup always", guide)
        self.assertIn("Central owns this invocation", guide)
        self.assertIn("Central owns this same-executor invocation", workflow_guide)
        self.assertIn("Android product repository", workflow_guide)

        validate_sha = actions[VALIDATE_ANDROID]["sha"]
        foundation_sha = actions[EXACT_CHECKOUT]["sha"]
        sync_sha = actions[UPLOAD_GRADLE_SEED]["sha"]
        self.assertNotEqual(validate_sha, foundation_sha)
        self.assertNotEqual(sync_sha, foundation_sha)


if __name__ == "__main__":
    unittest.main()
