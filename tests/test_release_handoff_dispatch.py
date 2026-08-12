from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ci_workflows.release import main
from ci_workflows.release_manifest import canonical_json, sha256_text


ROOT = Path(__file__).resolve().parents[1]


class FakeResponse:
    status = 204

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, _maximum: int):
        return b""


class ReleaseHandoffDispatchTest(unittest.TestCase):
    def payload(self) -> dict[str, object]:
        image_digest = "sha256:" + "c" * 64
        chart_digest = "sha256:" + "d" * 64
        return {
            "schema_version": 1,
            "kind": "flux-selection-request",
            "producer_repository": "StreamScapeTV/iptv-backend",
            "target_repository": "StreamScapeTV/flux",
            "release_id": "iptv-backend",
            "release_version": "1.2.3",
            "source_sha": "a" * 40,
            "release_manifest_sha256": "b" * 64,
            "github_release_url": (
                "https://github.com/StreamScapeTV/iptv-backend/releases/tag/v1.2.3"
            ),
            "products": [
                {
                    "product_id": "iptv-backend-image",
                    "kind": "oci-image",
                    "digest": image_digest,
                    "digests": {"server": image_digest},
                    "immutable_references": [
                        f"ghcr.io/streamscapetv/iptv-backend@{image_digest}"
                    ],
                },
                {
                    "product_id": "iptv-backend-chart",
                    "kind": "helm-chart",
                    "digest": chart_digest,
                    "digests": {"primary": chart_digest},
                    "immutable_references": [
                        "oci://git.faruqi.dev/mimranfaruqi/helm-charts/"
                        "iptv-backend:1.2.3"
                    ],
                },
            ],
            "requested_action": "review-selection",
            "mutation_authorized": False,
            "secrets_included": False,
        }

    def test_dispatch_is_fixed_review_only_repository_request(self) -> None:
        handoff = canonical_json(self.payload())
        digest = sha256_text(handoff)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "github-output"
            captured = {}

            def fake_urlopen(request, timeout=0):
                captured["url"] = request.full_url
                captured["method"] = request.method
                captured["headers"] = dict(request.header_items())
                captured["body"] = request.data
                captured["timeout"] = timeout
                return FakeResponse()

            with (
                patch.dict(
                    os.environ,
                    {
                        "GITHUB_OUTPUT": str(output),
                        "FLUX_HANDOFF_TOKEN": "not-serialized-secret",
                    },
                    clear=False,
                ),
                patch("urllib.request.urlopen", side_effect=fake_urlopen),
            ):
                result = main(
                    [
                        "--root",
                        str(ROOT),
                        "dispatch-handoff",
                        "--request-id",
                        "fixture-request-0003",
                        "--flux-handoff-json",
                        handoff,
                        "--flux-handoff-sha256",
                        digest,
                    ]
                )

        self.assertEqual(0, result)
        self.assertEqual(
            "https://api.github.com/repos/StreamScapeTV/flux/dispatches",
            captured["url"],
        )
        self.assertEqual("POST", captured["method"])
        body = json.loads(captured["body"].decode("utf-8"))
        self.assertEqual("release-selection-review", body["event_type"])
        self.assertEqual("fixture-request-0003", body["client_payload"]["request_id"])
        self.assertEqual(digest, body["client_payload"]["handoff_sha256"])
        self.assertEqual(self.payload(), body["client_payload"]["handoff"])
        self.assertNotIn("not-serialized-secret", captured["body"].decode("utf-8"))
        values = dict(
            line.split("=", 1)
            for line in output.read_text(encoding="utf-8").splitlines()
        )
        self.assertEqual("review-requested", values["handoff_state"])

    def test_digest_mismatch_fails_before_network(self) -> None:
        handoff = canonical_json(self.payload())
        with (
            patch.dict(os.environ, {"FLUX_HANDOFF_TOKEN": "secret"}, clear=False),
            patch("urllib.request.urlopen") as urlopen,
        ):
            result = main(
                [
                    "--root",
                    str(ROOT),
                    "dispatch-handoff",
                    "--request-id",
                    "fixture-request-0003",
                    "--flux-handoff-json",
                    handoff,
                    "--flux-handoff-sha256",
                    "0" * 64,
                ]
            )
        self.assertEqual(2, result)
        urlopen.assert_not_called()

    def test_missing_handoff_token_fails_before_network(self) -> None:
        handoff = canonical_json(self.payload())
        digest = sha256_text(handoff)
        environment = dict(os.environ)
        environment.pop("FLUX_HANDOFF_TOKEN", None)
        with (
            patch.dict(os.environ, environment, clear=True),
            patch("urllib.request.urlopen") as urlopen,
        ):
            result = main(
                [
                    "--root",
                    str(ROOT),
                    "dispatch-handoff",
                    "--request-id",
                    "fixture-request-0003",
                    "--flux-handoff-json",
                    handoff,
                    "--flux-handoff-sha256",
                    digest,
                ]
            )
        self.assertEqual(2, result)
        urlopen.assert_not_called()

    def test_digest_valid_extra_secret_field_is_rejected_before_network(self) -> None:
        payload = self.payload()
        payload["registry_token"] = "secret=must-not-cross-boundary"
        handoff = canonical_json(payload)
        digest = sha256_text(handoff)
        with (
            patch.dict(os.environ, {"FLUX_HANDOFF_TOKEN": "secret"}, clear=False),
            patch("urllib.request.urlopen") as urlopen,
        ):
            result = main(
                [
                    "--root",
                    str(ROOT),
                    "dispatch-handoff",
                    "--request-id",
                    "fixture-request-0003",
                    "--flux-handoff-json",
                    handoff,
                    "--flux-handoff-sha256",
                    digest,
                ]
            )
        self.assertEqual(2, result)
        urlopen.assert_not_called()

    def test_non_flux_release_cannot_smuggle_selection(self) -> None:
        payload = self.payload()
        payload["selection"] = {
            "canary_id": "unexpected",
            "previous_known_good": "unexpected",
            "rollback_id": "unexpected",
        }
        handoff = canonical_json(payload)
        digest = sha256_text(handoff)
        with (
            patch.dict(os.environ, {"FLUX_HANDOFF_TOKEN": "secret"}, clear=False),
            patch("urllib.request.urlopen") as urlopen,
        ):
            result = main(
                [
                    "--root",
                    str(ROOT),
                    "dispatch-handoff",
                    "--request-id",
                    "fixture-request-0003",
                    "--flux-handoff-json",
                    handoff,
                    "--flux-handoff-sha256",
                    digest,
                ]
            )
        self.assertEqual(2, result)
        urlopen.assert_not_called()


if __name__ == "__main__":
    unittest.main()
