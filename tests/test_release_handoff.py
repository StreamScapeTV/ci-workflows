from __future__ import annotations

import json
from pathlib import Path
import unittest

from ci_workflows.release_contract import resolve_release_plan
from ci_workflows.release_handoff import build_flux_handoff, flux_handoff_json
from ci_workflows.release_manifest import canonical_json, publication_identity
from ci_workflows.release_types import ReleaseError


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = json.loads(
    (ROOT / "tests/fixtures/release/publications.json").read_text(encoding="utf-8")
)["cases"]


def fixture_identities(case):
    image = case["image"]
    chart = case["chart"]
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
        self.image, self.chart = fixture_identities(FIXTURES["iptv-backend"])

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
        self.assertNotIn("selection", payload)
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

    def test_flux_runner_handoff_requires_exact_canary_and_rollback_selection(self) -> None:
        case = FIXTURES["flux-runner-assets"]
        image, chart = fixture_identities(case)
        plan = resolve_release_plan(ROOT, "flux-runner-assets", "StreamScapeTV/flux")
        kwargs = {
            "plan": plan,
            "release_version": case["release_version"],
            "source_sha": case["source_sha"],
            "release_manifest_sha256": "b" * 64,
            "github_release_url": (
                "https://github.com/StreamScapeTV/flux/releases/tag/2.0.0"
            ),
            "image": image,
            "chart": chart,
        }
        with self.assertRaisesRegex(ReleaseError, r"^handoff_selection_required$"):
            build_flux_handoff(**kwargs)
        payload = build_flux_handoff(
            **kwargs,
            canary_id="runner-images-canary",
            previous_known_good="flux-policy:runner-images/current-known-good",
            rollback_id="runner-images-rollback",
        )
        self.assertEqual(
            {
                "canary_id": "runner-images-canary",
                "previous_known_good": "flux-policy:runner-images/current-known-good",
                "rollback_id": "runner-images-rollback",
            },
            payload["selection"],
        )

    def test_non_flux_release_rejects_selection_identity_smuggling(self) -> None:
        with self.assertRaisesRegex(ReleaseError, r"^handoff_selection_rejected$"):
            build_flux_handoff(
                plan=self.plan,
                release_version="1.4.2",
                source_sha="1" * 40,
                release_manifest_sha256="a" * 64,
                github_release_url=(
                    "https://github.com/StreamScapeTV/iptv-backend/releases/tag/1.4.2"
                ),
                image=self.image,
                chart=self.chart,
                canary_id="unexpected",
                previous_known_good="unexpected",
                rollback_id="unexpected",
            )

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

    def test_schema_forbids_credentials_live_mutation_and_weak_flux_selection(self) -> None:
        schema = json.loads(
            (ROOT / "contracts/flux-handoff.schema.json").read_text(encoding="utf-8")
        )
        self.assertFalse(schema["properties"]["mutation_authorized"]["const"])
        self.assertFalse(schema["properties"]["secrets_included"]["const"])
        self.assertEqual(
            "review-selection",
            schema["properties"]["requested_action"]["const"],
        )
        self.assertEqual(
            ["selection"],
            schema["allOf"][0]["then"]["required"],
        )
        serialized = json.dumps(schema).casefold()
        self.assertNotIn("kubeconfig", serialized)
        self.assertNotIn("cluster credential", serialized)


if __name__ == "__main__":
    unittest.main()
