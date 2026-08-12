from __future__ import annotations

import json
from pathlib import Path
import unittest
from unittest.mock import patch

from ci_workflows.release_contract import resolve_release_plan
from ci_workflows.release_manifest import (
    publication_identity,
    release_manifest_json,
    sha256_text,
)
from ci_workflows.release_types import ReleaseError


ROOT = Path(__file__).resolve().parents[1]
SOURCE_SHA = "1" * 40


def identities():
    image = publication_identity(
        product_id="iptv-backend-image",
        kind="oci-image",
        digest="sha256:" + "a" * 64,
        immutable_references_json=json.dumps(
            {"reference": "ghcr.io/streamscapetv/iptv-backend@sha256:" + "a" * 64}
        ),
    )
    chart = publication_identity(
        product_id="iptv-backend-chart",
        kind="helm-chart",
        digest="sha256:" + "b" * 64,
        immutable_references_json=json.dumps(
            {
                "chart": "oci://git.faruqi.dev/mimranfaruqi/helm-charts/iptv-backend:1.2.3",
                "chart_digest": "sha256:" + "b" * 64,
                "package_sha256": "b" * 64,
            }
        ),
    )
    return image, chart


class ReleaseManifestInputTest(unittest.TestCase):
    def test_publication_identity_rejects_invalid_product_and_non_text_json(self) -> None:
        with self.assertRaisesRegex(ReleaseError, r"^publication_product_rejected$"):
            publication_identity(
                product_id="../backend",
                kind="oci-image",
                digest="sha256:" + "a" * 64,
                immutable_references_json=json.dumps(
                    {"reference": "ghcr.io/example/image@sha256:" + "a" * 64}
                ),
            )
        with self.assertRaisesRegex(ReleaseError, r"^publication_evidence_rejected$"):
            publication_identity(
                product_id="iptv-backend-image",
                kind="oci-image",
                digest="sha256:" + "a" * 64,
                immutable_references_json=None,
            )
        with self.assertRaisesRegex(ReleaseError, r"^publication_evidence_rejected$"):
            publication_identity(
                product_id="iptv-backend-image",
                kind="oci-image",
                digest="sha256:" + "a" * 64,
                immutable_references_json=json.dumps(
                    {"reference": "ghcr.io/example/image@sha256:" + "a" * 64}
                ),
                evidence_json=None,
            )

    def test_non_finite_evidence_is_rejected(self) -> None:
        with self.assertRaisesRegex(ReleaseError, r"^publication_evidence_rejected$"):
            publication_identity(
                product_id="iptv-backend-image",
                kind="oci-image",
                digest="sha256:" + "a" * 64,
                immutable_references_json=json.dumps(
                    {"reference": "ghcr.io/example/image@sha256:" + "a" * 64}
                ),
                evidence_json='{"metric":NaN}',
            )

    def test_non_text_digest_and_canonical_text_fail_closed(self) -> None:
        with self.assertRaisesRegex(ReleaseError, r"^publication_digest_rejected$"):
            publication_identity(
                product_id="iptv-backend-image",
                kind="oci-image",
                digest=None,
                immutable_references_json=json.dumps(
                    {"reference": "ghcr.io/example/image@sha256:" + "a" * 64}
                ),
            )
        with self.assertRaisesRegex(ReleaseError, r"^canonical_text_rejected$"):
            sha256_text(None)

    def test_manifest_rejects_non_text_sha_and_timestamp_with_stable_codes(self) -> None:
        plan = resolve_release_plan(
            ROOT,
            "iptv-backend",
            "StreamScapeTV/iptv-backend",
        )
        image, chart = identities()
        common = {
            "root": ROOT,
            "plan": plan,
            "release_version": "1.2.3",
            "source_sha": SOURCE_SHA,
            "tag_object_sha": "2" * 40,
            "tag_commit_sha": SOURCE_SHA,
            "source_timestamp": "2026-08-12T10:15:30+00:00",
            "workflow_sha": "3" * 40,
            "image": image,
            "chart": chart,
        }
        with patch("ci_workflows.release_manifest._release_surface", return_value={}):
            invalid_sha = dict(common)
            invalid_sha["workflow_sha"] = None
            with self.assertRaisesRegex(ReleaseError, r"^release_sha_rejected$"):
                release_manifest_json(**invalid_sha)

            invalid_timestamp = dict(common)
            invalid_timestamp["source_timestamp"] = None
            with self.assertRaisesRegex(
                ReleaseError, r"^source_timestamp_rejected$"
            ):
                release_manifest_json(**invalid_timestamp)


if __name__ == "__main__":
    unittest.main()
