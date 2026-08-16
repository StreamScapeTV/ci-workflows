from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from ci_workflows.oci_publish_contract import (
    OciPublishError,
    PublishRequest,
    resolve_plan,
    verify,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/oci-publish/oci-products.json"
SHA = "a" * 40


class PlatformConfirmationTests(unittest.TestCase):
    def _root(self) -> tempfile.TemporaryDirectory[str]:
        temp = tempfile.TemporaryDirectory()
        contract_dir = Path(temp.name) / "contracts"
        contract_dir.mkdir()
        shutil.copyfile(FIXTURE, contract_dir / "oci-products.json")
        return temp

    def test_platform_set_confirms_checked_in_matrix(self) -> None:
        with self._root() as temp:
            plan = resolve_plan(
                Path(temp),
                PublishRequest(
                    "StreamScapeTV/backend",
                    SHA,
                    SHA,
                    "backend-image",
                    "1.2.3",
                    "trusted-exact",
                    "linux-multi-arch",
                ),
            )
        self.assertEqual(plan.targets[0].platforms, ("linux/amd64", "linux/arm64/v8"))

    def test_platform_set_cannot_narrow_checked_in_matrix(self) -> None:
        with self._root() as temp:
            with self.assertRaisesRegex(OciPublishError, "platform_override_forbidden"):
                resolve_plan(
                    Path(temp),
                    PublishRequest(
                        "StreamScapeTV/backend",
                        SHA,
                        SHA,
                        "backend-image",
                        "1.2.3",
                        "trusted-exact",
                        "linux-amd64",
                    ),
                )


class PublicProjectionTests(unittest.TestCase):
    def test_verify_projects_registered_outputs_and_flux_handoff(self) -> None:
        target = SimpleNamespace(target_id="runner-buildah")
        plan = SimpleNamespace(
            targets=(target,),
            admitted_sha=SHA,
            release_version="1.2.3",
            flux_asset=True,
            canary_id="runner-images-canary",
            previous_known_good="flux-policy:runner-images/current-known-good",
            rollback_id="runner-images-rollback",
        )
        manifest = "sha256:" + "1" * 64
        detailed = {
            "result": "success",
            "repositories_json": '{"runner-buildah":"ghcr.io/streamscapetv/flux-runner-buildah"}',
            "version_references_json": '{"runner-buildah":"ghcr.io/streamscapetv/flux-runner-buildah:1.2.3"}',
            "source_references_json": '{"runner-buildah":"ghcr.io/streamscapetv/flux-runner-buildah:sha-' + SHA + '"}',
            "manifest_digests_json": '{"runner-buildah":"' + manifest + '"}',
            "platform_digests_json": '{"runner-buildah":{}}',
        }
        with patch("ci_workflows.oci_publish_contract._runtime.verify", return_value=detailed):
            outputs = verify(plan, {})
        self.assertEqual(outputs["image_digest"], detailed["manifest_digests_json"])
        self.assertIn('"canary_id":"runner-images-canary"', outputs["immutable_references_json"])
        self.assertIn('"rollback_id":"runner-images-rollback"', outputs["immutable_references_json"])
        self.assertIn(manifest, outputs["immutable_references_json"])


if __name__ == "__main__":
    unittest.main()
