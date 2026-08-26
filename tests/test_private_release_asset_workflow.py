from __future__ import annotations

from pathlib import Path
import unittest

from ci_workflows.validation_model import load_actions_yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/reusable-apple.yml"


class PrivateReleaseAssetWorkflowTests(unittest.TestCase):
    def test_release_asset_uses_current_internal_helper_without_public_api_growth(self) -> None:
        document = load_actions_yaml(WORKFLOW, ROOT).data
        call = document["on"]["workflow_call"]
        inputs = set(call["inputs"])
        secrets = set(call["secrets"])
        self.assertNotIn("private_release_asset", " ".join(sorted(inputs)))
        self.assertEqual(
            secrets,
            {"repository_app_id", "repository_app_private_key", "private_dependency_token"},
        )

        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertEqual(text.count("actions/materialize-private-release-asset@main"), 4)
        self.assertIn("config_workflow_key: validation.apple", text)
        self.assertIn("PRIVATE_RELEASE_ASSET_TOKEN:", text)
        self.assertIn("steps.release_token.outputs.token || secrets.private_dependency_token", text)
        self.assertIn('phase: "plan"', text)
        self.assertIn('phase: "execute"', text)
        self.assertIn('phase: "cleanup"', text)
        self.assertIn('phase: "residue"', text)
        self.assertIn("RELEASE_REQUIRED:", text)
        self.assertIn("RELEASE_CLEANUP_OUTCOME:", text)
        self.assertIn("RELEASE_RESIDUE_OUTCOME:", text)
        self.assertNotIn("upload-artifact", text.lower())

    def test_legacy_apple_executor_phase_counts_remain_one_each(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertEqual(text.count("phase: execute"), 1)
        self.assertEqual(text.count("phase: cleanup"), 1)
        self.assertEqual(text.count("phase: residue"), 1)


if __name__ == "__main__":
    unittest.main()
