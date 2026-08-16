from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ci_workflows.helm_simple import _reject_latest_images, publish
from ci_workflows.helm_types import (
    HelmPlan,
    HelmProduct,
    HelmValidationError,
    HelmValidationResult,
)


ROOT = Path(__file__).resolve().parents[1]


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
        required_image_references=(
            "git.faruqi.dev/mimranfaruqi/iptv-backend@sha256:" + "a" * 64,
        ),
    )
    return HelmPlan(
        product=product,
        release_version="1.2.3",
        values_profile="default",
        values_path="charts/iptv-backend/values.yaml",
        policy_path=None,
    )


def _validation(root: Path) -> HelmValidationResult:
    package = root / "normalized.tgz"
    package.write_bytes(b"chart")
    return HelmValidationResult(
        chart_digest="sha256:" + "b" * 64,
        package_sha256="b" * 64,
        summary="{}",
        archive_path=package,
    )


class HelmSimplePublicationTests(unittest.TestCase):
    def test_simple_publish_logs_in_and_pushes_without_read_back(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            calls: list[tuple[list[str], str | None]] = []

            def fake_run(
                argv,
                *,
                cwd,
                environment,
                timeout,
                code,
                stdin=None,
                check=True,
            ):
                calls.append((list(argv), stdin))
                return subprocess.CompletedProcess(argv, 0, "", "")

            runtime = {
                "PATH": "/usr/bin",
                "HOME": str(root),
                "INPUT_REGISTRY_USERNAME": "user",
                "INPUT_REGISTRY_TOKEN": "token-value",
            }
            with (
                patch("ci_workflows.helm_simple._verify_no_kubernetes_authority"),
                patch(
                    "ci_workflows.helm_simple._runtime_environment",
                    return_value={"PATH": "/usr/bin", "HOME": str(root)},
                ),
                patch("ci_workflows.helm_simple._registry_host", return_value="git.faruqi.dev"),
                patch("ci_workflows.helm_simple._run", side_effect=fake_run),
            ):
                result = publish(root, root, _plan(), _validation(root), runtime)

        self.assertTrue(result.published)
        self.assertEqual([call[0][:2] for call in calls], [["helm", "registry"], ["helm", "push"]])
        flattened = " ".join(token for argv, _stdin in calls for token in argv)
        self.assertNotIn("token-value", flattened)
        self.assertEqual(calls[0][1], "token-value\n")
        self.assertIsNone(calls[1][1])
        self.assertFalse(any("pull" in argv or "skopeo" in argv for argv, _ in calls))
        self.assertIn("iptv-backend:1.2.3", result.immutable_references_json)

    def test_simple_publish_requires_normal_registry_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch("ci_workflows.helm_simple._verify_no_kubernetes_authority"):
                with self.assertRaisesRegex(
                    HelmValidationError,
                    "registry_auth_missing",
                ):
                    publish(
                        root,
                        root,
                        _plan(),
                        _validation(root),
                        {"PATH": "/usr/bin", "HOME": str(root)},
                    )

    def test_generic_chart_validation_does_not_require_digest_pins(self) -> None:
        _reject_latest_images(
            'image: git.faruqi.dev/mimranfaruqi/iptv-backend:1.2.3\n'
        )
        _reject_latest_images(
            'image: git.faruqi.dev/mimranfaruqi/iptv-backend@sha256:' + "a" * 64 + "\n"
        )
        with self.assertRaisesRegex(
            HelmValidationError,
            "image_reference_mismatch",
        ):
            _reject_latest_images(
                "image: git.faruqi.dev/mimranfaruqi/iptv-backend:latest\n"
            )

    def test_core_actions_use_normal_python_without_action_lock_bootstrap(self) -> None:
        for relative, operation in (
            ("actions/validate-helm/action.yml", "helm validate --phase"),
            ("actions/publish-helm/action.yml", "helm publish --phase"),
        ):
            action = (ROOT / relative).read_text(encoding="utf-8")
            self.assertIn(operation, action)
            self.assertIn("PYTHONPATH", action)
            self.assertNotIn("bootstrap_validation_runtime.py", action)
            self.assertNotIn("action-tool-lock.json", action)
            self.assertNotIn("scripts/ci/helm_release.py", action)
            self.assertNotIn("INPUT_IMAGE_DIGEST", action)
            self.assertNotIn("INPUT_IMMUTABLE_REFERENCES_JSON", action)


if __name__ == "__main__":
    unittest.main()
