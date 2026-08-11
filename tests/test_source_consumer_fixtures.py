from __future__ import annotations

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests/fixtures/source-admission"


class SourceConsumerFixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.cases = json.loads(
            (FIXTURES / "cases.json").read_text(encoding="utf-8")
        )
        cls.events = json.loads(
            (FIXTURES / "events.json").read_text(encoding="utf-8")
        )

    def test_all_required_organization_consumer_patterns_are_recorded(self) -> None:
        patterns = {
            item["id"]: item
            for item in self.cases["consumer_patterns"]
        }
        self.assertEqual(
            set(patterns),
            {
                "backend-manual-exact",
                "android-manual-exact",
                "apple-develop-validation",
                "media-develop-validation",
                "web-pr-validation",
                "web-push-validation",
                "tag-image-chart-historical-release",
                "flux-source-validation",
                "flux-cluster-reconciliation",
            },
        )
        for pattern in patterns.values():
            self.assertIn(pattern["event"], self.events)

    def test_privileged_metadata_patterns_forbid_untrusted_source_execution(
        self,
    ) -> None:
        patterns = {
            item["id"]: item
            for item in self.cases["consumer_patterns"]
        }
        for identifier in (
            "flux-cluster-reconciliation",
        ):
            with self.subTest(identifier=identifier):
                self.assertFalse(
                    patterns[identifier]["executes_untrusted_pr_source"]
                )
        self.assertFalse(
            patterns["flux-cluster-reconciliation"]["product_source_checkout"]
        )
        self.assertFalse(
            patterns["flux-source-validation"]["cluster_authorized"]
        )

    def test_historical_release_pattern_is_explicit(self) -> None:
        patterns = {
            item["id"]: item
            for item in self.cases["consumer_patterns"]
        }
        release = patterns["tag-image-chart-historical-release"]
        self.assertEqual(release["source_mode"], "tag")
        self.assertTrue(release["historical_commit_supported"])


if __name__ == "__main__":
    unittest.main()
