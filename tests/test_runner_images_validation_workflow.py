from __future__ import annotations

import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/runner-images-validation.yml"


class GeneralRunnerImageValidationWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = WORKFLOW.read_text(encoding="utf-8")
        cls.document = yaml.load(cls.source, Loader=yaml.BaseLoader)

    def test_trigger_is_exact_head_pull_request_or_manual_validation(self) -> None:
        triggers = self.document["on"]
        self.assertEqual(
            ["opened", "reopened", "synchronize", "ready_for_review"],
            triggers["pull_request"]["types"],
        )
        self.assertEqual(
            [
                ".github/workflows/runner-images-validation.yml",
                "actions/runner-image/**",
                "runner-images/general/**",
                "scripts/ci/runner_images.py",
                "src/ci_workflows/runner_images.py",
                "tests/test_runner_image_general.py",
                "tests/test_runner_images_validation_workflow.py",
            ],
            triggers["pull_request"]["paths"],
        )
        self.assertEqual("", triggers["workflow_dispatch"])
        self.assertNotIn("push", triggers)

    def test_job_uses_only_reviewed_buildah_capacity_and_shared_action(self) -> None:
        self.assertEqual({"contents": "read"}, self.document["permissions"])
        self.assertEqual(
            {
                "group": "runner-images-validation-${{ github.event.pull_request.number || github.ref }}",
                "cancel-in-progress": "true",
            },
            self.document["concurrency"],
        )
        self.assertEqual(["general"], list(self.document["jobs"]))
        job = self.document["jobs"]["general"]
        self.assertEqual(["linux", "amd64", "buildah", "high"], job["runs-on"])
        self.assertEqual("180", job["timeout-minutes"])
        self.assertEqual(
            "${{ github.event_name == 'pull_request' && github.event.pull_request.head.sha || github.sha }}",
            job["env"]["SOURCE_SHA"],
        )
        steps = job["steps"]
        shared = next(
            step
            for step in steps
            if step.get("name") == "Build and smoke through the shared runner-image action"
        )
        self.assertEqual("./actions/runner-image", shared["uses"])
        self.assertEqual(
            {
                "image": "general",
                "source_sha": "${{ env.SOURCE_SHA }}",
                "publish": "false",
            },
            shared["with"],
        )
        self.assertEqual(
            "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1",
            steps[0]["uses"],
        )
        self.assertEqual("${{ env.SOURCE_SHA }}", steps[0]["with"]["ref"])
        self.assertEqual("false", steps[0]["with"]["persist-credentials"])
        self.assertEqual("false", steps[0]["with"]["set-safe-directory"])

    def test_cleanup_source_and_authority_boundaries_are_terminal(self) -> None:
        self.assertIn("Verify runner-image credential cleanup\n        if: always()", self.source)
        self.assertIn("Verify exact source remained clean", self.source)
        self.assertIn("always() && !cancelled()", self.source)
        for forbidden in (
            "uses: ./.github/workflows/",
            "actions/cache",
            "upload-artifact",
            "download-artifact",
            "registry_username",
            "registry_token",
            "secrets: inherit",
            "buildah bud",
            "buildah push",
            "docker ",
            "kubectl ",
            "flux ",
            "release_tag",
            "publish: true",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, self.source)


if __name__ == "__main__":
    unittest.main()
