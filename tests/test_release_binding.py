from __future__ import annotations

import json
from pathlib import Path
import unittest

from ci_workflows.release_binding import (
    digest_pinned_image_references,
    image_reference_bundle,
)
from ci_workflows.release_types import ReleaseError


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = json.loads(
    (ROOT / "tests/fixtures/release/publications.json").read_text(encoding="utf-8")
)["cases"]["flux-runner-assets"]["image"]


class ReleaseImageBindingTest(unittest.TestCase):
    def setUp(self) -> None:
        versions = FIXTURE["version_references"]
        self.repositories = {
            target: reference.rsplit(":", 1)[0]
            for target, reference in versions.items()
        }
        self.digests = FIXTURE["digests"]

    def test_digest_pinned_references_bind_every_read_back_target(self) -> None:
        result = digest_pinned_image_references(
            json.dumps(self.repositories),
            json.dumps(self.digests),
        )
        self.assertEqual(set(self.repositories), set(result))
        for target, reference in result.items():
            self.assertEqual(
                f"{self.repositories[target]}@{self.digests[target]}",
                reference,
            )
            self.assertIn("@sha256:", reference)
            self.assertNotIn(":latest", reference)

    def test_bundle_preserves_version_source_and_digest_identities(self) -> None:
        digest_refs, rendered = image_reference_bundle(
            repositories_json=json.dumps(self.repositories),
            manifest_digests_json=json.dumps(self.digests),
            version_references_json=json.dumps(FIXTURE["version_references"]),
            source_references_json=json.dumps(FIXTURE["source_references"]),
        )
        bundle = json.loads(rendered)
        self.assertEqual(digest_refs, bundle["digest_references"])
        self.assertEqual(FIXTURE["version_references"], bundle["version_references"])
        self.assertEqual(FIXTURE["source_references"], bundle["source_references"])

    def test_target_set_mismatch_fails_closed(self) -> None:
        digests = dict(self.digests)
        digests.pop(next(iter(digests)))
        with self.assertRaisesRegex(ReleaseError, r"^image_target_mismatch$"):
            digest_pinned_image_references(
                json.dumps(self.repositories),
                json.dumps(digests),
            )

    def test_unchecked_registry_redirect_is_rejected(self) -> None:
        repositories = dict(self.repositories)
        repositories[next(iter(repositories))] = "evil.example/streamscapetv/runner"
        with self.assertRaisesRegex(ReleaseError, r"^image_repository_map_rejected$"):
            digest_pinned_image_references(
                json.dumps(repositories),
                json.dumps(self.digests),
            )

    def test_version_reference_must_match_read_back_repository(self) -> None:
        versions = dict(FIXTURE["version_references"])
        versions[next(iter(versions))] = "ghcr.io/streamscapetv/other:2.0.0"
        with self.assertRaisesRegex(ReleaseError, r"^image_reference_map_rejected$"):
            image_reference_bundle(
                repositories_json=json.dumps(self.repositories),
                manifest_digests_json=json.dumps(self.digests),
                version_references_json=json.dumps(versions),
                source_references_json=json.dumps(FIXTURE["source_references"]),
            )


if __name__ == "__main__":
    unittest.main()
