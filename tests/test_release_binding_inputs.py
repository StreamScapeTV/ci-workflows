from __future__ import annotations

import json
from pathlib import Path
import unittest

from ci_workflows.release_binding import image_reference_bundle
from ci_workflows.release_types import ReleaseError


ROOT = Path(__file__).resolve().parents[1]
CASE = json.loads(
    (ROOT / "tests/fixtures/release/publications.json").read_text(encoding="utf-8")
)["cases"]["iptv-backend"]


def immutable_output() -> dict[str, object]:
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
    return {
        "targets": targets,
        "release": {
            "source_sha": CASE["source_sha"],
            "version": CASE["release_version"],
        },
    }


class ReleaseBindingInputTest(unittest.TestCase):
    def test_non_text_digest_json_fails_with_digest_code(self) -> None:
        with self.assertRaisesRegex(ReleaseError, r"^image_digest_map_rejected$"):
            image_reference_bundle(
                image_digest_json=None,
                immutable_references_json=json.dumps(immutable_output()),
                expected_source_sha=CASE["source_sha"],
                expected_release_version=CASE["release_version"],
            )

    def test_non_text_reference_json_fails_with_reference_code(self) -> None:
        with self.assertRaisesRegex(ReleaseError, r"^image_reference_map_rejected$"):
            image_reference_bundle(
                image_digest_json=json.dumps(CASE["image"]["digests"]),
                immutable_references_json=None,
                expected_source_sha=CASE["source_sha"],
                expected_release_version=CASE["release_version"],
            )

    def test_non_text_source_sha_fails_with_release_identity_code(self) -> None:
        with self.assertRaisesRegex(
            ReleaseError, r"^image_release_identity_rejected$"
        ):
            image_reference_bundle(
                image_digest_json=json.dumps(CASE["image"]["digests"]),
                immutable_references_json=json.dumps(immutable_output()),
                expected_source_sha=None,
                expected_release_version=CASE["release_version"],
            )


if __name__ == "__main__":
    unittest.main()
