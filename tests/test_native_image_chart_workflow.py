from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "reusable-native-image-chart.yml"
BACKEND = ROOT / "scripts" / "ci" / "native_image_chart_backend.py"
VALIDATE = ROOT / "scripts" / "ci" / "native_image_chart_validate.py"
PREPARE = ROOT / "scripts" / "ci" / "native_image_chart_prepare.py"
PUBLIC_INDEX = ROOT / "contracts" / "public-workflows.json"
TAG_FIXTURE = ROOT / "tests" / "fixtures" / "harness" / "callers" / "tag-release.yml"


class NativeImageChartWorkflowContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.text = WORKFLOW.read_text(encoding="utf-8")
        cls.backend = BACKEND.read_text(encoding="utf-8")
        cls.validate = VALIDATE.read_text(encoding="utf-8")
        cls.prepare = PREPARE.read_text(encoding="utf-8")
        cls.implementation = "\n".join((cls.text, cls.backend, cls.validate, cls.prepare))

    def test_is_generic_reusable_exact_tag_publisher(self) -> None:
        text = self.text
        self.assertIn("workflow_call:", text)
        self.assertIn("Resolve exact release tag authority", text)
        self.assertIn("Revalidate tag immediately before publication", text)
        self.assertEqual(3, text.count("repository: ${{ job.workflow_repository }}"))
        self.assertEqual(3, text.count("ref: ${{ job.workflow_sha }}"))
        self.assertNotIn("${{ github.workflow_sha }}", text)
        self.assertNotIn("${GITHUB_WORKFLOW_SHA}", text)
        self.assertIn("repository: ${{ github.repository }}", text)
        self.assertNotIn("product_id", self.implementation)
        self.assertNotIn("supported_consumers", self.implementation)
        self.assertNotIn("supported_products", self.implementation)
        self.assertNotIn("agent-state-dashboard", self.implementation)

    def test_backend_is_bounded_and_hosted_is_standard_github_linux_only(self) -> None:
        self.assertIn('"organization": {', self.backend)
        self.assertIn('"github-hosted": {', self.backend)
        self.assertIn('"registry": "git.faruqi.dev"', self.backend)
        self.assertIn('"registry": "ghcr.io"', self.backend)
        self.assertIn('"registry_namespace": "streamscapetv"', self.backend)
        self.assertIn('"chart_namespace": "streamscapetv/helm-charts"', self.backend)
        self.assertIn('workflow_api="release.native-image-chart"', self.backend)
        self.assertIn('requested_profile="buildah-high"', self.backend)
        self.assertIn("runs-on: ubuntu-latest", self.text)
        self.assertEqual(2, self.text.count("runs-on: ${{ fromJSON(needs.plan.outputs.runs_on_json) }}"))
        self.assertIn('test "${RUNNER_ENVIRONMENT:-}" = github-hosted', self.text)
        self.assertIn('test "$(uname -m)" = x86_64', self.text)
        self.assertIn("remote image is not linux/amd64", self.text)
        self.assertNotIn("qemu", self.implementation.casefold())

    def test_composes_product_neutral_packaging_primitives(self) -> None:
        self.assertIn("native_image_chart_backend.py", self.text)
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
        for public_input in (
            "execution_backend:",
            "image_name:",
            "chart_name:",
            "chart_path:",
            "dockerfile_path:",
            "build_context:",
        ):
            self.assertIn(public_input, self.text)
        self.assertNotIn("registry_host:", self.text)
        self.assertNotIn("runner_labels:", self.text)

    def test_publication_is_immutable_public_read_back_and_non_deploying(self) -> None:
        text = self.text
        self.assertIn("require unused immutable version identities", text.casefold())
        self.assertIn("skopeo --authfile", text)
        self.assertIn('readback_auth="${state}/anonymous-auth.json"', text)
        self.assertIn("registry_logout", text)
        self.assertIn("image_digest", text)
        self.assertIn("chart_digest", text)
        self.assertIn("chart_package_sha256", text)
        self.assertNotIn("upload-artifact", text)
        self.assertNotIn("kubectl", text)
        self.assertNotIn("flux reconcile", text)
        self.assertNotIn(":latest", text)

    def test_private_default_and_public_hosted_credentials_are_separate(self) -> None:
        text = self.text
        self.assertIn("default: organization", text)
        self.assertIn("required: false", text)
        self.assertIn("PRIVATE_REGISTRY_USERNAME: ${{ secrets.registry_username }}", text)
        self.assertIn("PRIVATE_REGISTRY_TOKEN: ${{ secrets.registry_token }}", text)
        self.assertIn("github.actor", text)
        self.assertIn("github.token", text)
        self.assertIn("packages: write", text)
        self.assertIn('test -z "${PRIVATE_REGISTRY_USERNAME}"', text)
        self.assertIn('test -z "${PRIVATE_REGISTRY_TOKEN}"', text)
        self.assertIn('test -n "${PRIVATE_REGISTRY_USERNAME}"', text)
        self.assertIn('test -n "${PRIVATE_REGISTRY_TOKEN}"', text)
        self.assertIn("BUILDAH_ISOLATION=chroot", text)
        self.assertNotIn("package.json", self.validate)
        self.assertNotIn("release versions are not aligned", self.validate)
        self.assertIn('version=os.environ["VERSION"]', self.prepare)
        self.assertIn('app_version=os.environ["VERSION"]', self.prepare)

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
