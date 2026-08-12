from __future__ import annotations

import json
from pathlib import Path
import unittest

from ci_workflows.release_binding import image_reference_bundle
from ci_workflows.release_contract import resolve_public_release
from ci_workflows.release_evidence import (
    chart_publication_evidence,
    evidence_json,
    image_publication_evidence,
)
from ci_workflows.release_github import desired_release, ensure_github_release
from ci_workflows.release_handoff import flux_handoff_json
from ci_workflows.release_manifest import (
    canonical_json,
    publication_identity,
    publication_progress,
    release_manifest_json,
)
from ci_workflows.release_types import ReleaseError


ROOT = Path(__file__).resolve().parents[1]
CASE = json.loads(
    (ROOT / "tests/fixtures/release/publications.json").read_text(encoding="utf-8")
)["cases"]["iptv-backend"]


class MemoryReleaseAPI:
    def __init__(self, repository: str) -> None:
        self.repository = repository
        self.release = None
        self.create_calls = 0

    def get_by_tag(self, _tag: str):
        return self.release

    def create(self, payload):
        self.create_calls += 1
        self.release = {
            **dict(payload),
            "html_url": (
                f"https://github.com/{self.repository}/releases/tag/"
                f"{payload['tag_name']}"
            ),
        }
        return self.release


def registered_image_outputs(case) -> tuple[str, str]:
    image = case["image"]
    targets = {}
    for target, version_reference in image["version_references"].items():
        repository = version_reference.rsplit(":", 1)[0]
        targets[target] = {
            "repository": repository,
            "version": version_reference,
            "source_sha": image["source_references"][target],
            "manifest_digest": image["digests"][target],
        }
    immutable = {
        "targets": targets,
        "release": {
            "source_sha": case["source_sha"],
            "version": case["release_version"],
        },
    }
    return canonical_json(image["digests"]), canonical_json(immutable)


class ReleaseOrchestrationEndToEndTest(unittest.TestCase):
    def build_verified_release(self):
        version = CASE["release_version"]
        source_sha = CASE["source_sha"]
        plan, request = resolve_public_release(
            ROOT,
            release_contract="backend",
            repository=CASE["repository"],
            admitted_sha=source_sha,
            release_tag=version,
            release_version=version,
            request_id="fixture-release-request-19",
            target_id="iptv-backend",
        )
        self.assertEqual("iptv-backend", request["release_id"])

        image_digest_json, image_immutable_json = registered_image_outputs(CASE)
        digests, digest_references, image_bundle, selection = image_reference_bundle(
            image_digest_json=image_digest_json,
            immutable_references_json=image_immutable_json,
            expected_source_sha=source_sha,
            expected_release_version=version,
        )
        self.assertIsNone(selection)
        self.assertEqual(
            [
                "ghcr.io/streamscapetv/iptv-backend@"
                "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
            ],
            [digest_references[key] for key in sorted(digest_references)],
        )

        image_evidence = image_publication_evidence(
            result="success",
            image_digest_json=image_digest_json,
            platform_digests_json=canonical_json(CASE["image"]["platform_digests"]),
            immutable_references_json=image_immutable_json,
        )
        chart_evidence = chart_publication_evidence(
            result="success",
            immutable_references_json=canonical_json(
                CASE["chart"]["immutable_references"]
            ),
        )
        image = publication_identity(
            product_id=CASE["image"]["product_id"],
            kind="oci-image",
            digests_json=canonical_json(digests),
            immutable_references_json=image_bundle,
            evidence_json=evidence_json(image_evidence),
        )
        chart = publication_identity(
            product_id=CASE["chart"]["product_id"],
            kind="helm-chart",
            digest=CASE["chart"]["digest"],
            immutable_references_json=canonical_json(
                CASE["chart"]["immutable_references"]
            ),
            evidence_json=evidence_json(chart_evidence),
        )
        manifest_json, manifest_sha256 = release_manifest_json(
            root=ROOT,
            plan=plan,
            release_version=version,
            source_sha=source_sha,
            tag_object_sha=CASE["tag_object_sha"],
            tag_commit_sha=source_sha,
            source_timestamp=CASE["source_timestamp"],
            workflow_sha=CASE["workflow_sha"],
            image=image,
            chart=chart,
        )
        desired = desired_release(
            plan=plan,
            release_tag=version,
            release_version=version,
            source_sha=source_sha,
            manifest_json=manifest_json,
            manifest_sha256=manifest_sha256,
        )
        return plan, image, chart, manifest_json, manifest_sha256, desired

    def test_exact_release_replay_and_flux_handoff_share_one_manifest_identity(self) -> None:
        plan, image, chart, manifest_json, manifest_sha256, desired = (
            self.build_verified_release()
        )
        api = MemoryReleaseAPI(CASE["repository"])

        release_url, create_state = ensure_github_release(api, desired)
        replay_url, replay_state = ensure_github_release(api, desired)

        self.assertEqual("created", create_state)
        self.assertEqual("existing-matched", replay_state)
        self.assertEqual(release_url, replay_url)
        self.assertEqual(1, api.create_calls)
        self.assertIn(manifest_sha256, desired.body)
        self.assertIn(manifest_json, desired.body)

        handoff_json, handoff_sha256 = flux_handoff_json(
            plan=plan,
            release_version=CASE["release_version"],
            source_sha=CASE["source_sha"],
            release_manifest_sha256=manifest_sha256,
            github_release_url=release_url,
            image=image,
            chart=chart,
        )
        handoff = json.loads(handoff_json)
        self.assertEqual(manifest_sha256, handoff["release_manifest_sha256"])
        self.assertEqual(release_url, handoff["github_release_url"])
        self.assertEqual(
            [CASE["image"]["product_id"], CASE["chart"]["product_id"]],
            [product["product_id"] for product in handoff["products"]],
        )
        self.assertFalse(handoff["mutation_authorized"])
        self.assertFalse(handoff["secrets_included"])
        self.assertEqual("review-selection", handoff["requested_action"])
        self.assertRegex(handoff_sha256, r"^[0-9a-f]{64}$")
        self.assertEqual(
            "complete",
            publication_progress(image_result="success", chart_result="success"),
        )

    def test_conflicting_github_release_fails_after_complete_publication_identity(self) -> None:
        _plan, _image, _chart, _manifest_json, _manifest_sha256, desired = (
            self.build_verified_release()
        )
        api = MemoryReleaseAPI(CASE["repository"])
        ensure_github_release(api, desired)
        api.release = dict(api.release)
        api.release["body"] = "conflicting immutable release body"

        with self.assertRaisesRegex(ReleaseError, r"^github_release_conflict$"):
            ensure_github_release(api, desired)
        self.assertEqual(1, api.create_calls)

    def test_partial_publication_never_projects_complete_state(self) -> None:
        self.assertEqual(
            "image-published-awaiting-chart",
            publication_progress(image_result="success", chart_result="failure"),
        )
        self.assertEqual(
            "image-publication-incomplete",
            publication_progress(image_result="failure", chart_result="missing"),
        )
        self.assertEqual(
            "publication-not-complete",
            publication_progress(image_result="missing", chart_result="missing"),
        )

    def test_secret_shaped_evidence_is_rejected_before_manifest_or_handoff(self) -> None:
        image_digest_json, image_immutable_json = registered_image_outputs(CASE)
        digests, _digest_references, image_bundle, _selection = image_reference_bundle(
            image_digest_json=image_digest_json,
            immutable_references_json=image_immutable_json,
            expected_source_sha=CASE["source_sha"],
            expected_release_version=CASE["release_version"],
        )
        with self.assertRaisesRegex(ReleaseError, r"^publication_evidence_rejected$"):
            publication_identity(
                product_id=CASE["image"]["product_id"],
                kind="oci-image",
                digests_json=canonical_json(digests),
                immutable_references_json=image_bundle,
                evidence_json=canonical_json(
                    {"registry_token": "credential-shaped-value"}
                ),
            )


if __name__ == "__main__":
    unittest.main()
