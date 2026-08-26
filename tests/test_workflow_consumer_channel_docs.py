from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_DOCS = ROOT / "docs" / "workflows"
PUBLIC_REUSABLE_CALL = re.compile(
    r"uses:\s+StreamScapeTV/ci-workflows/\.github/workflows/reusable-[^\s`]+@([^\s`]+)"
)


class WorkflowConsumerChannelDocsTests(unittest.TestCase):
    def test_retained_public_consumer_examples_use_active_main_channel(self) -> None:
        guides_with_calls = 0
        for path in sorted(WORKFLOW_DOCS.glob("*.md")):
            source = path.read_text(encoding="utf-8")
            refs = PUBLIC_REUSABLE_CALL.findall(source)
            if not refs:
                continue
            guides_with_calls += 1
            with self.subTest(name=path.name):
                self.assertEqual(set(refs), {"main"})
        self.assertGreaterEqual(guides_with_calls, 1)

    def test_public_main_examples_keep_private_source_boundaries_without_helper_pins(self) -> None:
        flutter = (WORKFLOW_DOCS / "flutter.md").read_text(encoding="utf-8")
        self.assertIn(
            "invoke first-party Central composite actions through the current\nshared-library channel `@main`",
            flutter,
        )
        self.assertIn(
            "Product source\nauthority remains separate: the exact admitted caller SHA is checked out through\n`exact-checkout@main`",
            flutter,
        )
        self.assertIn("private source", flutter.lower())
        self.assertNotIn("exact immutable SHAs", flutter)
        self.assertNotIn("full-SHA references", flutter)


if __name__ == "__main__":
    unittest.main()
