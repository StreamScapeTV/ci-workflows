from __future__ import annotations

import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/runner-images-validation.yml"
EXPECTED_IMAGES = ["general", "mobile", "buildah", "service", "docker", "flux-control"]
EXPECTED_PATHS = [
    ".github/workflows/runner-images-validation.yml",
    "actions/runner-image/**",
    "runner-images/general/**",
    "runner-images/mobile/**",
    "runner-images/buildah/**",
    "runner-images/service/**",
    "runner-images/docker/**",
    "runner-images/flux-control/**",
    "scripts/ci/runner_images.py",
    "src/ci_workflows/runner_images.py",
    "tests/test_runner_image_general.py",
    "tests/test_mobile_runner_image_assembly.py",
    "tests/test_service_runner_capacity.py",
    "tests/test_runner_image_docker.py",
    "tests/test_runner_image_flux_control.py",
    "tests/test_runner_image_workflow.py",
    "tests/test_runner_images_validation_workflow.py",
]


class RunnerImageValidationWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = WORKFLOW.read_text(encoding="utf-8")
        cls.document = yaml.load(cls.source, Loader=yaml.BaseLoader)

    def test_trigger_and_matrix_are_fixed(self) -> None:
        triggers = self.document["on"]
        self.assertEqual(["opened", "reopened", "synchronize", "ready_for_review"], triggers["pull_request"]["types"])
        self.assertEqual(EXPECTED_PATHS, triggers["pull_request"]["paths"])
        job = self.document["jobs"]["images"]
        self.assertEqual(job["strategy"]["matrix"]["image"], EXPECTED_IMAGES)
        self.assertEqual(job["runs-on"], ["ubuntu-latest"])
        self.assertEqual(self.document["permissions"], {"contents": "read"})

    def test_each_image_uses_shared_nonpublishing_action_and_shared_cleanup(self) -> None:
        steps = self.document["jobs"]["images"]["steps"]
        shared = next(step for step in steps if step.get("name") == "Build and smoke with shared Buildah action")
        self.assertEqual(shared["uses"], "./actions/runner-image")
        self.assertEqual(
            shared["with"],
            {"image": "${{ matrix.image }}", "source_sha": "${{ env.SOURCE_SHA }}", "publish": "false"},
        )
        cleanup = next(step for step in steps if step.get("name") == "Remove runner-image validation residue")
        self.assertEqual(cleanup["if"], "always()")
        self.assertIn("scripts/ci/runner_images.py cleanup", cleanup["run"])
        self.assertIn("Verify exact source remained clean", self.source)

    def test_validation_has_no_registry_write_or_artifact_surface(self) -> None:
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
            'publish: "true"',
        ):
            self.assertNotIn(forbidden, self.source)


if __name__ == "__main__":
    unittest.main()
