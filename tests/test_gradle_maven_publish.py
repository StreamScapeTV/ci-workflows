from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

from ci_workflows.gradle_maven_publish import (
    GradleMavenPublishError,
    cleanup_source,
    derive_release_version,
    main,
    parse_publication_tasks,
    publish,
    resolve_plan,
    verify_source_clean,
)


class GradleMavenPublishTests(unittest.TestCase):
    def _source(self, directory: str) -> tuple[Path, str]:
        source = Path(directory) / "source"
        android = source / "android"
        android.mkdir(parents=True)
        (android / "gradlew").write_text("#!/bin/sh\n", encoding="utf-8")
        (source / "VERSION").write_text("1.2.3\n", encoding="utf-8")
        (source / ".gitignore").write_text(".gradle/\nbuild/\n", encoding="utf-8")
        subprocess.run(("git", "init", "--quiet", str(source)), check=True)
        subprocess.run(
            ("git", "-C", str(source), "add", "VERSION", ".gitignore", "android/gradlew"),
            check=True,
        )
        subprocess.run(
            (
                "git",
                "-C",
                str(source),
                "-c",
                "user.email=ci@example.invalid",
                "-c",
                "user.name=CI",
                "commit",
                "--quiet",
                "-m",
                "fixture",
            ),
            check=True,
        )
        sha = subprocess.check_output(
            ("git", "-C", str(source), "rev-parse", "HEAD"), text=True
        ).strip()
        return source, sha

    def test_versions_are_develop_or_exact_version_tag_only(self) -> None:
        sha = "a" * 40
        self.assertEqual(
            derive_release_version(
                "1.2.3",
                github_ref="refs/tags/v1.2.3",
                expected_branch="develop",
                admitted_sha=sha,
            ),
            "1.2.3",
        )
        self.assertEqual(
            derive_release_version(
                "1.2.3",
                github_ref="refs/heads/develop",
                expected_branch="develop",
                admitted_sha=sha,
            ),
            "1.2.3-develop.aaaaaaaaaaaa",
        )
        for github_ref, expected_branch in (
            ("refs/heads/feature/publication", "develop"),
            ("refs/tags/v1.2.4", "develop"),
            ("refs/heads/main", "main"),
        ):
            with self.subTest(github_ref=github_ref, expected_branch=expected_branch):
                with self.assertRaises(GradleMavenPublishError):
                    derive_release_version(
                        "1.2.3",
                        github_ref=github_ref,
                        expected_branch=expected_branch,
                        admitted_sha=sha,
                    )

    def test_publication_tasks_reject_non_publication_and_shell_injection(self) -> None:
        tasks = parse_publication_tasks(
            json.dumps(
                [
                    ":playback-api:publishAllPublicationsToForgejoRepository",
                    ":playback-android:publishAllPublicationsToForgejoRepository",
                ]
            )
        )
        self.assertEqual(len(tasks), 2)
        for raw in (
            "[]",
            json.dumps(["publish"]),
            json.dumps(["--scan"]),
            json.dumps(["publish;curl"]),
            json.dumps([":module:publishAllPublicationsToForgejoRepository", "publish"]),
            json.dumps([":module:publishAllPublicationsToForgejoRepository"] * 2),
        ):
            with self.subTest(raw=raw), self.assertRaises(GradleMavenPublishError):
                parse_publication_tasks(raw)

    def test_plan_requires_exact_bounded_source_paths_and_authorized_ref(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source, sha = self._source(directory)
            plan = resolve_plan(
                source_root=source,
                admitted_sha=sha,
                github_ref="refs/heads/develop",
                expected_branch="develop",
                working_directory="android",
                gradle_wrapper_path="gradlew",
                version_file="VERSION",
                arguments_json='[":playback-android:publishAllPublicationsToForgejoRepository"]',
            )
            self.assertEqual(plan.release_version, f"1.2.3-develop.{sha[:12]}")
            self.assertEqual(plan.working_directory, (source / "android").resolve())
            with self.assertRaises(GradleMavenPublishError):
                resolve_plan(
                    source_root=source,
                    admitted_sha=sha,
                    github_ref="refs/heads/develop",
                    expected_branch="develop",
                    working_directory="../escape",
                    gradle_wrapper_path="gradlew",
                    version_file="VERSION",
                    arguments_json='[":playback-android:publishAllPublicationsToForgejoRepository"]',
                )

    def test_publish_runs_one_gradle_process_and_constrains_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source, sha = self._source(directory)
            plan = resolve_plan(
                source_root=source,
                admitted_sha=sha,
                github_ref="refs/heads/develop",
                expected_branch="develop",
                working_directory="android",
                gradle_wrapper_path="gradlew",
                version_file="VERSION",
                arguments_json='[":playback-android:publishAllPublicationsToForgejoRepository"]',
            )
            completed = mock.Mock(returncode=0)
            with mock.patch("subprocess.run", return_value=completed) as run:
                wall_ms = publish(
                    plan,
                    environment={
                        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                        "INPUT_REGISTRY_USERNAME": "publisher",
                        "INPUT_REGISTRY_TOKEN": "secret-token",
                    },
                    registry_username="publisher",
                    registry_token="secret-token",
                )
            self.assertGreaterEqual(wall_ms, 0)
            run.assert_called_once()
            command = run.call_args.args[0]
            self.assertEqual(command[0], str((source / "android" / "gradlew").resolve()))
            self.assertIn("--no-daemon", command)
            self.assertIn(f"-PciMavenPublicationVersion=1.2.3-develop.{sha[:12]}", command)
            self.assertNotIn("secret-token", command)
            runtime = run.call_args.kwargs["env"]
            self.assertEqual(runtime["FORGEJO_REGISTRY_USERNAME"], "publisher")
            self.assertEqual(runtime["FORGEJO_REGISTRY_TOKEN"], "secret-token")
            self.assertNotIn("INPUT_REGISTRY_USERNAME", runtime)
            self.assertNotIn("INPUT_REGISTRY_TOKEN", runtime)

    def test_missing_registry_credentials_fail_before_gradle(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source, sha = self._source(directory)
            plan = resolve_plan(
                source_root=source,
                admitted_sha=sha,
                github_ref="refs/tags/v1.2.3",
                expected_branch="develop",
                working_directory="android",
                gradle_wrapper_path="gradlew",
                version_file="VERSION",
                arguments_json='[":playback-android:publishAllPublicationsToForgejoRepository"]',
            )
            with mock.patch("subprocess.run") as run:
                with self.assertRaises(GradleMavenPublishError):
                    publish(plan, environment={}, registry_username="", registry_token="")
            run.assert_not_called()

    def test_cleanup_and_residue_cover_success_and_failed_publication_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source, sha = self._source(directory)
            generated = source / "build" / "publication.txt"
            generated.parent.mkdir()
            generated.write_text("generated", encoding="utf-8")
            cleanup_source(source, admitted_sha=sha)
            self.assertFalse(generated.exists())
            verify_source_clean(source, admitted_sha=sha)

            (source / "VERSION").write_text("1.2.4\n", encoding="utf-8")
            with self.assertRaises(GradleMavenPublishError):
                verify_source_clean(source, admitted_sha=sha)

    def test_plan_phase_outputs_version_without_running_gradle(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source, sha = self._source(directory)
            output = Path(directory) / "github-output"
            result = main(
                {
                    "INPUT_PHASE": "plan",
                    "INPUT_SOURCE_ROOT": str(source),
                    "INPUT_ADMITTED_SHA": sha,
                    "INPUT_EXPECTED_BRANCH": "develop",
                    "INPUT_WORKING_DIRECTORY": "android",
                    "INPUT_GRADLE_WRAPPER_PATH": "gradlew",
                    "INPUT_VERSION_FILE": "VERSION",
                    "INPUT_ARGUMENTS_JSON": '[":playback-android:publishAllPublicationsToForgejoRepository"]',
                    "GITHUB_REF": "refs/heads/develop",
                    "GITHUB_OUTPUT": str(output),
                }
            )
            self.assertEqual(result, 0)
            self.assertEqual(output.read_text(encoding="utf-8"), f"release_version=1.2.3-develop.{sha[:12]}\n")


if __name__ == "__main__":
    unittest.main()
