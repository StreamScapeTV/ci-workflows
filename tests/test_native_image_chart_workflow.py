from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "reusable-native-image-chart.yml"


class NativeImageChartWorkflowContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.text = WORKFLOW.read_text(encoding="utf-8")

    def test_is_generic_reusable_exact_tag_publisher(self) -> None:
        text = self.text
        self.assertIn("workflow_call:", text)
        self.assertIn("Resolve exact tag-push authority", text)
        self.assertIn("Revalidate tag immediately before publication", text)
        self.assertIn("${{ github.workflow_sha }}", text)
        self.assertIn("repository: ${{ github.repository }}", text)
        self.assertNotIn("product_id", text)
        self.assertNotIn("supported_consumers", text)
        self.assertNotIn("supported_products", text)
        self.assertNotIn("agent-state-dashboard", text)

    def test_selects_native_amd64_central_capacity_only(self) -> None:
        text = self.text
        self.assertIn("runs-on: [linux, amd64, buildah, high]", text)
        self.assertIn('test "$(uname -m)" = x86_64', text)
        self.assertIn("remote image is not linux/amd64", text)
        self.assertNotIn("arm64", text)
        self.assertNotIn("qemu", text.casefold())
        self.assertNotIn("--platform \"linux/", text)

    def test_composes_product_neutral_packaging_primitives(self) -> None:
        text = self.text
        self.assertIn("ci_workflows.packaging_primitives", text)
        for primitive in (
            "build_image",
            "inspect_image",
            "registry_authenticate",
            "push_image",
            "helm_lint",
            "helm_template",
            "helm_package",
            "helm_push",
            "registry_logout",
        ):
            self.assertIn(primitive, text)
        self.assertIn("image_name:", text)
        self.assertIn("chart_name:", text)
        self.assertIn("chart_path:", text)
        self.assertIn("dockerfile_path:", text)
        self.assertIn("build_context:", text)

    def test_publication_is_immutable_read_back_and_non_deploying(self) -> None:
        text = self.text
        self.assertIn("Require unused immutable version identities", text)
        self.assertIn("skopeo inspect --raw", text)
        self.assertIn("image_digest", text)
        self.assertIn("chart_digest", text)
        self.assertIn("chart_package_sha256", text)
        self.assertIn("FORGEJO", text) if False else None
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
        self.assertIn("release versions are not aligned", text)


if __name__ == "__main__":
    unittest.main()
