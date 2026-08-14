from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GUIDE = ROOT / "docs/architecture/node-validation.md"
WORKFLOWS = (
    ROOT / ".github/workflows/reusable-node.yml",
    ROOT / ".github/workflows/reusable-python.yml",
    ROOT / ".github/workflows/reusable-android.yml",
    ROOT / ".github/workflows/reusable-flutter.yml",
)
IMMUTABLE_PRIVATE_ACTION = re.compile(
    r"uses:\s+StreamScapeTV/ci-workflows/actions/[A-Za-z0-9._/-]+@[0-9a-f]{40}\b"
)


class NodeArchitectureDocumentationTests(unittest.TestCase):
    def test_implemented_validators_use_immutable_private_actions_not_central_clone(self) -> None:
        for workflow in WORKFLOWS:
            with self.subTest(workflow=workflow.name):
                source = workflow.read_text(encoding="utf-8")
                self.assertNotIn("job.workflow_repository", source)
                self.assertNotIn("job.workflow_sha", source)
                self.assertRegex(source, IMMUTABLE_PRIVATE_ACTION)

    def test_node_guide_describes_current_shared_private_reuse_model(self) -> None:
        source = GUIDE.read_text(encoding="utf-8")
        self.assertIn(
            "Node, Python, Android, and Flutter now share this reviewed immutable private-action distribution model.",
            source,
        )
        self.assertNotIn(
            "Android/Flutter/Python reusable workflows that still use an exact `job.workflow_repository` / `job.workflow_sha` checkout",
            source,
        )


if __name__ == "__main__":
    unittest.main()
