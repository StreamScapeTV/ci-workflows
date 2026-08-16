from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

import yaml

from ci_workflows.validation_model import ActionsLoader


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/reusable-release.yml"
PRODUCTS = ROOT / "contracts/public-workflows/products.json"
HELM_SHA = "7b17879f21fbf029708d6a404a9dd12d75503a52"


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
        self.assertEqual(self.workflow["permissions"], {"actions": "read", "contents": "write"})

    def test_release_tag_is_validated_before_checkout_and_is_source_of_truth(self) -> None:
        admit_steps = self.workflow["jobs"]["admit"]["steps"]
        self.assertEqual(admit_steps[0]["name"], "Validate release request before checkout")
        self.assertIn("RELEASE_TAG", admit_steps[0]["env"])
        self.assertIn("publish-with-github-release", admit_steps[0]["run"])
        self.assertEqual(admit_steps[1]["with"]["ref"], "refs/tags/${{ inputs.release_tag }}")
        self.assertFalse(admit_steps[1]["with"]["persist-credentials"])
        self.assertIn('test "$(git -C source rev-parse HEAD)" = "${ADMITTED_SHA}"', admit_steps[2]["run"])
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

    def test_image_publication_is_plain_buildah_push_with_bounded_cleanup(self) -> None:
        image = self.workflow["jobs"]["image"]
        self.assertEqual(image["runs-on"], ["linux", "amd64", "buildah", "high"])
        self.assertEqual(image["permissions"], {"contents": "read"})
        source = self.text
        self.assertIn('image_repository="git.faruqi.dev/mimranfaruqi/${IMAGE_NAME}"', source)
        self.assertIn("buildah bud --pull=always", source)
        self.assertIn("--platform linux/amd64,linux/arm64", source)
        self.assertIn("buildah manifest push --all", source)
        self.assertIn("--password-stdin", source)
        self.assertIn("Remove image publication state", source)
        self.assertIn("if: always()", source)
        self.assertNotIn("cleanup()", source)
        self.assertNotIn("docker build", source.casefold())
        self.assertNotIn("docker push", source.casefold())
        self.assertNotIn("skopeo inspect", source.casefold())

    def test_chart_publication_uses_issue18_action_without_reusable_nesting(self) -> None:
        chart = self.workflow["jobs"]["chart"]
        self.assertNotIn("uses", chart)
        self.assertEqual(chart["runs-on"], ["linux", "amd64", "buildah", "tiny"])
        steps = chart["steps"]
        helpers = [str(step.get("uses", "")) for step in steps]
        self.assertIn(
            f"StreamScapeTV/ci-workflows/actions/publish-helm@{HELM_SHA}",
            helpers,
        )
        self.assertGreaterEqual(
            helpers.count(f"StreamScapeTV/ci-workflows/actions/publish-helm@{HELM_SHA}"),
            3,
        )
        helm = next(step for step in steps if step.get("id") == "helm")
        self.assertEqual(helm["with"]["admitted_sha"], "${{ needs.admit.outputs.source_sha }}")
        self.assertEqual(helm["with"]["product_id"], "${{ inputs.product_id }}")
        self.assertEqual(helm["with"]["release_version"], "${{ needs.admit.outputs.version }}")
        self.assertEqual(helm["with"]["source_trust"], "trusted-exact")

    def test_github_release_is_optional_bounded_normal_api_write(self) -> None:
        release = self.workflow["jobs"]["github_release"]
        self.assertEqual(release["permissions"], {"contents": "write"})
        self.assertIn("inputs.operation == 'publish-with-github-release'", release["if"])
        run = release["steps"][0]["run"]
        self.assertLessEqual(sum(1 for line in run.splitlines() if line.strip()), 40)
        self.assertIn("curl --fail-with-body", run)
        self.assertIn("generate_release_notes", run)
        self.assertNotIn("python3 - <<", run)
        self.assertNotIn("gh release", run.casefold())
        self.assertEqual(release["steps"][1]["if"], "always()")

    def test_public_workflow_owns_no_concurrency_or_nested_reusable_workflow(self) -> None:
        self.assertNotIn("concurrency", self.workflow)
        for job in self.workflow["jobs"].values():
            self.assertFalse(
                isinstance(job, dict) and str(job.get("uses", "")).startswith("./.github/workflows/")
            )

    def test_every_inline_run_block_stays_within_harness_readability_limit(self) -> None:
        function_definition = re.compile(r"^\s*(?:function\s+)?[A-Za-z_][A-Za-z0-9_]*\s*\(\)\s*\{")
        for job_name, job in self.workflow["jobs"].items():
            for step in job.get("steps", []):
                run = step.get("run")
                if not isinstance(run, str):
                    continue
                lines = [line for line in run.splitlines() if line.strip()]
                with self.subTest(job=job_name, step=step.get("name")):
                    self.assertLessEqual(len(lines), 40)
                    self.assertFalse(any(function_definition.match(line) for line in lines))

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
