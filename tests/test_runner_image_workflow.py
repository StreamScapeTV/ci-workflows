from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

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
    def test_fixed_five_image_family(self) -> None:
        self.assertEqual(("general", "mobile", "buildah", "docker", "flux-control"), IMAGE_IDS)
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
            plan = build_plan(root, image_id="general", source_sha=sha, release_tag="v1.2.3")
        self.assertEqual(plan.local_reference, f"ciw-runner-general:sha-{sha[:12]}")
        self.assertEqual(plan.remote_reference, "git.faruqi.dev/mimranfaruqi/github-actions-runner-general:v1.2.3")
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
        cls.internal = (ROOT / ".github/workflows/internal-runner-image.yml").read_text(encoding="utf-8")
        cls.release = (ROOT / ".github/workflows/runner-images-release.yml").read_text(encoding="utf-8")

    def test_composite_action_is_the_single_build_smoke_publish_path(self) -> None:
        for expected in (
            "scripts/ci/runner_images.py",
            "buildah bud",
            "buildah from",
            "buildah run",
            "buildah push",
            "skopeo inspect",
            "Remove exact runner-image build and authentication state",
            "if: always()",
        ):
            self.assertIn(expected, self.action)
        for forbidden in ("actions/cache", "upload-artifact", "kubectl apply", "flux reconcile"):
            self.assertNotIn(forbidden, self.action)

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
