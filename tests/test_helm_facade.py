from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ci_workflows import helm
from ci_workflows.helm_types import (
    HelmPlan,
    HelmProduct,
    HelmPublicationResult,
    HelmValidationResult,
)


class HelmFacadeTests(unittest.TestCase):
    def plan(self) -> HelmPlan:
        product = HelmProduct(
            product_id="iptv-backend-chart",
            repository="StreamScapeTV/iptv-backend",
            chart_name="iptv-backend",
            chart_root="charts/iptv-backend",
            values_profiles={"default": "values.yaml"},
            policy_path=None,
            registry_repository="oci://git.faruqi.dev/mimranfaruqi/helm-charts",
            locked_dependencies=(),
            required_image_references=(),
        )
        return HelmPlan(
            product=product,
            release_version="1.2.3",
            values_profile="default",
            values_path="values.yaml",
            policy_path=None,
        )

    def validation(self, root: Path) -> HelmValidationResult:
        archive = root / "canonical.tgz"
        archive.write_bytes(b"chart")
        return HelmValidationResult(
            chart_digest="sha256:" + "a" * 64,
            package_sha256="a" * 64,
            summary="{}",
            archive_path=archive,
        )

    def preliminary_publication(self) -> HelmPublicationResult:
        return HelmPublicationResult(
            chart_digest="sha256:" + "a" * 64,
            immutable_references_json=json.dumps(
                {
                    "chart": "oci://git.faruqi.dev/mimranfaruqi/helm-charts/iptv-backend:1.2.3",
                    "chart_digest": "sha256:" + "a" * 64,
                    "package_sha256": "a" * 64,
                }
            ),
            package_sha256="a" * 64,
            published=True,
        )

    def test_publish_projects_remote_manifest_digest_from_named_component(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            remote_digest = "sha256:" + "b" * 64
            with (
                patch(
                    "ci_workflows.helm.publish_and_read_back",
                    return_value=self.preliminary_publication(),
                ) as registry,
                patch(
                    "ci_workflows.helm.remote_chart_manifest_digest",
                    return_value=remote_digest,
                ) as manifest,
            ):
                result = helm.publish(
                    root,
                    root,
                    self.plan(),
                    self.validation(root),
                    {"INPUT_RELEASE_MODE": "tag-push"},
                )
            self.assertEqual(result.chart_digest, remote_digest)
            self.assertEqual(
                json.loads(result.immutable_references_json)["chart_digest"],
                remote_digest,
            )
            self.assertTrue(result.published)
            registry.assert_called_once()
            manifest.assert_called_once()

    def test_read_back_forces_existing_tag_and_never_inherits_publish_mode(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            captured: dict[str, str] = {}

            def fake_registry(source_root, state_root, plan, validation, environment):
                captured.update(environment)
                result = self.preliminary_publication()
                return HelmPublicationResult(
                    chart_digest=result.chart_digest,
                    immutable_references_json=result.immutable_references_json,
                    package_sha256=result.package_sha256,
                    published=False,
                )

            with (
                patch(
                    "ci_workflows.helm.publish_and_read_back",
                    side_effect=fake_registry,
                ),
                patch(
                    "ci_workflows.helm.remote_chart_manifest_digest",
                    return_value="sha256:" + "b" * 64,
                ),
            ):
                result = helm.read_back(
                    root,
                    root,
                    self.plan(),
                    self.validation(root),
                    {"INPUT_RELEASE_MODE": "tag-push", "KEEP": "bounded"},
                )
            self.assertEqual(captured["INPUT_RELEASE_MODE"], "existing-tag")
            self.assertEqual(captured["KEEP"], "bounded")
            self.assertFalse(result.published)


if __name__ == "__main__":
    unittest.main()
