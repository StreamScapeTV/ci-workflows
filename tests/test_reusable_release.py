from __future__ import annotations

import json
import unittest
from pathlib import Path

import yaml

from ci_workflows.validation_model import ActionsLoader


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/reusable-release.yml"
PRODUCTS = ROOT / "contracts/public-workflows/products.json"


class ReusableReleaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.text = WORKFLOW.read_text(encoding="utf-8")
        cls.workflow = yaml.load(cls.text, Loader=ActionsLoader)
        products = json.loads(PRODUCTS.read_text(encoding="utf-8"))
        cls.public = next(
            item
            for item in products["workflows"]
            if item["api_name"] == "release.orchestrate"
        )

    def test_public_contract_matches_simple_workflow_call(self) -> None:
        call = self.workflow["on"]["workflow_call"]
        self.assertEqual(self.public["status"], "implemented")
        self.assertEqual(self.public["file"], ".github/workflows/reusable-release.yml")
        self.assertEqual(
            {item["name"] for item in self.public["inputs"]},
            set(call["inputs"]),
        )
        self.assertEqual(set(self.public["secrets"]), set(call["secrets"]))
        self.assertEqual(set(self.public["outputs"]), set(call["outputs"]))
        self.assertEqual(self.public["supported_consumers"], ["StreamScapeTV/iptv-backend"])
        self.assertEqual(
            self.public["supported_products"],
            ["iptv-backend-image", "iptv-backend-chart"],
        )
        self.assertEqual(
            self.public["implementation_components"],
            [
                "reusable-release.yml",
                "buildah",
                "internal-release-helm-composition",
                "github-releases-api",
            ],
        )
        self.assertEqual(self.workflow["permissions"], {"actions": "read", "contents": "write"})

    def test_release_tag_is_source_of_truth_without_recovery_or_manifest_framework(self) -> None:
        self.assertIn("ref: refs/tags/${{ inputs.release_tag }}", self.text)
        self.assertIn('test "$(git -C source rev-parse HEAD)" = "${ADMITTED_SHA}"', self.text)
        self.assertIn("persist-credentials: false", self.text)
        self.assertIn("publish|publish-with-github-release", self.text)
        for retired in (
            "image_recovery_authority",
            "release_manifest",
            "provenance",
            "canary",
            "rollback",
            "resolve-release-tag",
            "skopeo",
            "readback",
            "read-back",
            "flux reconcile",
            "kubectl",
            "kubeconfig",
            "actions/cache",
            "upload-artifact",
            "download-artifact",
        ):
            self.assertNotIn(retired, self.text.casefold())

    def test_image_publication_is_plain_buildah_push_with_no_remote_proof_gate(self) -> None:
        image = self.workflow["jobs"]["image"]
        self.assertEqual(image["runs-on"], ["linux", "amd64", "buildah", "high"])
        self.assertEqual(image["permissions"], {"contents": "read"})
        source = self.text
        self.assertIn('image_repository="git.faruqi.dev/mimranfaruqi/${IMAGE_NAME}"', source)
        self.assertIn("buildah bud --pull=always", source)
        self.assertIn("--platform linux/amd64,linux/arm64", source)
        self.assertIn("buildah manifest push --all", source)
        self.assertIn("--password-stdin", source)
        self.assertNotIn("docker build", source.casefold())
        self.assertNotIn("docker push", source.casefold())
        self.assertNotIn("skopeo inspect", source.casefold())
        self.assertIn("test -z \"$(find \"${RUNNER_TEMP}\" -maxdepth 1 -name 'release-image.*'", source)

    def test_chart_publication_reuses_issue18_workflow(self) -> None:
        chart = self.workflow["jobs"]["chart"]
        self.assertEqual(chart["uses"], "./.github/workflows/reusable-helm-publish.yml")
        self.assertEqual(chart["with"]["admitted_sha"], "${{ needs.admit.outputs.source_sha }}")
        self.assertEqual(chart["with"]["product_id"], "${{ inputs.product_id }}")
        self.assertEqual(chart["with"]["release_version"], "${{ needs.admit.outputs.version }}")
        self.assertEqual(
            set(chart["secrets"]),
            {"registry_username", "registry_token"},
        )

    def test_github_release_is_optional_bounded_normal_api_write(self) -> None:
        release = self.workflow["jobs"]["github_release"]
        self.assertEqual(release["permissions"], {"contents": "write"})
        self.assertIn("inputs.operation == 'publish-with-github-release'", release["if"])
        self.assertIn('"POST"', self.text)
        self.assertIn('"PATCH"', self.text)
        self.assertIn("generate_release_notes", self.text)
        self.assertNotIn("gh release", self.text.casefold())

    def test_requested_publication_cannot_fail_silently(self) -> None:
        summary = self.workflow["jobs"]["summary"]
        self.assertEqual(summary["if"], "always()")
        run = summary["steps"][0]["run"]
        self.assertIn('test "${ADMIT_RESULT}" = success', run)
        self.assertIn('test "${IMAGE_RESULT}" = success', run)
        self.assertIn('test "${CHART_RESULT}" = success', run)
        self.assertIn('if test "${OPERATION}" = publish-with-github-release', run)
        self.assertIn('test "${RELEASE_RESULT}" = success', run)
        self.assertIn('test "${RELEASE_RESULT}" = skipped', run)


if __name__ == "__main__":
    unittest.main()
