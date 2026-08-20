from __future__ import annotations

import os
import stat
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


class RunnerImageContractTests(unittest.TestCase):
    def test_fixed_six_image_family(self) -> None:
        self.assertEqual(("general", "mobile", "buildah", "service", "docker", "flux-control"), IMAGE_IDS)
        self.assertEqual(IMAGE_IDS, release_matrix())
        for image_id in IMAGE_IDS:
            image = resolve_image(image_id)
            self.assertEqual(image.context_path, f"runner-images/{image_id}")
            self.assertEqual(image.registry_repository, f"git.faruqi.dev/mimranfaruqi/github-actions-runner-{image_id}")

    def test_plan_requires_image_source_and_uses_human_release_tag(self) -> None:
        sha = "a" * 40
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            context = root / "runner-images/general"
            context.mkdir(parents=True)
            (context / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
            (context / "smoke.sh").write_text("#!/bin/sh\n", encoding="utf-8")
            plan = build_plan(root, image_id="general", source_sha=sha, release_tag="1.0")
        self.assertEqual(plan.local_reference, f"ciw-runner-general:sha-{sha[:12]}")
        self.assertEqual(plan.remote_reference, "git.faruqi.dev/mimranfaruqi/github-actions-runner-general:1.0")
        self.assertEqual(plan.latest_reference, "git.faruqi.dev/mimranfaruqi/github-actions-runner-general:latest")
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

    def test_release_outputs_keep_exact_source_and_family(self) -> None:
        values = release_outputs("v2.0.0", "b" * 40)
        self.assertEqual(values["release_tag"], "v2.0.0")
        self.assertEqual(values["source_sha"], "b" * 40)
        for image in IMAGE_IDS:
            self.assertIn(image, values["images_json"])


class RunnerImageWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.action = (ROOT / "actions/runner-image/action.yml").read_text(encoding="utf-8")
        cls.action_document = yaml.safe_load(cls.action)
        cls.internal = (ROOT / ".github/workflows/internal-runner-image.yml").read_text(encoding="utf-8")
        cls.release = (ROOT / ".github/workflows/runner-images-release.yml").read_text(encoding="utf-8")

    @classmethod
    def _action_step_run(cls, name: str) -> str:
        for step in cls.action_document["runs"]["steps"]:
            if step.get("name") == name:
                return str(step["run"])
        raise AssertionError(f"missing composite action step: {name}")

    def test_composite_action_is_the_single_build_smoke_publish_path(self) -> None:
        for expected in (
            "scripts/ci/ciw.py",
            "scripts/ci/runner_images.py",
            "prepare_inputs.py",
            ".ciw-build-inputs",
            "Prepare isolated registry authentication state",
            "buildah bud",
            "buildah from",
            "buildah run",
            "buildah push",
            "skopeo inspect",
            'rm -rf -- "${build_inputs}"',
            "Remove exact runner-image build and authentication state",
            "if: always()",
        ):
            self.assertIn(expected, self.action)
        self.assertGreaterEqual(self.action.count('--authfile "${authfile}"'), 6)
        self.assertNotIn(".config/containers/auth.json", self.action)
        for forbidden in ("actions/cache", "upload-artifact", "kubectl apply", "flux reconcile"):
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

    def test_isolated_authfile_is_created_private_and_cannot_be_reused(self) -> None:
        script = self._action_step_run("Prepare isolated registry authentication state")
        with tempfile.TemporaryDirectory() as temp:
            environment = dict(os.environ)
            environment.update(
                {
                    "RUNNER_TEMP": temp,
                    "GITHUB_RUN_ID": "123",
                    "GITHUB_RUN_ATTEMPT": "2",
                    "IMAGE": "general",
                }
            )
            subprocess.run(["bash", "-c", script], env=environment, check=True)
            authfile = Path(temp) / "ciw-runner-auth-123-2-general.json"
            self.assertEqual(authfile.read_text(encoding="utf-8"), "{}\n")
            self.assertEqual(stat.S_IMODE(authfile.stat().st_mode), 0o600)
            self.assertFalse(authfile.is_symlink())
            failed = subprocess.run(["bash", "-c", script], env=environment, check=False)
            self.assertNotEqual(failed.returncode, 0)

    def test_all_registry_reads_use_the_isolated_authfile(self) -> None:
        build = self._action_step_run("Build exact runner image")
        smoke = self._action_step_run("Run image-owned smoke as the configured image user")
        authenticate = self._action_step_run("Authenticate to the fixed runner registry")
        publish = self._action_step_run("Publish and confirm the versioned and latest tags")
        for script in (build, smoke, authenticate, publish):
            self.assertIn('test -f "${authfile}"', script)
            self.assertIn('test ! -L "${authfile}"', script)
            self.assertIn('--authfile "${authfile}"', script)
        self.assertIn("buildah bud", build)
        self.assertIn("buildah from", smoke)
        self.assertIn("buildah login", authenticate)
        self.assertEqual(publish.count("buildah push"), 2)
        self.assertEqual(publish.count("skopeo inspect"), 2)
        self.assertIn('docker://${REMOTE_REFERENCE}', publish)
        self.assertIn('docker://${LATEST_REFERENCE}', publish)
        self.assertLess(
            publish.index('docker://${REMOTE_REFERENCE}'),
            publish.index('docker://${LATEST_REFERENCE}'),
        )

    def test_publication_uses_dedicated_buildah_push_scratch_only(self) -> None:
        build = self._action_step_run("Build exact runner image")
        smoke = self._action_step_run("Run image-owned smoke as the configured image user")
        publish = self._action_step_run("Publish and confirm the versioned and latest tags")

        self.assertNotIn("/var/tmp/buildah", build)
        self.assertNotIn("/var/tmp/buildah", smoke)
        self.assertIn("push_tmp=/var/tmp/buildah", publish)
        self.assertIn('test -d "${push_tmp}"', publish)
        self.assertIn('test ! -L "${push_tmp}"', publish)
        self.assertIn('test -w "${push_tmp}"', publish)
        self.assertIn('export TMPDIR="${push_tmp}"', publish)
        self.assertIn('export TMP="${push_tmp}"', publish)
        self.assertIn('export TEMP="${push_tmp}"', publish)
        self.assertLess(publish.index('export TMPDIR="${push_tmp}"'), publish.index("buildah push"))
        self.assertEqual(publish.count("buildah push"), 2)

    def test_internal_leaf_is_shallow_and_delegates_to_composite_action(self) -> None:
        self.assertIn("workflow_call:", self.internal)
        self.assertNotIn("workflow_dispatch:", self.internal)
        self.assertNotIn("pull_request:", self.internal)
        self.assertIn("runs-on: [linux, amd64, buildah, high]", self.internal)
        self.assertIn("uses: ./actions/runner-image", self.internal)
        self.assertIn("Verify runner-image credential cleanup\n        if: always()", self.internal)
        self.assertNotIn("uses: ./.github/workflows/", self.internal)
        self.assertNotIn("buildah bud", self.internal)

    def test_release_has_fixed_repository_tag_matrix_without_reusable_nesting(self) -> None:
        self.assertIn("push:\n    tags:", self.release)
        self.assertIn('      - "*"', self.release)
        self.assertIn("workflow_dispatch:", self.release)
        self.assertIn("Existing ci-workflows Git tag to rebuild", self.release)
        self.assertIn("refs/tags/${RELEASE_TAG}^{commit}", self.release)
        self.assertIn("uses: ./actions/runner-image", self.release)
        self.assertIn("publish: \"true\"", self.release)
        self.assertIn("Verify runner-image credential cleanup\n        if: always()", self.release)
        self.assertNotIn("uses: ./.github/workflows/internal-runner-image.yml", self.release)
        for image in IMAGE_IDS:
            self.assertEqual(self.release.count(f"          - {image}"), 1)
        for forbidden in ("actions/cache", "upload-artifact", "kubectl", "flux reconcile"):
            self.assertNotIn(forbidden, self.release)


if __name__ == "__main__":
    unittest.main()
