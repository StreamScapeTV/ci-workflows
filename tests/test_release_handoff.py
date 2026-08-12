from __future__ import annotations

import json
from pathlib import Path
import unittest

from ci_workflows.release_contract import resolve_release_plan
from ci_workflows.release_handoff import build_flux_handoff, flux_handoff_json
from ci_workflows.release_manifest import canonical_json, publication_identity
from ci_workflows.release_types import ReleaseError


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = json.loads(
    (ROOT / "tests/fixtures/release/publications.json").read_text(encoding="utf-8")
)["cases"]["iptv-backend"]


def fixture_identities():
    image = FIXTURE["image"]
    chart = FIXTURE["chart"]
    return (
        publication_identity(
            product_id=image["product_id"],
            kind="oci-image",
            digests_json=canonical_json(image["digests"]),
            immutable_references_json=canonical_json(
                {
                    "version_references": image["version_references"],
                    "source_references": image["source_references"],
                }
            ),
            evidence_json=canonical_json({"evidence_id": image["evidence_id"]}),
        ),
        publication_identity(
            product_id=chart["product_id"],
            kind="helm-chart",
            digest=chart["digest"],
            immutable_references_json=canonical_json(chart["immutable_references"]),
        ),
    )


class ReleaseHandoffTest(unittest.TestCase):
    def setUp(self) -> None:
        self.plan = resolve_release_plan(
            ROOT,
            "iptv-backend",
            "StreamScapeTV/iptv-backend",
        )
        self.image, self.chart = fixture_identities()

    def _payload(self):
        return build_flux_handoff(
            plan=self.plan,
            release_version="1.4.2",
            source_sha="1" * 40,
            release_manifest_sha256="a" * 64,
            github_release_url=(
                "https://github.com/StreamScapeTV/iptv-backend/releases/tag/1.4.2"
            ),
            image=self.image,
            chart=self.chart,
        )

    def test_handoff_contains_only_reviewable_immutable_selection_request(self) -> None:
        payload = self._payload()
        self.assertEqual("flux-selection-request", payload["kind"])
        self.assertEqual("StreamScapeTV/flux", payload["target_repository"])
        self.assertEqual("review-selection", payload["requested_action"])
        self.assertFalse(payload["mutation_authorized"])
        self.assertFalse(payload["secrets_included"])
        self.assertEqual(2, len(payload["products"]))
        for product in payload["products"]:
            self.assertNotIn("evidence", product)
            self.assertTrue(product["digests"])
            self.assertTrue(product["immutable_references"])

    def test_handoff_is_deterministic_and_has_independent_digest(self) -> None:
        first, first_sha = flux_handoff_json(
            plan=self.plan,
            release_version="1.4.2",
            source_sha="1" * 40,
            release_manifest_sha256="a" * 64,
            github_release_url=(
                "https://github.com/StreamScapeTV/iptv-backend/releases/tag/1.4.2"
            ),
            image=self.image,
            chart=self.chart,
        )
        second, second_sha = flux_handoff_json(
            plan=self.plan,
            release_version="1.4.2",
            source_sha="1" * 40,
            release_manifest_sha256="a" * 64,
            github_release_url=(
                "https://github.com/StreamScapeTV/iptv-backend/releases/tag/1.4.2"
            ),
            image=self.image,
            chart=self.chart,
        )
        self.assertEqual(first, second)
        self.assertEqual(first_sha, second_sha)
        self.assertEqual(self._payload(), json.loads(first))

    def test_external_or_unreviewed_handoff_targets_are_impossible(self) -> None:
        with self.assertRaisesRegex(ReleaseError, r"^github_release_url_rejected$"):
            build_flux_handoff(
                plan=self.plan,
                release_version="1.4.2",
                source_sha="1" * 40,
                release_manifest_sha256="a" * 64,
                github_release_url="https://example.com/release/1.4.2",
                image=self.image,
                chart=self.chart,
            )

    def test_schema_forbids_credentials_and_live_mutation(self) -> None:
        schema = json.loads(
            (ROOT / "contracts/flux-handoff.schema.json").read_text(encoding="utf-8")
        )
        self.assertFalse(schema["properties"]["mutation_authorized"]["const"])
        self.assertFalse(schema["properties"]["secrets_included"]["const"])
        self.assertEqual(
            "review-selection",
            schema["properties"]["requested_action"]["const"],
        )
        serialized = json.dumps(schema).casefold()
        self.assertNotIn("kubeconfig", serialized)
        self.assertNotIn("cluster credential", serialized)


if __name__ == "__main__":
    unittest.main()
