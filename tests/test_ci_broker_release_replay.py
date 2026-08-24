from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/ci-broker-image.yml"


class BrokerReleaseReplayTests(unittest.TestCase):
    def test_chart_package_is_normalized_before_publish_and_exact_readback(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")
        package = text.index('helm package "${chart}"')
        normalize = text.index("--sort=name", package)
        gzip = text.index("gzip -n", normalize)
        publish = text.index('helm push "${BROKER_CHART_PACKAGE}"', gzip)
        readback = text.index('helm pull "${chart_ref}"', publish)

        self.assertLess(package, normalize)
        self.assertLess(normalize, gzip)
        self.assertLess(gzip, publish)
        self.assertLess(publish, readback)
        self.assertIn("--mtime='@0'", text)
        self.assertIn("--owner=0", text)
        self.assertIn("--group=0", text)
        self.assertIn('sha256sum "${existing}"', text)
        self.assertIn('sha256sum "${readback}"', text)
        self.assertNotIn(":latest", text)


if __name__ == "__main__":
    unittest.main()
