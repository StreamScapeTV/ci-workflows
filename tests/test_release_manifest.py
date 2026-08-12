from __future__ import annotations

import hashlib
import json
from pathlib import Path
import unittest

from ci_workflows.release_contract import resolve_release_plan
from ci_workflows.release_manifest import (
    canonical_json,
    publication_identity,
    publication_progress,
    release_manifest_json,
)
from ci_workflows.release_types import ReleaseError


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = json.loads(
    (ROOT / "tests/fixtures/release/publications.json").read_text(encoding="utf-8")
)["cases"]


def identities(case: dict[str, object]):
    image = case["image"]
    chart = case["chart"]
    assert isinstance(image, dict)
    assert isinstance(chart, dict)
    image_references = {
        "version_references": image["version_references"],
        "source_references": image["source_references"],
    }
    image_evidence = {
        "platform_digests": image["platform_digests"],
        "evidence_id": image["evidence_id"],
    }
    return (
        publication_identity(
            product_id=str(image["product_id"]),
            kind="oci-image",
            digests_json=canonical_json(image["digests"]),
            immutable_references_json=canonical_json(image_references),
            evidence_json=canonical_json(image_evidence),
        ),
        publication_identity(
            product_id=str(chart["product_id"]),
            kind="helm-chart",
            digest=str(chart["digest"]),
            immutable_references_json=canonical_json(chart["immutable_references"]),
            evidence_json=canonical_json(chart["immutable_references"]),
        ),
    )


class PublicationIdentityTest(unittest.TestCase):
    def test_single_target_identity_preserves_exact_remote_digest(self) -> None:
        image, _ = identities(FIXTURES["iptv-backend"])
        self.assertEqual(
            "sha256:" + "a" * 64,
            image.digest,
        )
        self.assertEqual({"server": "sha256:" + "a" * 64}, image.digests)
        self.assertEqual(2, len(image.immutable_references))
        self.assertTrue(all("latest" not in value for value in image.immutable_references))

    def test_multi_target_identity_preserves_digest_map_without_arbitrary_collapse(self) -> None:
        image, _ = identities(FIXTURES["flux-runner-assets"])
        expected = "sha256:" + hashlib.sha256(
            canonical_json(image.digests).encode("utf-8")
        ).hexdigest()
        self.assertEqual(expected, image.digest)
        self.assertEqual(2, len(image.digests))
        self.assertEqual(4, len(image.immutable_references))

    def test_mutable_latest_reference_fails_closed(self) -> None:
        with self.assertRaisesRegex(ReleaseError, r"^publication_reference_rejected$"):
            publication_identity(
                product_id="iptv-backend-image",
                kind="oci-image",
                digest="sha256:" + "a" * 64,
                immutable_references_json=json.dumps(
                    {"version_reference": "ghcr.io/streamscapetv/iptv-backend:latest"}
                ),
            )

    def test_digest_map_and_explicit_primary_must_agree(self) -> None:
        with self.assertRaisesRegex(ReleaseError, r"^publication_digest_mismatch$"):
            publication_identity(
                product_id="iptv-backend-image",
                kind="oci-image",
                digest="sha256:" + "a" * 64,
                digests_json=json.dumps({"server": "sha256:" + "b" * 64}),
                immutable_references_json=json.dumps(
                    {"version_reference": "ghcr.io/streamscapetv/iptv-backend:1.4.2"}
                ),
            )


class ReleaseManifestTest(unittest.TestCase):
    def _render(self, release_id: str) -> tuple[str, str]:
        case = FIXTURES[release_id]
        assert isinstance(case, dict)
        image, chart = identities(case)
        plan = resolve_release_plan(ROOT, release_id, str(case["repository"]))
        return release_manifest_json(
            root=ROOT,
            plan=plan,
            release_version=str(case["release_version"]),
            source_sha=str(case["source_sha"]),
            tag_object_sha=str(case["tag_object_sha"]),
            tag_commit_sha=str(case["source_sha"]),
            source_timestamp=str(case["source_timestamp"]),
            workflow_sha=str(case["workflow_sha"]),
            image=image,
            chart=chart,
        )

    def test_manifest_is_canonical_deterministic_and_binds_exact_authority(self) -> None:
        first, first_sha = self._render("iptv-backend")
        second, second_sha = self._render("iptv-backend")
        self.assertEqual(first, second)
        self.assertEqual(first_sha, second_sha)
        self.assertEqual(hashlib.sha256(first.encode("utf-8")).hexdigest(), first_sha)
        payload = json.loads(first)
        self.assertEqual("product", payload["shared_release"]["kind"])
        self.assertEqual("1.4.2", payload["shared_release"]["tag"])
        self.assertEqual("1" * 40, payload["shared_release"]["commit"])
        self.assertEqual("2" * 40, payload["product_release"]["tag_object_sha"])
        self.assertEqual("1" * 40, payload["product_release"]["tag_commit_sha"])
        self.assertEqual("exact-match-only", payload["product_release"]["replay_policy"])
        self.assertFalse(payload["product_release"]["flux_handoff"]["mutation_authorized"])

    def test_flux_manifest_keeps_every_runner_image_digest(self) -> None:
        rendered, _ = self._render("flux-runner-assets")
        payload = json.loads(rendered)
        image = payload["product_release"]["publications"]["image"]
        self.assertEqual(
            {"linux-builder", "linux-general"},
            set(image["digests"]),
        )
        self.assertEqual(4, len(image["immutable_references"]))

    def test_tag_commit_mismatch_fails_before_manifest_creation(self) -> None:
        case = FIXTURES["iptv-backend"]
        image, chart = identities(case)
        plan = resolve_release_plan(ROOT, "iptv-backend", str(case["repository"]))
        with self.assertRaisesRegex(ReleaseError, r"^tag_source_mismatch$"):
            release_manifest_json(
                root=ROOT,
                plan=plan,
                release_version=str(case["release_version"]),
                source_sha="1" * 40,
                tag_object_sha="2" * 40,
                tag_commit_sha="9" * 40,
                source_timestamp=str(case["source_timestamp"]),
                workflow_sha="3" * 40,
                image=image,
                chart=chart,
            )

    def test_existing_library_manifest_required_fields_remain_unchanged(self) -> None:
        schema = json.loads(
            (ROOT / "contracts/release-manifest.schema.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            {
                "schema_version",
                "shared_release",
                "workflow_apis",
                "function_library",
                "schemas",
                "action_lock",
                "tool_lock",
                "runner_profiles",
                "consumers",
            },
            set(schema["required"]),
        )
        self.assertIn("product_release", schema["properties"])

    def test_partial_publication_state_is_explicit_and_replayable(self) -> None:
        self.assertEqual(
            "image-published-awaiting-chart",
            publication_progress(image_result="success", chart_result="failure"),
        )
        self.assertEqual(
            "complete",
            publication_progress(image_result="success", chart_result="success"),
        )
        self.assertNotEqual(
            "complete",
            publication_progress(image_result="failure", chart_result="skipped"),
        )


if __name__ == "__main__":
    unittest.main()
