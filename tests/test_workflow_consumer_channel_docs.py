from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_DOCS = ROOT / "docs" / "workflows"
BOUNDED_GUIDES = (
    "flutter.md",
    "oci-build.md",
    "oci-publish.md",
)
PUBLIC_REUSABLE_CALL = re.compile(
    r"uses:\s+StreamScapeTV/ci-workflows/\.github/workflows/reusable-[^\s`]+@([^\s`]+)"
)
ACTIVE_CHANNEL_GUIDANCE = (
    "During active development/bootstrap, repository consumers call public reusable\n"
    "`ci-workflows` workflows at `@main`. Full-SHA and stable-tag references remain\n"
    "supported, but they are not the current default consumer channel"
)


class WorkflowConsumerChannelDocsTests(unittest.TestCase):
    def test_bounded_public_consumer_examples_use_active_main_channel(self) -> None:
        for name in BOUNDED_GUIDES:
            with self.subTest(name=name):
                source = (WORKFLOW_DOCS / name).read_text(encoding="utf-8")
                refs = PUBLIC_REUSABLE_CALL.findall(source)
                self.assertGreaterEqual(len(refs), 1)
                self.assertEqual(set(refs), {"main"})
                self.assertIn(ACTIVE_CHANNEL_GUIDANCE, source)
                self.assertIn(
                    "a later\nexplicit stable-release/cutover decision may make an immutable channel preferred\nor required",
                    source,
                )

    def test_public_main_examples_do_not_weaken_private_helper_immutability(self) -> None:
        flutter = (WORKFLOW_DOCS / "flutter.md").read_text(encoding="utf-8")
        oci_build = (WORKFLOW_DOCS / "oci-build.md").read_text(encoding="utf-8")

        self.assertIn(
            "invoke reviewed central composite actions directly through exact\nfull-SHA references",
            flutter,
        )
        self.assertRegex(
            oci_build,
            r"`validate-oci` composite action through immutable central revision\n`[0-9a-f]{40}`",
        )
        for source in (flutter, oci_build):
            self.assertIn(
                "This does not weaken internal/private helper pins, which remain\nexact immutable SHAs.",
                source,
            )


if __name__ == "__main__":
    unittest.main()
