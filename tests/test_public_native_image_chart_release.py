from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import yaml

from ci_workflows.public_native_image_chart import (
    CHART_NAMESPACE,
    REGISTRY,
    REGISTRY_NAMESPACE,
    chart_reference,
    image_reference,
    readback_public,
)
from ci_workflows.validation_model import ActionsLoader

ROOT = Path(__file__).resolve().parents[1]
PUBLIC_WORKFLOW = ROOT / ".github" / "workflows" / "reusable-public-native-image-chart.yml"
PRIVATE_WORKFLOW = ROOT / ".github" / "workflows" / "reusable-native-image-chart.yml"
PRODUCTS = ROOT / "contracts" / "public-workflows" / "products.json"
PERMISSIONS = ROOT / "contracts" / "permission-profiles.json"


class PublicNativeImageChartReleaseTests(unittest.TestCase):
    def test_public_api_is_hosted_secret_free_and_ghcr_only(self) -> None:
        workflow = yaml.load(PUBLIC_WORKFLOW.read_text(encoding="utf-8"), Loader=ActionsLoader)
        call = workflow["on"]["workflow_call"]
        self.assertEqual(
            set(call["inputs"]),
            {"image_name", "chart_name", "chart_path", "dockerfile_path", "build_context"},
        )
        self.assertNotIn("secrets", call)
        self.assertEqual(workflow["permissions"], {"contents": "read", "packages": "write"})
        self.assertEqual(workflow["env"]["REGISTRY"], "ghcr.io")
        self.assertEqual(workflow["env"]["REGISTRY_NAMESPACE"], "streamscapetv")
        self.assertEqual(workflow["env"]["CHART_NAMESPACE"], "streamscapetv/helm-charts")
        self.assertEqual(workflow["jobs"]["admit"]["runs-on"], ["ubuntu-latest"])
        self.assertEqual(workflow["jobs"]["publish"]["runs-on"], ["ubuntu-latest"])

    def test_public_path_reuses_existing_build_and_chart_preparation(self) -> None:
        text = PUBLIC_WORKFLOW.read_text(encoding="utf-8")
        self.assertEqual(text.count("native_image_chart_prepare.py"), 1)
        self.assertEqual(text.count("native_image_chart_validate.py"), 1)
        self.assertIn("public_native_image_chart.py authenticate", text)
        self.assertIn("public_native_image_chart.py require-unused", text)
        self.assertIn("public_native_image_chart.py publish", text)
        self.assertIn("public_native_image_chart.py readback", text)
        self.assertIn("public_native_image_chart.py cleanup", text)
        self.assertIn("Drop credentials and anonymously read back public artifacts", text)
        self.assertNotIn("actions/upload-artifact", text)
        self.assertNotIn("git.faruqi.dev", text)
        self.assertNotIn("registry_username:", text)
        self.assertNotIn("registry_token:", text)
        self.assertNotIn("secrets:", text)
        self.assertNotIn(":latest", text)
        self.assertNotIn("self-hosted", text)
        self.assertNotIn("kubectl", text)
        self.assertNotIn("flux reconcile", text)

    def test_existing_private_release_surface_remains_private_and_unchanged_in_kind(self) -> None:
        text = PRIVATE_WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("REGISTRY: git.faruqi.dev", text)
        self.assertIn("registry_username:", text)
        self.assertIn("registry_token:", text)
        self.assertIn("runs-on: [linux, amd64, buildah, high]", text)
        self.assertNotIn("packages: write", text)
        self.assertNotIn("reusable-public-native-image-chart", text)

    def test_public_contract_uses_distinct_packages_write_permission_profile(self) -> None:
        products = json.loads(PRODUCTS.read_text(encoding="utf-8"))
        row = next(item for item in products["workflows"] if item["api_name"] == "release.public-native-image-chart")
        self.assertEqual(row["api_version"], "1.0.0")
        self.assertEqual(row["permission_profile"], "public-oci-publication")
        self.assertEqual(row["secrets"], [])
        profiles = json.loads(PERMISSIONS.read_text(encoding="utf-8"))["profiles"]
        profile = next(item for item in profiles if item["id"] == "public-oci-publication")
        self.assertEqual(profile["caller_permissions"], {"contents": "read", "packages": "write"})
        self.assertEqual(profile["workflow_permissions"], profile["caller_permissions"])
        self.assertEqual(profile["named_secrets_allowed"], [])

    def test_public_references_are_fixed_under_streamscapetv(self) -> None:
        self.assertEqual(REGISTRY, "ghcr.io")
        self.assertEqual(REGISTRY_NAMESPACE, "streamscapetv")
        self.assertEqual(CHART_NAMESPACE, "streamscapetv/helm-charts")
        self.assertEqual(
            image_reference("dashboard", "1.2.3"),
            "ghcr.io/streamscapetv/dashboard:1.2.3",
        )
        self.assertEqual(
            chart_reference("dashboard", "1.2.3"),
            "ghcr.io/streamscapetv/helm-charts/dashboard:1.2.3",
        )

    def test_readback_logs_out_before_anonymous_image_and_chart_checks(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            authfile = root / "anonymous.json"
            environment = {"REGISTRY_AUTH_FILE": str(root / "auth.json")}
            with (
                mock.patch("ci_workflows.public_native_image_chart.registry_logout") as logout,
                mock.patch(
                    "ci_workflows.public_native_image_chart._anonymous_raw_digest",
                    side_effect=["sha256:" + "a" * 64, "sha256:" + "b" * 64],
                ) as raw,
                mock.patch(
                    "ci_workflows.public_native_image_chart._anonymous_image_inspect",
                    return_value={
                        "Digest": "sha256:" + "a" * 64,
                        "Os": "linux",
                        "Architecture": "amd64",
                    },
                ),
            ):
                result = readback_public(
                    image_name="dashboard",
                    chart_name="dashboard",
                    version="1.2.3",
                    anonymous_authfile=authfile,
                    environment=environment,
                    cwd=root,
                )
            self.assertEqual(logout.call_count, 2)
            self.assertTrue(authfile.is_file())
            self.assertEqual(raw.call_count, 2)
            self.assertEqual(result["image_digest"], "sha256:" + "a" * 64)
            self.assertEqual(result["chart_digest"], "sha256:" + "b" * 64)
            self.assertEqual(
                result["chart_reference"],
                "oci://ghcr.io/streamscapetv/helm-charts/dashboard:1.2.3",
            )


if __name__ == "__main__":
    unittest.main()
