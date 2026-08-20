from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from ci_workflows.gradle_maven_publish import (
    GradleMavenPublishError,
    derive_release_version,
    parse_publication_tasks,
    publish,
    resolve_plan,
)


SHA = "a" * 40


class GradleMavenPublishTests(unittest.TestCase):
    def test_versions_are_stable_tag_or_source_unique_develop(self) -> None:
        self.assertEqual(
            derive_release_version(
                "1.2.3",
                github_ref="refs/tags/v1.2.3",
                expected_branch="develop",
                admitted_sha=SHA,
            ),
            "1.2.3",
        )
        self.assertEqual(
            derive_release_version(
                "1.2.3",
                github_ref="refs/heads/develop",
                expected_branch="develop",
                admitted_sha=SHA,
            ),
            "1.2.3-develop.aaaaaaaaaaaa",
        )
        for version in ("1.2", "01.2.3", "1.2.3-alpha"):
            with self.subTest(version=version), self.assertRaises(GradleMavenPublishError):
                derive_release_version(
                    version,
                    github_ref=f"refs/tags/v{version}",
                    expected_branch="develop",
                    admitted_sha=SHA,
                )
        with self.assertRaises(GradleMavenPublishError):
            derive_release_version(
                "1.2.3",
                github_ref="refs/heads/feature/not-authorized",
                expected_branch="develop",
                admitted_sha=SHA,
            )

    def test_publication_tasks_are_bounded_gradle_tasks_only(self) -> None:
        tasks = parse_publication_tasks(
            json.dumps([
                ":playback-api:publishAllPublicationsToForgejoRepository",
                ":playback-android:publishAllPublicationsToForgejoRepository",
            ])
        )
        self.assertEqual(len(tasks), 2)
        for raw in (
            "[]",
            json.dumps(["--scan"]),
            json.dumps(["publish", "publish"]),
            json.dumps(["publish;curl"]),
        ):
            with self.subTest(raw=raw), self.assertRaises(GradleMavenPublishError):
                parse_publication_tasks(raw)

    def test_plan_requires_exact_bounded_source_paths_and_authorized_ref(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            android = source / "android"
            android.mkdir(parents=True)
            wrapper = android / "gradlew"
            wrapper.write_text("#!/bin/sh\n", encoding="utf-8")
            version = source / "VERSION"
            version.write_text("1.0.0\n", encoding="utf-8")
            plan = resolve_plan(
                source_root=source,
                admitted_sha=SHA,
                github_ref="refs/heads/develop",
                expected_branch="develop",
                working_directory="android",
                gradle_wrapper_path="gradlew",
                version_file="VERSION",
                arguments_json='[":x:publishAllPublicationsToForgejoRepository"]',
            )
            self.assertEqual(plan.release_version, "1.0.0-develop.aaaaaaaaaaaa")
            self.assertEqual(plan.working_directory, android.resolve())
            with self.assertRaises(GradleMavenPublishError):
                resolve_plan(
                    source_root=source,
                    admitted_sha=SHA,
                    github_ref="refs/heads/develop",
                    expected_branch="develop",
                    working_directory="../escape",
                    gradle_wrapper_path="gradlew",
                    version_file="VERSION",
                    arguments_json='["publish"]',
                )

    def test_publish_runs_one_gradle_command_and_confines_registry_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            android = source / "android"
            android.mkdir(parents=True)
            wrapper = android / "gradlew"
            wrapper.write_text("#!/bin/sh\n", encoding="utf-8")
            (source / "VERSION").write_text("1.0.0\n", encoding="utf-8")
            plan = resolve_plan(
                source_root=source,
                admitted_sha=SHA,
                github_ref="refs/heads/develop",
                expected_branch="develop",
                working_directory="android",
                gradle_wrapper_path="gradlew",
                version_file="VERSION",
                arguments_json='[":a:publishAllPublicationsToForgejoRepository",":b:publishAllPublicationsToForgejoRepository"]',
            )
            completed = mock.Mock(returncode=0)
            with mock.patch("subprocess.run", return_value=completed) as run:
                wall_ms = publish(
                    plan,
                    environment={"PATH": os.environ.get("PATH", "/usr/bin:/bin")},
                    registry_username="publisher",
                    registry_token="secret-token",
                )
            self.assertGreaterEqual(wall_ms, 0)
            self.assertEqual(run.call_count, 1)
            command = run.call_args.args[0]
            self.assertEqual(command[0], str(wrapper.resolve()))
            self.assertIn("--no-daemon", command)
            self.assertIn("-PciMavenPublicationVersion=1.0.0-develop.aaaaaaaaaaaa", command)
            self.assertEqual(command[-2:], [
                ":a:publishAllPublicationsToForgejoRepository",
                ":b:publishAllPublicationsToForgejoRepository",
            ])
            runtime = run.call_args.kwargs["env"]
            self.assertEqual(runtime["CIW_MAVEN_REGISTRY_USERNAME"], "publisher")
            self.assertEqual(runtime["CIW_MAVEN_REGISTRY_TOKEN"], "secret-token")
            self.assertNotIn("secret-token", " ".join(command))

    def test_missing_registry_credentials_fail_before_gradle(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            android = source / "android"
            android.mkdir(parents=True)
            wrapper = android / "gradlew"
            wrapper.write_text("#!/bin/sh\n", encoding="utf-8")
            (source / "VERSION").write_text("1.0.0\n", encoding="utf-8")
            plan = resolve_plan(
                source_root=source,
                admitted_sha=SHA,
                github_ref="refs/tags/v1.0.0",
                expected_branch="develop",
                working_directory="android",
                gradle_wrapper_path="gradlew",
                version_file="VERSION",
                arguments_json='["publish"]',
            )
            with mock.patch("subprocess.run") as run, self.assertRaises(GradleMavenPublishError):
                publish(plan, environment={}, registry_username="", registry_token="")
            run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
