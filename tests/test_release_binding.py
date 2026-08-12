from __future__ import annotations

import json
from pathlib import Path
import unittest

from ci_workflows.release_binding import image_reference_bundle
from ci_workflows.release_types import ReleaseError


ROOT = Path(__file__).resolve().parents[1]
CASE = json.loads(
    (ROOT / "tests/fixtures/release/publications.json").read_text(encoding="utf-8")
)["cases"]["flux-runner-assets"]
FIXTURE = CASE["image"]
SOURCE_SHA = CASE["source_sha"]
VERSION = CASE["release_version"]


def registered_outputs() -> tuple[dict[str, str], dict[str, object]]:
    targets: dict[str, object] = {}
    for target, version_reference in FIXTURE["version_references"].items():
        repository = version_reference.rsplit(":", 1)[0]
        targets[target] = {
            "repository": repository,
            "version": version_reference,
            "source_sha": FIXTURE["source_references"][target],
            "manifest_digest": FIXTURE["digests"][target],
        }
    immutable: dict[str, object] = {
        "targets": targets,
        "release": {"source_sha": SOURCE_SHA, "version": VERSION},
        "flux": {
            "canary_id": "runner-images-canary",
            "previous_known_good": "flux-policy:runner-images/current-known-good",
            "rollback_id": "runner-images-rollback",
        },
    }
    return dict(FIXTURE["digests"]), immutable


class ReleaseImageBindingTest(unittest.TestCase):
    def setUp(self) -> None:
        self.digests, self.immutable = registered_outputs()

    def bind(self, *, digests=None, immutable=None, source_sha=SOURCE_SHA, version=VERSION):
        return image_reference_bundle(
            image_digest_json=json.dumps(self.digests if digests is None else digests),
            immutable_references_json=json.dumps(
                self.immutable if immutable is None else immutable
            ),
            expected_source_sha=source_sha,
            expected_release_version=version,
        )

    def test_digest_pinned_references_bind_every_read_back_target(self) -> None:
        normalized, digest_refs, rendered, selection = self.bind()
        self.assertEqual(self.digests, normalized)
        self.assertEqual(set(self.digests), set(digest_refs))
        bundle = json.loads(rendered)
        for target, reference in digest_refs.items():
            repository = self.immutable["targets"][target]["repository"]
            self.assertEqual(f"{repository}@{self.digests[target]}", reference)
            self.assertIn("@sha256:", reference)
            self.assertNotIn(":latest", reference)
        self.assertEqual(digest_refs, bundle["digest_references"])
        self.assertEqual("runner-images-canary", selection["canary_id"])
        self.assertEqual(
            "flux-policy:runner-images/current-known-good",
            selection["previous_known_good"],
        )

    def test_bundle_preserves_registered_version_and_source_identities(self) -> None:
        _, _, rendered, _ = self.bind()
        bundle = json.loads(rendered)
        self.assertEqual(FIXTURE["version_references"], bundle["version_references"])
        self.assertEqual(FIXTURE["source_references"], bundle["source_references"])

    def test_target_set_mismatch_fails_closed(self) -> None:
        digests = dict(self.digests)
        digests.pop(next(iter(digests)))
        with self.assertRaisesRegex(ReleaseError, r"^image_target_mismatch$"):
            self.bind(digests=digests)

    def test_manifest_digest_conflict_fails_closed(self) -> None:
        immutable = json.loads(json.dumps(self.immutable))
        target = next(iter(immutable["targets"]))
        immutable["targets"][target]["manifest_digest"] = "sha256:" + "9" * 64
        with self.assertRaisesRegex(ReleaseError, r"^image_digest_map_rejected$"):
            self.bind(immutable=immutable)

    def test_unchecked_registry_redirect_is_rejected(self) -> None:
        immutable = json.loads(json.dumps(self.immutable))
        target = next(iter(immutable["targets"]))
        immutable["targets"][target]["repository"] = "evil.example/streamscapetv/runner"
        with self.assertRaisesRegex(ReleaseError, r"^image_digest_map_rejected$"):
            self.bind(immutable=immutable)

    def test_version_reference_must_match_release_identity(self) -> None:
        immutable = json.loads(json.dumps(self.immutable))
        target = next(iter(immutable["targets"]))
        repository = immutable["targets"][target]["repository"]
        immutable["targets"][target]["version"] = f"{repository}:9.9.9"
        with self.assertRaisesRegex(ReleaseError, r"^image_reference_map_rejected$"):
            self.bind(immutable=immutable)

    def test_registered_release_source_must_equal_admitted_source(self) -> None:
        with self.assertRaisesRegex(ReleaseError, r"^image_release_identity_mismatch$"):
            self.bind(source_sha="9" * 40)


if __name__ == "__main__":
    unittest.main()
