from __future__ import annotations

import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/runner-images-validation.yml"

EXPECTED_IMAGES = [
    "general",
    "mobile",
    "buildah",
    "service",
    "docker",
    "flux-control",
]
EXPECTED_PATHS = [
    ".github/workflows/runner-images-validation.yml",
    "actions/runner-image/**",
    "contracts/runner-execution-backends.json",
    "generated/runner-execution-backends.json",
    "runner-images/general/**",
    "runner-images/mobile/**",
    "runner-images/buildah/**",
    "runner-images/service/**",
    "runner-images/docker/**",
    "runner-images/flux-control/**",
    "scripts/ci/hosted_runner_images.py",
    "scripts/ci/runner_images.py",
    "src/ci_workflows/hosted_runner_images.py",
    "src/ci_workflows/runner_images.py",
    "tests/test_hosted_runner_image_action.py",
    "tests/test_hosted_runner_images.py",
    "tests/test_runner_image_general.py",
    "tests/test_mobile_runner_image_assembly.py",
    "tests/test_service_runner_capacity.py",
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

    def test_job_uses_complete_fixed_matrix_on_standard_github_hosted_linux(self) -> None:
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
        self.assertEqual("ubuntu-latest", job["runs-on"])
        self.assertEqual("180", job["timeout-minutes"])
        self.assertEqual(
            "${{ github.event_name == 'pull_request' && github.event.pull_request.head.sha || github.sha }}",
            job["env"]["SOURCE_SHA"],
        )

    def test_each_matrix_entry_builds_smokes_and_records_hosted_metrics_without_publication(self) -> None:
        job = self.document["jobs"]["images"]
        steps = job["steps"]
        shared = next(
            step
            for step in steps
            if step.get("name") == "Build and smoke on fresh GitHub-hosted capacity"
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
        metrics = next(
            step
            for step in steps
            if step.get("name") == "Record hosted feasibility measurements"
        )
        self.assertEqual(
            metrics["env"]["HOSTED_METRICS_JSON"],
            "${{ steps.image.outputs.hosted_metrics_json }}",
        )
        self.assertIn("GITHUB_STEP_SUMMARY", metrics["run"])
        checkout = steps[0]
        self.assertEqual(
            "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1",
            checkout["uses"],
        )
        self.assertEqual("${{ env.SOURCE_SHA }}", checkout["with"]["ref"])
        self.assertEqual("false", checkout["with"]["persist-credentials"])
        self.assertEqual("false", checkout["with"]["set-safe-directory"])

    def test_validation_has_zero_registry_write_secret_and_artifact_surface(self) -> None:
        self.assertIn("Verify exact source remained clean", self.source)
        self.assertIn("always() && !cancelled()", self.source)
        for forbidden in (
            "packages: write",
            "registry_username",
            "registry_token",
            "RUNNER_REGISTRY_USERNAME",
            "RUNNER_REGISTRY_TOKEN",
            "git.faruqi.dev",
            "secrets: inherit",
            "actions/cache",
            "upload-artifact",
            "download-artifact",
            "buildah bud",
            "buildah push",
            "runs-on: [linux, amd64, buildah",
            "release_tag:",
            'publish: "true"',
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, self.source)


if __name__ == "__main__":
    unittest.main()
