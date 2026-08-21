from __future__ import annotations

import json
import unittest
from pathlib import Path

import yaml

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
            {
                "image_name",
                "chart_name",
                "chart_path",
                "dockerfile_path",
                "build_context",
                "publish_latest_image",
            },
        )
        self.assertEqual(call["inputs"]["publish_latest_image"]["type"], "boolean")
        self.assertFalse(call["inputs"]["publish_latest_image"]["default"])
        self.assertFalse(call["inputs"]["publish_latest_image"]["required"])
        self.assertNotIn("secrets", call)
        self.assertEqual(workflow["permissions"], {"contents": "read", "packages": "write"})
        self.assertEqual(workflow["env"]["REGISTRY"], "ghcr.io")
        self.assertEqual(workflow["env"]["REGISTRY_NAMESPACE"], "streamscapetv")
        self.assertEqual(workflow["env"]["CHART_NAMESPACE"], "streamscapetv/helm-charts")
        self.assertEqual(workflow["jobs"]["admit"]["runs-on"], ["ubuntu-latest"])
        self.assertEqual(workflow["jobs"]["publish"]["runs-on"], ["ubuntu-latest"])

    def test_public_path_reuses_existing_packaging_primitives(self) -> None:
        text = PUBLIC_WORKFLOW.read_text(encoding="utf-8")
        self.assertEqual(text.count("native_image_chart_prepare.py"), 1)
        self.assertEqual(text.count("native_image_chart_validate.py"), 1)
        for primitive in (
            "registry_authenticate",
            "push_image",
            "tag_image",
            "helm_push",
            "registry_logout",
            "cleanup_packaging_state",
        ):
            self.assertIn(primitive, text)
        self.assertIn("Drop credentials and anonymously read back public artifacts", text)
        self.assertIn('printf \'{}\\n\' > "${anon_authfile}"', text)
        self.assertIn('skopeo inspect --authfile "${anon_authfile}" --raw', text)
        self.assertNotIn("actions/upload-artifact", text)
        self.assertNotIn("git.faruqi.dev", text)
        self.assertNotIn("registry_username:", text)
        self.assertNotIn("registry_token:", text)
        self.assertNotIn("secrets:", text)
        self.assertNotIn("self-hosted", text)
        self.assertNotIn("kubectl", text)
        self.assertNotIn("flux reconcile", text)

    def test_latest_alias_is_opt_in_same_build_and_never_chart_authority(self) -> None:
        text = PUBLIC_WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("PUBLISH_LATEST_IMAGE: ${{ inputs.publish_latest_image }}", text)
        self.assertIn(
            "LATEST_IMAGE_REFERENCE: ghcr.io/streamscapetv/${{ inputs.image_name }}:latest",
            text,
        )
        self.assertIn('if os.environ["PUBLISH_LATEST_IMAGE"] == "true":', text)
        self.assertIn(
            'tag_image(\n                  os.environ["IMAGE_REFERENCE"],\n                  os.environ["LATEST_IMAGE_REFERENCE"],',
            text,
        )
        self.assertIn(
            'push_image(os.environ["LATEST_IMAGE_REFERENCE"], environment=env, tool="buildah", cwd=source)',
            text,
        )
        self.assertEqual(text.count("native_image_chart_prepare.py"), 1, "latest must not trigger a second image build")
        self.assertNotIn('${CHART_NAME}:latest', text)
        self.assertNotIn('helm_push(Path(os.environ["PACKAGE_PATH"]), "oci://ghcr.io/streamscapetv/helm-charts/latest"', text)

    def test_existing_private_release_surface_is_not_migrated(self) -> None:
        text = PRIVATE_WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("REGISTRY: git.faruqi.dev", text)
        self.assertIn("registry_username:", text)
        self.assertIn("registry_token:", text)
        self.assertIn("runs-on: [linux, amd64, buildah, high]", text)
        self.assertNotIn("packages: write", text)
        self.assertNotIn("reusable-public-native-image-chart", text)

    def test_public_contract_uses_distinct_packages_write_permission_profile(self) -> None:
        products = json.loads(PRODUCTS.read_text(encoding="utf-8"))
        row = next(
            item
            for item in products["workflows"]
            if item["api_name"] == "release.public-native-image-chart"
        )
        self.assertEqual(row["api_version"], "1.1.0")
        self.assertEqual(row["permission_profile"], "public-oci-publication")
        self.assertEqual(row["secrets"], [])
        publish_latest = next(item for item in row["inputs"] if item["name"] == "publish_latest_image")
        self.assertEqual(publish_latest, {"name": "publish_latest_image", "required": False, "default": False})
        profiles = json.loads(PERMISSIONS.read_text(encoding="utf-8"))["profiles"]
        profile = next(item for item in profiles if item["id"] == "public-oci-publication")
        self.assertEqual(
            profile["caller_permissions"],
            {"contents": "read", "packages": "write"},
        )
        self.assertEqual(profile["workflow_permissions"], profile["caller_permissions"])
        self.assertEqual(profile["named_secrets_allowed"], [])

    def test_versioned_references_are_fixed_and_latest_is_read_back_anonymously(self) -> None:
        text = PUBLIC_WORKFLOW.read_text(encoding="utf-8")
        self.assertIn(
            "ghcr.io/streamscapetv/${{ inputs.image_name }}:${{ needs.admit.outputs.version }}",
            text,
        )
        self.assertIn(
            'chart_reference="${REGISTRY}/${CHART_NAMESPACE}/${CHART_NAME}:${VERSION}"',
            text,
        )
        self.assertGreaterEqual(text.count('skopeo inspect --authfile "${anon_authfile}"'), 5)
        self.assertIn("anonymous image read-back identity mismatch", text)
        self.assertIn("anonymous latest image read-back identity mismatch", text)
        self.assertIn("latest image alias does not match immutable release image", text)
        self.assertIn('if [[ "${PUBLISH_LATEST_IMAGE}" == "true" ]]; then', text)


if __name__ == "__main__":
    unittest.main()
