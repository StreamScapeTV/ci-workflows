from __future__ import annotations

import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/runner-images-validation.yml"

EXPECTED_IMAGES = ["general", "mobile", "docker", "flux-control"]
EXPECTED_PATHS = [
    ".github/workflows/runner-images-validation.yml",
    "actions/runner-image/**",
    "runner-images/general/**",
    "runner-images/mobile/**",
    "runner-images/docker/**",
    "runner-images/flux-control/**",
    "scripts/ci/runner_images.py",
    "src/ci_workflows/runner_images.py",
    "tests/test_runner_image_general.py",
    "tests/test_mobile_runner_image_assembly.py",
    "tests/test_runner_image_docker.py",
    "tests/test_runner_image_flux_control.py",
    "tests/test_runner_images_validation_workflow.py",
]


class RunnerImageValidationWorkflowTests(unittest.TestCase):
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
        self.assertEqual(EXPECTED_PATHS, triggers["pull_request"]["paths"])
        self.assertEqual("", triggers["workflow_dispatch"])
        self.assertNotIn("push", triggers)

    def test_job_uses_fixed_image_matrix_and_reviewed_buildah_capacity(self) -> None:
        self.assertEqual({"contents": "read"}, self.document["permissions"])
        self.assertEqual(
            {
                "group": "runner-images-validation-${{ github.event.pull_request.number || github.ref }}",
                "cancel-in-progress": "true",
            },
            self.document["concurrency"],
        )
        self.assertEqual(["images"], list(self.document["jobs"]))
        job = self.document["jobs"]["images"]
        self.assertEqual(
            {
                "fail-fast": "false",
                "matrix": {"image": EXPECTED_IMAGES},
            },
            job["strategy"],
        )
        self.assertEqual(["linux", "amd64", "buildah", "high"], job["runs-on"])
        self.assertEqual("180", job["timeout-minutes"])
        self.assertEqual(
            "${{ github.event_name == 'pull_request' && github.event.pull_request.head.sha || github.sha }}",
            job["env"]["SOURCE_SHA"],
        )

    def test_buildah_publication_scratch_is_proven_before_image_validation(self) -> None:
        steps = self.document["jobs"]["images"]["steps"]
        scratch_index = next(
            index
            for index, step in enumerate(steps)
            if step.get("name") == "Verify dedicated Buildah publication scratch"
        )
        shared_index = next(
            index
            for index, step in enumerate(steps)
            if step.get("name") == "Build and smoke through the shared runner-image action"
        )
        scratch = steps[scratch_index]
        self.assertLess(scratch_index, shared_index)
        self.assertEqual("bash", scratch["shell"])
        self.assertIn("push_tmp=/var/tmp/buildah", scratch["run"])
        self.assertIn('test -d "${push_tmp}"', scratch["run"])
        self.assertIn('test ! -L "${push_tmp}"', scratch["run"])
        self.assertIn('test -w "${push_tmp}"', scratch["run"])

    def test_each_matrix_entry_uses_shared_nonpublishing_action(self) -> None:
        job = self.document["jobs"]["images"]
        steps = job["steps"]
        shared = next(
            step
            for step in steps
            if step.get("name") == "Build and smoke through the shared runner-image action"
        )
        self.assertEqual("./actions/runner-image", shared["uses"])
        self.assertEqual(
            {
                "image": "${{ matrix.image }}",
                "source_sha": "${{ env.SOURCE_SHA }}",
                "publish": "false",
            },
            shared["with"],
        )
        checkout = steps[0]
        self.assertEqual(
            "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1",
            checkout["uses"],
        )
        self.assertEqual("${{ env.SOURCE_SHA }}", checkout["with"]["ref"])
        self.assertEqual("false", checkout["with"]["persist-credentials"])
        self.assertEqual("false", checkout["with"]["set-safe-directory"])

    def test_cleanup_source_and_authority_boundaries_are_terminal(self) -> None:
        job = self.document["jobs"]["images"]
        cleanup = next(
            step
            for step in job["steps"]
            if step.get("name") == "Verify runner-image credential cleanup"
        )
        self.assertEqual("always()", cleanup["if"])
        self.assertEqual("${{ matrix.image }}", cleanup["env"]["IMAGE"])
        self.assertIn("-${IMAGE}.json", cleanup["run"])
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