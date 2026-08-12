from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ci_workflows.release import main


ROOT = Path(__file__).resolve().parents[1]
CASE = json.loads(
    (ROOT / "tests/fixtures/release/publications.json").read_text(encoding="utf-8")
)["cases"]["flux-runner-assets"]


def registered_outputs() -> tuple[dict[str, str], dict[str, object]]:
    image = CASE["image"]
    targets: dict[str, object] = {}
    for target, version_reference in image["version_references"].items():
        repository = version_reference.rsplit(":", 1)[0]
        targets[target] = {
            "repository": repository,
            "version": version_reference,
            "source_sha": image["source_references"][target],
            "manifest_digest": image["digests"][target],
        }
    return (
        dict(image["digests"]),
        {
            "targets": targets,
            "release": {
                "source_sha": CASE["source_sha"],
                "version": CASE["release_version"],
            },
            "flux": {
                "canary_id": "runner-images-canary",
                "previous_known_good": "flux-policy:runner-images/current-known-good",
                "rollback_id": "runner-images-rollback",
            },
        },
    )


class ReleaseCliTest(unittest.TestCase):
    def test_image_bindings_emits_sorted_helm_ready_digest_array(self) -> None:
        digests, immutable = registered_outputs()
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "github-output"
            with patch.dict(os.environ, {"GITHUB_OUTPUT": str(output)}, clear=False):
                result = main(
                    [
                        "--root",
                        str(ROOT),
                        "image-bindings",
                        "--image-digest-json",
                        json.dumps(digests),
                        "--immutable-references-json",
                        json.dumps(immutable),
                        "--expected-source-sha",
                        str(CASE["source_sha"]),
                        "--expected-release-version",
                        str(CASE["release_version"]),
                    ]
                )
            self.assertEqual(0, result)
            values = {}
            for line in output.read_text(encoding="utf-8").splitlines():
                key, value = line.split("=", 1)
                values[key] = value
            required = json.loads(values["required_image_references_json"])
            expected = []
            for target in sorted(digests):
                repository = immutable["targets"][target]["repository"]
                expected.append(f"{repository}@{digests[target]}")
            self.assertEqual(expected, required)
            self.assertEqual(
                dict(sorted(digests.items())),
                json.loads(values["image_digests_json"]),
            )
            self.assertEqual("runner-images-canary", values["canary_id"])


if __name__ == "__main__":
    unittest.main()
