from __future__ import annotations

import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
ACTION = ROOT / "actions/runner-image/action.yml"


class HostedRunnerImageActionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = ACTION.read_text(encoding="utf-8")
        cls.document = yaml.load(cls.source, Loader=yaml.BaseLoader)
        cls.steps = cls.document["runs"]["steps"]

    def test_public_surface_has_no_private_registry_credentials(self) -> None:
        self.assertEqual(
            set(self.document["inputs"]),
            {"image", "source_sha", "release_tag", "publish"},
        )
        for token in (
            "registry_username",
            "registry_token",
            "RUNNER_REGISTRY_USERNAME",
            "RUNNER_REGISTRY_TOKEN",
            "git.faruqi.dev",
            "secrets: inherit",
        ):
            self.assertNotIn(token, self.source)

    def test_action_fails_closed_outside_standard_github_hosted_linux(self) -> None:
        guard = next(
            step
            for step in self.steps
            if step.get("name") == "Require standard GitHub-hosted Linux Docker capacity"
        )
        self.assertIn('test "${RUNNER_ENVIRONMENT:-}" = "github-hosted"', guard["run"])
        self.assertIn('test "${RUNNER_OS:-}" = "Linux"', guard["run"])
        self.assertIn('test "${RUNNER_ARCH:-}" = "X64"', guard["run"])
        self.assertIn("docker version", guard["run"])
        self.assertIn("docker buildx version", guard["run"])

    def test_build_occurs_once_and_smoke_uses_that_exact_local_image(self) -> None:
        build = next(step for step in self.steps if step.get("name") == "Build exact runner image once with Docker")
        smoke = next(step for step in self.steps if step.get("name") == "Run image-owned smoke as the configured image user")
        self.assertEqual(self.source.count("docker build \\"), 1)
        self.assertIn("--no-cache", build["run"])
        self.assertIn("--platform linux/amd64", build["run"])
        self.assertIn("org.opencontainers.image.source=https://github.com/StreamScapeTV/ci-workflows", build["run"])
        self.assertIn("org.opencontainers.image.revision=${SOURCE_SHA}", build["run"])
        self.assertIn('docker run --rm', smoke["run"])
        self.assertIn('"${LOCAL_REFERENCE}"', smoke["run"])
        for forbidden in ("buildah ", "skopeo ", "podman "):
            self.assertNotIn(forbidden, self.source)

    def test_repository_token_exists_only_on_guarded_ghcr_login_step(self) -> None:
        login = next(step for step in self.steps if step.get("name") == "Authenticate only to GHCR with the repository token")
        self.assertEqual(login["if"], "${{ inputs.publish == 'true' }}")
        self.assertEqual(login["env"]["GHCR_TOKEN"], "${{ github.token }}")
        self.assertIn("docker login ghcr.io", login["run"])
        self.assertEqual(self.source.count("${{ github.token }}"), 1)
        for name in (
            "Build exact runner image once with Docker",
            "Measure hosted image and disk feasibility",
            "Run image-owned smoke as the configured image user",
        ):
            step = next(item for item in self.steps if item.get("name") == name)
            self.assertNotIn("GHCR_TOKEN", str(step))

    def test_publish_delegates_without_rebuild_and_anonymous_readback_follows_logout(self) -> None:
        names = [step.get("name") for step in self.steps]
        publish_name = "Publish the already-smoked image and verify GHCR digests"
        logout_name = "Drop GHCR authentication before public read-back"
        anonymous_name = "Verify GHCR version and latest are anonymously readable"
        self.assertLess(names.index(publish_name), names.index(logout_name))
        self.assertLess(names.index(logout_name), names.index(anonymous_name))
        publish = next(step for step in self.steps if step.get("name") == publish_name)
        anonymous = next(step for step in self.steps if step.get("name") == anonymous_name)
        self.assertIn("hosted_runner_images.py\" publish", publish["run"])
        self.assertNotIn("docker build", publish["run"])
        self.assertIn("hosted_runner_images.py\" anonymous-readback", anonymous["run"])

    def test_cleanup_is_unconditional_and_zero_artifact(self) -> None:
        cleanup = self.steps[-1]
        self.assertEqual(cleanup["name"], "Remove exact runner-image build and authentication state")
        self.assertEqual(cleanup["if"], "always()")
        for forbidden in ("upload-artifact", "download-artifact", "actions/cache"):
            self.assertNotIn(forbidden, self.source)


if __name__ == "__main__":
    unittest.main()
