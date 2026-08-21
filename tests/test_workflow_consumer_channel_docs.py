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
    "During active Central development, repository consumers call public reusable\n"
    "`ci-workflows` workflows at `@main` as ordinary shared-library references. No\n"
    "per-product bootstrap or registration step, consumer-maintained Central SHA, or\n"
    "synchronization handshake is required. Human-readable compatibility tags and\n"
    "full-SHA references remain supported, and a later reviewed policy may prefer a\n"
    "stable tag such as `@v1`."
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
                self.assertNotIn("active development/bootstrap", source)

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
            self.assertIn("This does not weaken internal/private helper pins", source)
            self.assertIn("exact immutable SHAs.", source)

    def test_policy_docs_do_not_describe_main_as_a_bootstrap_channel(self) -> None:
        sources = {
            "AGENTS.md": (ROOT / "AGENTS.md").read_text(encoding="utf-8"),
            "CONTRIBUTING.md": (ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8"),
            "source-and-trust.md": (
                ROOT / "docs" / "architecture" / "source-and-trust.md"
            ).read_text(encoding="utf-8"),
        }
        for name, source in sources.items():
            with self.subTest(name=name):
                self.assertIn("ordinary shared-library", source)
                self.assertNotIn("initial bootstrap consumer channel", source)
                self.assertNotIn("During bootstrap, consumers", source)
                self.assertNotIn("active-development/bootstrap phase", source)


if __name__ == "__main__":
    unittest.main()
