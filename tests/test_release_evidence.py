from __future__ import annotations

import json
from pathlib import Path
import unittest

from ci_workflows.release_evidence import (
    chart_publication_evidence,
    evidence_json,
    image_publication_evidence,
)
from ci_workflows.release_manifest import publication_identity
from ci_workflows.release_types import ReleaseError


ROOT = Path(__file__).resolve().parents[1]
CASE = json.loads(
    (ROOT / "tests/fixtures/release/publications.json").read_text(encoding="utf-8")
)["cases"]["iptv-backend"]


def immutable_image_output() -> dict[str, object]:
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


class ReleaseEvidenceTest(unittest.TestCase):
    def test_registered_oci_outputs_become_bounded_redacted_evidence(self) -> None:
        image = image_publication_evidence(
            result="success",
            image_digest_json=json.dumps(CASE["image"]["digests"]),
            platform_digests_json=json.dumps(CASE["image"]["platform_digests"]),
            immutable_references_json=json.dumps(immutable_image_output()),
        )
        self.assertEqual("success", image["result"])
        self.assertEqual(CASE["image"]["digests"], image["image_digest"])
        self.assertEqual(CASE["image"]["platform_digests"], image["platform_digests"])
        rendered = evidence_json(image)
        self.assertNotIn("password", rendered.casefold())
        self.assertNotIn("authorization", rendered.casefold())

    def test_chart_evidence_requires_successful_remote_read_back(self) -> None:
        chart = chart_publication_evidence(
            result="success",
            immutable_references_json=json.dumps(CASE["chart"]["immutable_references"]),
        )
        self.assertEqual("success", chart["result"])
        self.assertEqual(
            CASE["chart"]["immutable_references"],
            chart["read_back"],
        )
        with self.assertRaisesRegex(ReleaseError, r"^chart_evidence_rejected$"):
            chart_publication_evidence(
                result="failure",
                immutable_references_json="{}",
            )

    def test_image_evidence_requires_successful_publication(self) -> None:
        with self.assertRaisesRegex(ReleaseError, r"^image_evidence_rejected$"):
            image_publication_evidence(
                result="failure",
                image_digest_json=json.dumps(CASE["image"]["digests"]),
                platform_digests_json=json.dumps(CASE["image"]["platform_digests"]),
                immutable_references_json=json.dumps(immutable_image_output()),
            )

    def test_manifest_identity_rejects_secret_named_evidence_fields(self) -> None:
        with self.assertRaisesRegex(ReleaseError, r"^publication_evidence_rejected$"):
            publication_identity(
                product_id="iptv-backend-image",
                kind="oci-image",
                digest="sha256:" + "a" * 64,
                immutable_references_json=json.dumps(
                    {"reference": "ghcr.io/streamscapetv/iptv-backend:1.4.2"}
                ),
                evidence_json=json.dumps({"registry_token": "sensitive-value"}),
            )


if __name__ == "__main__":
    unittest.main()
