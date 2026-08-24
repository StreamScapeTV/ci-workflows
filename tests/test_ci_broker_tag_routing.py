from __future__ import annotations

import unittest
from pathlib import Path

import yaml

from ci_workflows.validation_model import ActionsLoader

ROOT = Path(__file__).resolve().parents[1]
BROKER = ROOT / ".github/workflows/ci-broker-image.yml"
RUNNERS = ROOT / ".github/workflows/runner-images-release.yml"


class BrokerTagRoutingTests(unittest.TestCase):
    def test_broker_tags_and_exact_replays_only_enter_broker_release_lane(self) -> None:
        broker = yaml.load(BROKER.read_text(encoding="utf-8"), Loader=ActionsLoader)
        runners = yaml.load(RUNNERS.read_text(encoding="utf-8"), Loader=ActionsLoader)

        self.assertEqual(broker["on"]["push"]["tags"], ["ci-broker-*"])
        self.assertEqual(runners["on"]["push"]["tags"], ["*", "!ci-broker-*"])
        manual = broker["on"]["workflow_dispatch"]["inputs"]
        self.assertEqual(set(manual), {"release_tag"})
        self.assertTrue(manual["release_tag"]["required"])
        self.assertEqual(manual["release_tag"]["type"], "string")


if __name__ == "__main__":
    unittest.main()
