from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ci_workflows import hosted_runner_images as hosted


class HostedRunnerImageTests(unittest.TestCase):
    def test_decimal_docker_sizes_are_parsed(self) -> None:
        self.assertEqual(hosted.parse_docker_size("0B"), 0)
        self.assertEqual(hosted.parse_docker_size("1.5kB"), 1500)
        self.assertEqual(hosted.parse_docker_size("4.25GB"), 4_250_000_000)

    def test_layer_limit_uses_conservative_headroom(self) -> None:
        self.assertEqual(
            hosted.validate_layer_sizes(["0B", "4.25GB", "900MB"]),
            4_250_000_000,
        )
        with self.assertRaises(hosted.HostedRunnerImageError) as context:
            hosted.validate_layer_sizes(["9.6GB"])
        self.assertEqual(context.exception.code, "ghcr_layer_limit_headroom")
        with self.assertRaises(hosted.HostedRunnerImageError) as context:
            hosted.validate_layer_sizes(["10GB"])
        self.assertEqual(context.exception.code, "ghcr_layer_limit_exceeded")

    def test_only_fixed_streamscape_ghcr_runner_references_are_accepted(self) -> None:
        valid = "ghcr.io/streamscapetv/github-actions-runner-general:1.0"
        self.assertEqual(hosted.validate_ghcr_reference(valid), valid)
        for invalid in (
            "git.faruqi.dev/mimranfaruqi/github-actions-runner-general:1.0",
            "ghcr.io/other/github-actions-runner-general:1.0",
            "ghcr.io/streamscapetv/arbitrary:1.0",
        ):
            with self.subTest(invalid=invalid):
                with self.assertRaises(hosted.HostedRunnerImageError):
                    hosted.validate_ghcr_reference(invalid)

    @staticmethod
    def completed(stdout: str = "", returncode: int = 0) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess([], returncode, stdout=stdout, stderr="")

    def test_new_publication_never_builds_and_pushes_same_local_image_to_both_tags(self) -> None:
        versioned = "ghcr.io/streamscapetv/github-actions-runner-general:1.0"
        latest = "ghcr.io/streamscapetv/github-actions-runner-general:latest"
        source_sha = "a" * 40
        digest = "sha256:" + "b" * 64
        config_digest = "sha256:" + "e" * 64
        commands: list[list[str]] = []

        def run(argv, **_kwargs):
            commands.append(list(argv))
            if argv[:3] == ["docker", "image", "inspect"]:
                return self.completed(source_sha + "\n")
            return self.completed()

        with mock.patch.object(hosted, "_existing_revision", return_value=None), mock.patch.object(
            hosted, "_local_config_digest", return_value=config_digest
        ), mock.patch.object(
            hosted, "_imagetools_config_digest", return_value=config_digest
        ), mock.patch.object(
            hosted, "_imagetools_digest", side_effect=[digest, digest]
        ), mock.patch.object(hosted, "_completed", side_effect=run):
            actual = hosted.publish_exact_image(
                local_reference="ciw-runner-general:sha-aaaaaaaaaaaa",
                versioned_reference=versioned,
                latest_reference=latest,
                source_sha=source_sha,
            )

        self.assertEqual(actual, digest)
        self.assertEqual(sum(command[:2] == ["docker", "tag"] for command in commands), 2)
        self.assertEqual(sum(command[:2] == ["docker", "push"] for command in commands), 2)
        self.assertFalse(any("build" in command for command in commands))

    def test_existing_version_tag_with_different_source_fails_before_registry_write(self) -> None:
        versioned = "ghcr.io/streamscapetv/github-actions-runner-general:1.0"
        latest = "ghcr.io/streamscapetv/github-actions-runner-general:latest"
        source_sha = "a" * 40
        commands: list[list[str]] = []

        def run(argv, **_kwargs):
            commands.append(list(argv))
            return self.completed(source_sha + "\n")

        with mock.patch.object(
            hosted,
            "_existing_revision",
            return_value=("b" * 40, "sha256:" + "c" * 64, "sha256:" + "d" * 64),
        ), mock.patch.object(
            hosted, "_local_config_digest", return_value="sha256:" + "d" * 64
        ), mock.patch.object(hosted, "_completed", side_effect=run), mock.patch.object(
            hosted, "_copy_exact_manifest_to_latest"
        ) as copy_latest:
            with self.assertRaises(hosted.HostedRunnerImageError) as context:
                hosted.publish_exact_image(
                    local_reference="ciw-runner-general:sha-aaaaaaaaaaaa",
                    versioned_reference=versioned,
                    latest_reference=latest,
                    source_sha=source_sha,
                )
        self.assertEqual(context.exception.code, "immutable_release_conflict")
        copy_latest.assert_not_called()
        self.assertFalse(any(command[:2] == ["docker", "push"] for command in commands))

    def test_existing_same_source_requires_same_config_before_moving_latest(self) -> None:
        versioned = "ghcr.io/streamscapetv/github-actions-runner-general:1.0"
        latest = "ghcr.io/streamscapetv/github-actions-runner-general:latest"
        source_sha = "a" * 40
        version_digest = "sha256:" + "b" * 64
        old_config = "sha256:" + "c" * 64
        local_config = "sha256:" + "d" * 64

        with mock.patch.object(
            hosted,
            "_existing_revision",
            return_value=(source_sha, version_digest, old_config),
        ), mock.patch.object(hosted, "_local_config_digest", return_value=local_config), mock.patch.object(
            hosted, "_completed", return_value=self.completed(source_sha + "\n")
        ), mock.patch.object(hosted, "_copy_exact_manifest_to_latest") as copy_latest:
            with self.assertRaises(hosted.HostedRunnerImageError) as context:
                hosted.publish_exact_image(
                    local_reference="ciw-runner-general:sha-aaaaaaaaaaaa",
                    versioned_reference=versioned,
                    latest_reference=latest,
                    source_sha=source_sha,
                )
        self.assertEqual(context.exception.code, "immutable_release_conflict")
        copy_latest.assert_not_called()

    def test_idempotent_replay_copies_existing_exact_manifest_to_latest_without_push(self) -> None:
        versioned = "ghcr.io/streamscapetv/github-actions-runner-general:1.0"
        latest = "ghcr.io/streamscapetv/github-actions-runner-general:latest"
        source_sha = "a" * 40
        version_digest = "sha256:" + "b" * 64
        config_digest = "sha256:" + "c" * 64
        commands: list[list[str]] = []

        def run(argv, **_kwargs):
            commands.append(list(argv))
            return self.completed(source_sha + "\n")

        with mock.patch.object(
            hosted,
            "_existing_revision",
            return_value=(source_sha, version_digest, config_digest),
        ), mock.patch.object(hosted, "_local_config_digest", return_value=config_digest), mock.patch.object(
            hosted, "_completed", side_effect=run
        ), mock.patch.object(
            hosted, "_imagetools_digest", return_value=version_digest
        ), mock.patch.object(hosted, "_copy_exact_manifest_to_latest") as copy_latest:
            actual = hosted.publish_exact_image(
                local_reference="ciw-runner-general:sha-aaaaaaaaaaaa",
                versioned_reference=versioned,
                latest_reference=latest,
                source_sha=source_sha,
            )

        self.assertEqual(actual, version_digest)
        copy_latest.assert_called_once_with(
            versioned_reference=versioned,
            latest_reference=latest,
            digest=version_digest,
        )
        self.assertFalse(any(command[:2] == ["docker", "push"] for command in commands))

    def test_exact_manifest_copy_disables_index_promotion(self) -> None:
        digest = "sha256:" + "d" * 64
        with mock.patch.object(hosted, "_completed", return_value=self.completed()) as completed:
            hosted._copy_exact_manifest_to_latest(
                versioned_reference="ghcr.io/streamscapetv/github-actions-runner-general:1.0",
                latest_reference="ghcr.io/streamscapetv/github-actions-runner-general:latest",
                digest=digest,
            )
        argv = completed.call_args.args[0]
        self.assertIn("--prefer-index=false", argv)
        self.assertNotIn("docker push", " ".join(argv))

    def test_anonymous_readback_requires_version_and_latest_digest_equality(self) -> None:
        versioned = "ghcr.io/streamscapetv/github-actions-runner-general:1.0"
        latest = "ghcr.io/streamscapetv/github-actions-runner-general:latest"
        digest = "sha256:" + "d" * 64
        with mock.patch.object(hosted, "_imagetools_digest", side_effect=[digest, digest]):
            hosted.verify_anonymous_pullability(
                versioned_reference=versioned,
                latest_reference=latest,
                expected_digest=digest,
            )


if __name__ == "__main__":
    unittest.main()
