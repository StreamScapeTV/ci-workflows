from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml

from ci_workflows.runner_images import (
    IMAGE_IDS,
    RunnerImageError,
    build_plan,
    cleanup_runner_state,
    release_matrix,
    release_outputs,
    resolve_image,
    validate_release_tag,
    validate_source_sha,
)

ROOT = Path(__file__).resolve().parents[1]
EXPECTED_IMAGES = (
    "general",
    "mobile",
    "buildah",
    "service",
    "docker",
    "flux-control",
)


class RunnerImageContractTests(unittest.TestCase):
    def test_fixed_six_image_family_targets_public_streamscape_ghcr(self) -> None:
        self.assertEqual(EXPECTED_IMAGES, IMAGE_IDS)
        self.assertEqual(IMAGE_IDS, release_matrix())
        for image_id in IMAGE_IDS:
            image = resolve_image(image_id)
            self.assertEqual(image.context_path, f"runner-images/{image_id}")
            self.assertEqual(
                image.registry_repository,
                f"ghcr.io/streamscapetv/github-actions-runner-{image_id}",
            )

    def test_plan_uses_exact_source_and_human_release_tag(self) -> None:
        sha = "a" * 40
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            context = root / "runner-images/general"
            context.mkdir(parents=True)
            (context / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
            (context / "smoke.sh").write_text("#!/bin/sh\n", encoding="utf-8")
            plan = build_plan(root, image_id="general", source_sha=sha, release_tag="1.0")
        self.assertEqual(plan.registry_host, "ghcr.io")
        self.assertEqual(plan.local_reference, f"ciw-runner-general:sha-{sha[:12]}")
        self.assertEqual(plan.remote_reference, "ghcr.io/streamscapetv/github-actions-runner-general:1.0")
        self.assertEqual(plan.latest_reference, "ghcr.io/streamscapetv/github-actions-runner-general:latest")

    def test_invalid_inputs_fail_closed(self) -> None:
        with self.assertRaises(RunnerImageError):
            resolve_image("arbitrary")
        for value in ("latest", "LATEST", "bad/tag", "", "a" * 129):
            with self.subTest(value=value), self.assertRaises(RunnerImageError):
                validate_release_tag(value)
        for value in ("A" * 40, "a" * 39, "a" * 41, "not-a-sha"):
            with self.subTest(value=value), self.assertRaises(RunnerImageError):
                validate_source_sha(value)

    def test_cleanup_is_reusable_and_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            workspace = root / "workspace"
            runner_temp = root / "temp"
            context = workspace / "runner-images/general"
            context.mkdir(parents=True)
            runner_temp.mkdir()
            (context / ".ciw-build-inputs").mkdir()
            (runner_temp / "ciw-buildah-push-123-1-general").mkdir()
            (runner_temp / "ciw-runner-auth-123-1-general.json").write_text("{}\n")
            (runner_temp / "ciw-runner-anon-123-1-general.json").write_text("{}\n")
            for _ in range(2):
                cleanup_runner_state(
                    image_id="general",
                    workspace=workspace,
                    runner_temp=runner_temp,
                    run_id="123",
                    run_attempt="1",
                )
            self.assertFalse((context / ".ciw-build-inputs").exists())
            self.assertFalse((runner_temp / "ciw-buildah-push-123-1-general").exists())

    def test_release_outputs_keep_exact_source_and_complete_family(self) -> None:
        values = release_outputs("v2.0.0", "b" * 40)
        self.assertEqual(values["release_tag"], "v2.0.0")
        self.assertEqual(values["source_sha"], "b" * 40)
        self.assertEqual(values["images_json"], '["general","mobile","buildah","service","docker","flux-control"]')


class RunnerImageWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.action = (ROOT / "actions/runner-image/action.yml").read_text(encoding="utf-8")
        cls.action_document = yaml.safe_load(cls.action)
        cls.internal = (ROOT / ".github/workflows/internal-runner-image.yml").read_text(encoding="utf-8")
        cls.internal_document = yaml.safe_load(cls.internal)
        cls.release = (ROOT / ".github/workflows/runner-images-release.yml").read_text(encoding="utf-8")
        cls.release_document = yaml.safe_load(cls.release)

    @classmethod
    def _action_step_run(cls, name: str) -> str:
        for step in cls.action_document["runs"]["steps"]:
            if step.get("name") == name:
                return str(step["run"])
        raise AssertionError(f"missing composite action step: {name}")

    def test_shared_action_uses_hosted_buildah_and_ghcr_only(self) -> None:
        self.assertEqual(set(self.action_document["inputs"]), {"image", "source_sha", "release_tag", "publish"})
        self.assertEqual(self.action.count("buildah bud \\"), 1)
        for expected in (
            "Require standard GitHub-hosted Buildah capacity",
            "buildah bud",
            "buildah from",
            "buildah run",
            "buildah login",
            "buildah push",
            "skopeo inspect",
            "ghcr.io",
            "${{ github.token }}",
            "Run image-owned smoke from the same local image",
            "Remove exact runner-image build and authentication state",
        ):
            self.assertIn(expected, self.action)
        for forbidden in (
            "docker build",
            "docker buildx",
            "docker push",
            "registry_username",
            "registry_token",
            "RUNNER_REGISTRY_USERNAME",
            "RUNNER_REGISTRY_TOKEN",
            "git.faruqi.dev",
            "upload-artifact",
            "download-artifact",
        ):
            self.assertNotIn(forbidden, self.action)

    def test_optional_product_preparer_remains_shared(self) -> None:
        script = self._action_step_run("Prepare optional image-owned build inputs")
        with tempfile.TemporaryDirectory() as temp:
            context = Path(temp) / "runner-images/flux-control"
            context.mkdir(parents=True)
            environment = dict(os.environ)
            environment["CONTEXT_PATH"] = str(context)
            subprocess.run(["bash", "-c", script], cwd=ROOT, env=environment, check=True)
            self.assertFalse((context / ".ciw-build-inputs").exists())

    def test_internal_leaf_is_thin_and_hosted(self) -> None:
        self.assertIn("workflow_call", self.internal_document["on"])
        self.assertEqual(self.internal_document["jobs"]["image"]["runs-on"], ["ubuntu-latest"])
        self.assertIn("uses: ./actions/runner-image", self.internal)
        self.assertIn("scripts/ci/runner_images.py cleanup", self.internal)
        self.assertNotIn("registry_username", self.internal)
        self.assertNotIn("git.faruqi.dev", self.internal)

    def test_release_is_six_hosted_ghcr_jobs_without_private_credentials(self) -> None:
        document = self.release_document
        self.assertEqual(document["jobs"]["resolve"]["runs-on"], ["ubuntu-latest"])
        release_job = document["jobs"]["release"]
        self.assertEqual(release_job["runs-on"], ["ubuntu-latest"])
        self.assertEqual(release_job["permissions"], {"contents": "read", "packages": "write"})
        self.assertEqual(tuple(release_job["strategy"]["matrix"]["image"]), EXPECTED_IMAGES)
        self.assertIn("uses: ./actions/runner-image", self.release)
        self.assertIn('publish: "true"', self.release)
        self.assertIn("scripts/ci/runner_images.py cleanup", self.release)
        for forbidden in (
            "registry_username",
            "registry_token",
            "RUNNER_REGISTRY_USERNAME",
            "RUNNER_REGISTRY_TOKEN",
            "git.faruqi.dev",
            "upload-artifact",
            "download-artifact",
            "docker build",
        ):
            self.assertNotIn(forbidden, self.release)


if __name__ == "__main__":
    unittest.main()
