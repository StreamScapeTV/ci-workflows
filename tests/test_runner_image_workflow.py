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

    def test_plan_requires_image_source_and_uses_human_release_tag(self) -> None:
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
        self.assertEqual(
            plan.remote_reference,
            "ghcr.io/streamscapetv/github-actions-runner-general:1.0",
        )
        self.assertEqual(
            plan.latest_reference,
            "ghcr.io/streamscapetv/github-actions-runner-general:latest",
        )
        self.assertEqual(plan.smoke_command, "/usr/local/bin/runner-image-smoke")

    def test_invalid_inputs_fail_closed(self) -> None:
        with self.assertRaises(RunnerImageError):
            resolve_image("arbitrary")
        for value in ("latest", "LATEST", "bad/tag", "", "a" * 129):
            with self.subTest(value=value), self.assertRaises(RunnerImageError):
                validate_release_tag(value)
        for value in ("A" * 40, "a" * 39, "a" * 41, "not-a-sha"):
            with self.subTest(value=value), self.assertRaises(RunnerImageError):
                validate_source_sha(value)

    def test_release_outputs_keep_exact_source_and_complete_family(self) -> None:
        values = release_outputs("v2.0.0", "b" * 40)
        self.assertEqual(values["release_tag"], "v2.0.0")
        self.assertEqual(values["source_sha"], "b" * 40)
        self.assertEqual(
            values["images_json"],
            '["general","mobile","buildah","service","docker","flux-control"]',
        )


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

    def test_composite_action_is_single_hosted_docker_build_smoke_publish_path(self) -> None:
        self.assertEqual(
            set(self.action_document["inputs"]),
            {"image", "source_sha", "release_tag", "publish"},
        )
        self.assertEqual(self.action.count("docker build \\"), 1)
        for expected in (
            "Require standard GitHub-hosted Linux Docker capacity",
            "scripts/ci/runner_images.py",
            "scripts/ci/hosted_runner_images.py",
            "prepare_inputs.py",
            ".ciw-build-inputs",
            "org.opencontainers.image.source=https://github.com/StreamScapeTV/ci-workflows",
            "org.opencontainers.image.revision=${SOURCE_SHA}",
            "Run image-owned smoke as the configured image user",
            "Authenticate only to GHCR with the repository token",
            "Publish the already-smoked image and verify GHCR digests",
            "Drop GHCR authentication before public read-back",
            "Verify GHCR version and latest are anonymously readable",
            "Remove exact runner-image build and authentication state",
            "if: always()",
        ):
            self.assertIn(expected, self.action)
        for forbidden in (
            "buildah bud",
            "buildah push",
            "skopeo inspect",
            "podman ",
            "registry_username",
            "registry_token",
            "RUNNER_REGISTRY_USERNAME",
            "RUNNER_REGISTRY_TOKEN",
            "git.faruqi.dev",
            "actions/cache",
            "upload-artifact",
            "download-artifact",
        ):
            self.assertNotIn(forbidden, self.action)

    def test_optional_product_preparer_runs_only_when_present(self) -> None:
        script = self._action_step_run("Prepare optional image-owned build inputs")
        with tempfile.TemporaryDirectory() as temp:
            context = Path(temp) / "runner-images/flux-control"
            context.mkdir(parents=True)
            environment = dict(os.environ)
            environment["CONTEXT_PATH"] = str(context)

            subprocess.run(
                ["bash", "-c", script],
                cwd=ROOT,
                env=environment,
                check=True,
            )
            self.assertFalse((context / ".ciw-build-inputs").exists())
            self.assertFalse((context / "prepare-marker").exists())

            (context / "prepare_inputs.py").write_text(
                "from pathlib import Path\n"
                "root = Path(__file__).resolve().parent\n"
                "(root / '.ciw-build-inputs').mkdir()\n"
                "(root / 'prepare-marker').write_text('invoked', encoding='utf-8')\n",
                encoding="utf-8",
            )
            subprocess.run(
                ["bash", "-c", script],
                cwd=ROOT,
                env=environment,
                check=True,
            )
            self.assertEqual(
                "invoked",
                (context / "prepare-marker").read_text(encoding="utf-8"),
            )
            self.assertTrue((context / ".ciw-build-inputs").is_dir())
            self.assertFalse((context / ".ciw-build-inputs").is_symlink())

    def test_hosted_guard_and_repository_token_are_bounded(self) -> None:
        guard = self._action_step_run("Require standard GitHub-hosted Linux Docker capacity")
        self.assertIn('test "${RUNNER_ENVIRONMENT:-}" = "github-hosted"', guard)
        self.assertIn('test "${RUNNER_OS:-}" = "Linux"', guard)
        self.assertIn('test "${RUNNER_ARCH:-}" = "X64"', guard)
        self.assertIn("docker version", guard)
        self.assertIn("docker buildx version", guard)
        login = next(
            step
            for step in self.action_document["runs"]["steps"]
            if step.get("name") == "Authenticate only to GHCR with the repository token"
        )
        self.assertEqual(login["env"]["GHCR_TOKEN"], "${{ github.token }}")
        self.assertEqual(self.action.count("${{ github.token }}"), 1)
        self.assertIn("docker login ghcr.io", login["run"])

    def test_internal_leaf_is_shallow_hosted_and_has_no_private_secret_surface(self) -> None:
        self.assertIn("workflow_call", self.internal_document["on"])
        self.assertNotIn("workflow_dispatch", self.internal_document["on"])
        self.assertNotIn("pull_request", self.internal_document["on"])
        self.assertEqual(self.internal_document["jobs"]["image"]["runs-on"], "ubuntu-latest")
        self.assertEqual(
            self.internal_document["permissions"],
            {"contents": "read", "packages": "write"},
        )
        self.assertIn("uses: ./actions/runner-image", self.internal)
        self.assertNotIn("uses: ./.github/workflows/", self.internal)
        for forbidden in (
            "registry_username",
            "registry_token",
            "RUNNER_REGISTRY_USERNAME",
            "RUNNER_REGISTRY_TOKEN",
            "git.faruqi.dev",
            "buildah bud",
            "upload-artifact",
        ):
            self.assertNotIn(forbidden, self.internal)

    def test_release_is_six_independent_hosted_public_ghcr_jobs(self) -> None:
        document = self.release_document
        self.assertEqual(document["jobs"]["resolve"]["runs-on"], "ubuntu-latest")
        release_job = document["jobs"]["release"]
        self.assertEqual(release_job["runs-on"], "ubuntu-latest")
        self.assertEqual(
            release_job["permissions"],
            {"contents": "read", "packages": "write"},
        )
        self.assertEqual(
            tuple(release_job["strategy"]["matrix"]["image"]),
            EXPECTED_IMAGES,
        )
        self.assertIn("refs/tags/${RELEASE_TAG}^{commit}", self.release)
        self.assertIn("uses: ./actions/runner-image", self.release)
        self.assertIn('publish: "true"', self.release)
        self.assertIn("Record hosted feasibility and public GHCR identity", self.release)
        self.assertNotIn("uses: ./.github/workflows/internal-runner-image.yml", self.release)
        for forbidden in (
            "registry_username",
            "registry_token",
            "RUNNER_REGISTRY_USERNAME",
            "RUNNER_REGISTRY_TOKEN",
            "git.faruqi.dev",
            "actions/cache",
            "upload-artifact",
            "download-artifact",
            "buildah bud",
            "buildah push",
        ):
            self.assertNotIn(forbidden, self.release)


if __name__ == "__main__":
    unittest.main()
