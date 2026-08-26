from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GUIDE = ROOT / "docs/architecture/node-validation.md"
WORKFLOW_GUIDE = ROOT / "docs/workflows/node.md"
WORKFLOWS = (
    ROOT / ".github/workflows/reusable-node.yml",
    ROOT / ".github/workflows/reusable-python.yml",
    ROOT / ".github/workflows/reusable-android.yml",
    ROOT / ".github/workflows/reusable-flutter.yml",
)
CURRENT_PRIVATE_ACTION = re.compile(
    r"uses:\s+StreamScapeTV/ci-workflows/actions/[A-Za-z0-9._/-]+@main\b"
)


class NodeArchitectureDocumentationTests(unittest.TestCase):
    def test_implemented_validators_use_current_private_actions_not_central_clone(self) -> None:
        for workflow in WORKFLOWS:
            with self.subTest(workflow=workflow.name):
                source = workflow.read_text(encoding="utf-8")
                self.assertNotIn("job.workflow_repository", source)
                self.assertNotIn("job.workflow_sha", source)
                self.assertRegex(source, CURRENT_PRIVATE_ACTION)

    def test_node_guide_describes_current_shared_private_reuse_model(self) -> None:
        source = GUIDE.read_text(encoding="utf-8")
        self.assertIn(
            "Node, Python, Android, and Flutter consume the current shared Central library through first-party actions on the active `@main` channel.",
            source,
        )
        self.assertIn("These helpers are not independently versioned components", source)
        self.assertNotIn("contracts/action-tool-lock.json", source)
        self.assertNotIn("reviewed immutable private-action distribution model", source)
        self.assertNotIn(
            "Android/Flutter/Python reusable workflows that still use an exact `job.workflow_repository` / `job.workflow_sha` checkout",
            source,
        )

    def test_node_workflow_guide_matches_live_main_library_and_setup_release(self) -> None:
        source = WORKFLOW_GUIDE.read_text(encoding="utf-8")
        for helper in (
            "actions/validate-node@main",
            "actions/exact-checkout@main",
            "actions/prepare-workspace@main",
            "actions/render-evidence@main",
            "actions/cleanup-workspace@main",
        ):
            self.assertIn(helper, source)
        self.assertIn("actions/setup-node@v6.5.0", source)
        self.assertIn("Private source, credentials, detailed command output", source)
        self.assertNotIn("contracts/action-tool-lock.json", source)
        self.assertNotIn("Immutable Central helper reuse", source)
        self.assertNotIn("pinned to a full commit SHA", source)
        self.assertNotIn("zero Actions artifacts", source)


if __name__ == "__main__":
    unittest.main()
