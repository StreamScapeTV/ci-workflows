from __future__ import annotations

import json
from pathlib import Path
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]
DEPENDABOT = ROOT / ".github/dependabot.yml"
RUNNERS = ROOT / "RUNNERS.md"
GENERAL_DOCKERFILE = ROOT / "runner-images/general/Dockerfile"
MOBILE_TOOLCHAIN = ROOT / "runner-images/mobile/toolchain.lock.json"


class RunnerImageFreshnessTests(unittest.TestCase):
    def test_docker_update_lanes_are_independent_and_weekly(self) -> None:
        document = yaml.safe_load(DEPENDABOT.read_text(encoding="utf-8"))
        self.assertEqual(2, document["version"])
        updates = document["updates"]
        expected_directories = (
            "/runner-images/mobile",
            "/runner-images/buildah",
            "/runner-images/service",
            "/runner-images/docker",
            "/runner-images/flux-control",
        )
        self.assertEqual(len(expected_directories), len(updates))
        self.assertEqual(expected_directories, tuple(row["directory"] for row in updates))

        for row in updates:
            with self.subTest(directory=row["directory"]):
                self.assertEqual("docker", row["package-ecosystem"])
                self.assertEqual({"interval": "weekly"}, row["schedule"])
                self.assertNotIn("directories", row)
                self.assertNotIn("groups", row)

                dockerfile = ROOT / row["directory"].lstrip("/") / "Dockerfile"
                first_from = next(
                    line for line in dockerfile.read_text(encoding="utf-8").splitlines()
                    if line.startswith("FROM ")
                )
                reference = first_from.split()[1]
                name_and_tag, separator, digest = reference.partition("@sha256:")
                self.assertEqual("@sha256:", separator)
                self.assertTrue(digest)
                self.assertIn(":", name_and_tag.rsplit("/", 1)[-1])

    def test_general_digest_only_stages_stay_lock_reviewed(self) -> None:
        document = yaml.safe_load(DEPENDABOT.read_text(encoding="utf-8"))
        directories = {row["directory"] for row in document["updates"]}
        self.assertNotIn("/runner-images/general", directories)

        first_from = next(
            line for line in GENERAL_DOCKERFILE.read_text(encoding="utf-8").splitlines()
            if line.startswith("FROM ")
        )
        reference = first_from.split()[1]
        name, separator, digest = reference.partition("@sha256:")
        self.assertEqual("@sha256:", separator)
        self.assertTrue(digest)
        self.assertNotIn(":", name.rsplit("/", 1)[-1])

    def test_runner_guide_tracks_mobile_node_lock(self) -> None:
        toolchain = json.loads(MOBILE_TOOLCHAIN.read_text(encoding="utf-8"))["toolchain"]
        guide = RUNNERS.read_text(encoding="utf-8")
        self.assertIn(f"Node {toolchain['node']}", guide)
        self.assertNotIn("Node 24.18.0", guide)


if __name__ == "__main__":
    unittest.main()
