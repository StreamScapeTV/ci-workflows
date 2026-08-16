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
        self.assertEqual({}, triggers["workflow_dispatch"])
        self.assertNotIn("push", triggers)

    def test_job_is_a_thin_nonpublishing_shared_workflow_caller(self) -> None:
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
        self.assertEqual(
            "./.github/workflows/internal-runner-image.yml",
            job["uses"],
        )
        self.assertEqual(
            {
                "image": "general",
                "source_sha": "${{ github.event_name == 'pull_request' && github.event.pull_request.head.sha || github.sha }}",
                "publish": "false",
            },
            job["with"],
        )
        for forbidden in (
            "runs-on",
            "steps",
            "container",
            "services",
            "secrets",
            "strategy",
        ):
            self.assertNotIn(forbidden, job)

    def test_caller_adds_no_cache_artifact_registry_or_deployment_authority(self) -> None:
        for forbidden in (
            "actions/cache",
            "upload-artifact",
            "download-artifact",
            "registry_username",
            "registry_token",
            "secrets: inherit",
            "buildah ",
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
