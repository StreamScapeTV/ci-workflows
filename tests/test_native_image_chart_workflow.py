from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "reusable-native-image-chart.yml"
VALIDATE = ROOT / "scripts" / "ci" / "native_image_chart_validate.py"
PREPARE = ROOT / "scripts" / "ci" / "native_image_chart_prepare.py"
PUBLIC_INDEX = ROOT / "contracts" / "public-workflows.json"
TAG_FIXTURE = ROOT / "tests" / "fixtures" / "harness" / "callers" / "tag-release.yml"


class NativeImageChartWorkflowContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.text = WORKFLOW.read_text(encoding="utf-8")
        cls.validate = VALIDATE.read_text(encoding="utf-8")
        cls.prepare = PREPARE.read_text(encoding="utf-8")
        cls.implementation = "\n".join((cls.text, cls.validate, cls.prepare))

    def test_is_generic_reusable_exact_tag_publisher(self) -> None:
        text = self.text
        self.assertIn("workflow_call:", text)
        self.assertIn("Resolve exact release tag authority", text)
        self.assertIn("Revalidate tag immediately before publication", text)
        self.assertIn("${{ github.workflow_sha }}", text)
        self.assertIn("repository: ${{ github.repository }}", text)
        self.assertNotIn("product_id", self.implementation)
        self.assertNotIn("supported_consumers", self.implementation)
        self.assertNotIn("supported_products", self.implementation)
        self.assertNotIn("agent-state-dashboard", self.implementation)

    def test_selects_native_amd64_central_capacity_only(self) -> None:
        text = self.text
        self.assertIn("runs-on: [linux, amd64, buildah, high]", text)
        self.assertIn('test "$(uname -m)" = x86_64', text)
        self.assertIn("remote image is not linux/amd64", text)
        self.assertNotIn("arm64", self.implementation)
        self.assertNotIn("qemu", self.implementation.casefold())
        self.assertNotIn("--platform \"linux/", text)

    def test_composes_product_neutral_packaging_primitives(self) -> None:
        self.assertIn("native_image_chart_validate.py", self.text)
        self.assertIn("native_image_chart_prepare.py", self.text)
        self.assertIn("ci_workflows.packaging_primitives", self.prepare)
        for primitive in (
            "build_image",
            "inspect_image",
            "helm_lint",
            "helm_template",
            "helm_package",
        ):
            self.assertIn(primitive, self.prepare)
        for primitive in (
            "registry_authenticate",
            "push_image",
            "helm_push",
            "registry_logout",
        ):
            self.assertIn(primitive, self.text)
        self.assertIn("image_name:", self.text)
        self.assertIn("chart_name:", self.text)
        self.assertIn("chart_path:", self.text)
        self.assertIn("dockerfile_path:", self.text)
        self.assertIn("build_context:", self.text)

    def test_publication_is_immutable_read_back_and_non_deploying(self) -> None:
        text = self.text
        self.assertIn("require unused immutable version identities", text.casefold())
        self.assertIn("skopeo inspect --raw", text)
        self.assertIn("image_digest", text)
        self.assertIn("chart_digest", text)
        self.assertIn("chart_package_sha256", text)
        self.assertNotIn("upload-artifact", text)
        self.assertNotIn("kubectl", text)
        self.assertNotIn("flux reconcile", text)
        self.assertNotIn(":latest", text)

    def test_fixed_registry_and_secret_interface_are_bounded(self) -> None:
        text = self.text
        self.assertIn("REGISTRY: git.faruqi.dev", text)
        self.assertIn("REGISTRY_NAMESPACE: mimranfaruqi", text)
        self.assertIn("CHART_NAMESPACE: mimranfaruqi/helm-charts", text)
        self.assertIn("registry_username:", text)
        self.assertIn("registry_token:", text)
        self.assertIn("CIW_REGISTRY_USERNAME", text)
        self.assertIn("CIW_REGISTRY_TOKEN", text)
        self.assertIn("release versions are not aligned", self.validate)

    def test_public_api_index_and_release_fixture_use_the_native_api(self) -> None:
        index = json.loads(PUBLIC_INDEX.read_text(encoding="utf-8"))
        apis = [entry["api_name"] for entry in index["workflows"]]
        self.assertEqual(index["workflow_count"], len(apis))
        self.assertEqual(apis.count("release.native-image-chart"), 1)
        fixture = TAG_FIXTURE.read_text(encoding="utf-8")
        self.assertIn("reusable-native-image-chart.yml@", fixture)
        self.assertNotIn("reusable-release.yml@", fixture)


if __name__ == "__main__":
    unittest.main()
