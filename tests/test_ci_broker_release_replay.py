from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/ci-broker-image.yml"


class BrokerReleaseReplayTests(unittest.TestCase):
    def test_chart_package_is_canonicalized_before_publish_and_exact_readback(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")
        package = text.index('helm package "${chart}"')
        canonicalize = text.index("canonicalize_chart_archive", package)
        publish = text.index('helm push "${BROKER_CHART_PACKAGE}"', canonicalize)
        readback = text.index('helm pull "${chart_ref}"', publish)

        self.assertLess(package, canonicalize)
        self.assertLess(canonicalize, publish)
        self.assertLess(publish, readback)
        self.assertIn('shasum -a 256 "${BROKER_CHART_PACKAGE}"', text)
        self.assertIn('shasum -a 256 "${existing}"', text)
        self.assertIn('shasum -a 256 "${readback}"', text)
        self.assertNotIn("sha256sum", text)
        self.assertNotIn(":latest", text)


if __name__ == "__main__":
    unittest.main()
