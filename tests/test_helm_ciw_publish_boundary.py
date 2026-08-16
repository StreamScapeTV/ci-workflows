from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ci_workflows import ciw_helm
from ci_workflows.helm_types import (
    HelmPlan,
    HelmProduct,
    HelmPublicationResult,
    HelmRequest,
    HelmValidationError,
    HelmValidationResult,
)


ROOT = Path(__file__).resolve().parents[1]
SHA = "a" * 40


def _request(source_trust: str = "trusted-exact") -> HelmRequest:
    return HelmRequest(
        repository="StreamScapeTV/iptv-backend",
        admitted_sha=SHA,
        product_id="iptv-backend-chart",
        release_version="1.2.3",
        values_profile="default",
        policy_path=None,
        artifact_exception_id=None,
        source_trust=source_trust,
    )


def _plan() -> HelmPlan:
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
        values_path="charts/iptv-backend/values.yaml",
        policy_path=None,
    )


class HelmCiwPublishBoundaryTests(unittest.TestCase):
    def test_generic_ciw_publish_execute_uses_simple_validate_and_push_path(self) -> None:
        plan = _plan()
        validation = HelmValidationResult(
            chart_digest="sha256:" + "b" * 64,
            package_sha256="b" * 64,
            summary="{}",
            archive_path=Path("/tmp/chart.tgz"),
        )
        publication = HelmPublicationResult(
            chart_digest=validation.chart_digest,
            immutable_references_json='{"chart":"oci://example/chart:1.2.3"}',
            package_sha256=validation.package_sha256,
            published=True,
        )
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            source = state / "source"
            source.mkdir()
            with (
                patch("ci_workflows.ciw_helm._state_root", return_value=state),
                patch("ci_workflows.ciw_helm._source_root", return_value=source),
                patch("ci_workflows.helm_contract.load_helm_contract", return_value={}),
                patch("ci_workflows.helm_contract.request_from_environment", return_value=_request()),
                patch("ci_workflows.helm_dependency_policy.resolve_validation_plan", return_value=plan),
                patch("ci_workflows.helm_simple.validate_and_package", return_value=validation) as validate,
                patch("ci_workflows.helm_policy.run_policy_hook") as policy,
                patch("ci_workflows.helm_archive.finalize_validation_archive", return_value=validation),
                patch("ci_workflows.helm_simple.publish", return_value=publication) as publish,
            ):
                values = ciw_helm.execute(
                    ROOT,
                    {},
                    operation="publish",
                    phase="execute",
                    source_relative="source",
                )
        validate.assert_called_once()
        policy.assert_called_once()
        publish.assert_called_once()
        self.assertEqual(values["result"], "success")
        self.assertEqual(values["published"], "true")
        self.assertEqual(values["chart_digest"], validation.chart_digest)
        self.assertEqual(values["source_trust"], "trusted-exact")

    def test_generic_publish_still_rejects_untrusted_source(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            with (
                patch("ci_workflows.ciw_helm._state_root", return_value=state),
                patch("ci_workflows.helm_contract.load_helm_contract", return_value={}),
                patch(
                    "ci_workflows.helm_contract.request_from_environment",
                    return_value=_request("trusted-pr"),
                ),
            ):
                with self.assertRaisesRegex(
                    HelmValidationError,
                    "source_trust_rejected",
                ):
                    ciw_helm.execute(
                        ROOT,
                        {},
                        operation="publish",
                        phase="execute",
                        source_relative="source",
                    )

    def test_generic_ciw_publish_cleanup_and_residue_remain_available(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            with (
                patch("ci_workflows.ciw_helm._state_root", return_value=state),
                patch("ci_workflows.helm_execution.cleanup_helm_state") as cleanup,
            ):
                values = ciw_helm.execute(
                    ROOT,
                    {},
                    operation="publish",
                    phase="cleanup",
                    source_relative="source",
                )
            cleanup.assert_called_once_with(state)
            self.assertEqual(values["result"], "success")

            with (
                patch("ci_workflows.ciw_helm._state_root", return_value=state),
                patch("ci_workflows.helm_execution.verify_no_helm_residue") as residue,
            ):
                values = ciw_helm.execute(
                    ROOT,
                    {},
                    operation="publish",
                    phase="residue",
                    source_relative="source",
                )
            residue.assert_called_once_with(state)
            self.assertEqual(values["result"], "success")


if __name__ == "__main__":
    unittest.main()
