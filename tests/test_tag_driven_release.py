from __future__ import annotations

import os
from pathlib import Path
import subprocess
import tempfile
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]
DISPATCH = ROOT / ".github/workflows/central-ci-dispatch.yml"


class TagDrivenReleaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.workflow = yaml.safe_load(DISPATCH.read_text(encoding="utf-8"))
        self.request = self.workflow["jobs"]["request"]
        self.step = next(
            step
            for step in self.request["steps"]
            if step.get("name") == "Resolve tag-driven release identity"
        )

    def run_resolver(
        self,
        workflow_key: str,
        profile: str,
        ref: str,
        inputs_json: str = "{}",
    ) -> tuple[subprocess.CompletedProcess[str], dict[str, str], str]:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            output = root / "github-output.txt"
            completed = subprocess.run(
                ["bash", "-c", self.step["run"]],
                cwd=root,
                env={
                    **os.environ,
                    "WORKFLOW_KEY": workflow_key,
                    "TEST_PROFILE": profile,
                    "REQUEST_REF": ref,
                    "INPUTS_JSON": inputs_json,
                    "GITHUB_OUTPUT": str(output),
                },
                capture_output=True,
                text=True,
                check=False,
            )
            values: dict[str, str] = {}
            if output.is_file():
                for line in output.read_text(encoding="utf-8").splitlines():
                    key, value = line.split("=", 1)
                    values[key] = value
            normalized = (root / ".central-tag-release-inputs.json").read_text(encoding="utf-8") if (root / ".central-tag-release-inputs.json").is_file() else ""
            return completed, values, normalized

    def test_request_output_prefers_tag_normalization_without_changing_manual_requests(self) -> None:
        self.assertEqual(
            self.request["outputs"]["inputs_json"],
            "${{ steps.tag_release.outputs.inputs_json || steps.claim.outputs.inputs_json }}",
        )
        self.assertEqual(
            self.request["outputs"]["release_version"],
            "${{ steps.tag_release.outputs.release_version }}",
        )
        self.assertEqual(
            self.step["if"],
            "${{\n  steps.claim.outputs.is_tag == 'true' &&\n  (\n    steps.claim.outputs.workflow_key == 'release.android' ||\n    steps.claim.outputs.workflow_key == 'release.apple' ||\n    steps.claim.outputs.workflow_key == 'release.maven'\n  )\n}}",
        )

    def test_android_mobile_tag_normalizes_build_and_version(self) -> None:
        completed, values, normalized = self.run_resolver(
            "release.android", "play", "1.0.0_257"
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(values["inputs_json"], '{"build_number":"257"}')
        self.assertEqual(values["release_version"], "1.0.0")
        self.assertEqual(normalized, '{"build_number":"257"}\n')

    def test_apple_mobile_tag_normalizes_build_and_version(self) -> None:
        completed, values, normalized = self.run_resolver(
            "release.apple", "testflight", "1.2_17"
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(values["inputs_json"], '{"build_number":"17"}')
        self.assertEqual(values["release_version"], "1.2")
        self.assertEqual(normalized, '{"build_number":"17"}\n')

    def test_mobile_tags_fail_closed_on_malformed_identity(self) -> None:
        for workflow_key, profile in (
            ("release.android", "play"),
            ("release.apple", "testflight"),
        ):
            for tag in (
                "v1.0.0_257",
                "1.0.0_0",
                "1.0.0_build257",
                "1.0.0_257_extra",
                "1_257",
            ):
                with self.subTest(workflow_key=workflow_key, tag=tag):
                    completed, _, _ = self.run_resolver(workflow_key, profile, tag)
                    self.assertNotEqual(completed.returncode, 0)

    def test_android_tag_reserves_one_version_code_for_post_publication_bump(self) -> None:
        for build in ("2100000000", "2100000001"):
            with self.subTest(build=build):
                completed, _, _ = self.run_resolver(
                    "release.android", "play", f"1.0.0_{build}"
                )
                self.assertNotEqual(completed.returncode, 0)
                self.assertIn("post-publication next versionCode", completed.stderr)

    def test_apple_tag_reserves_one_bounded_build_for_post_publication_bump(self) -> None:
        completed, _, _ = self.run_resolver(
            "release.apple", "testflight", "1.0.0_9999999999"
        )
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("leave room for the next bounded build number", completed.stderr)

    def test_tag_release_rejects_legacy_tag_as_build_number_inputs(self) -> None:
        for workflow_key, profile, tag, raw in (
            ("release.android", "play", "1.0.0_257", '{"build_number":"1.0.0_257"}'),
            ("release.apple", "testflight", "1.0.0_257", '{"build_number":"1.0.0_257"}'),
            ("release.maven", "publish", "2.4.1", '{"build_number":"2.4.1"}'),
        ):
            with self.subTest(workflow_key=workflow_key):
                completed, _, _ = self.run_resolver(workflow_key, profile, tag, raw)
                self.assertNotEqual(completed.returncode, 0)
                self.assertIn("accepts no semantic inputs", completed.stderr)

    def test_maven_plain_tag_is_normalized_as_release_version(self) -> None:
        completed, values, normalized = self.run_resolver(
            "release.maven", "publish", "2.4.1"
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(values["inputs_json"], '{"build_number":"2.4.1"}')
        self.assertEqual(values["release_version"], "2.4.1")
        self.assertEqual(normalized, '{"build_number":"2.4.1"}\n')

    def test_store_bump_transport_rejects_wrapper_staged_change(self) -> None:
        cases = (
            (
                ROOT / ".github/workflows/android.yml",
                "Advance and publish next Android build number",
                "Android mobile build bump wrapper must not stage product files",
            ),
            (
                ROOT / ".github/workflows/apple.yml",
                "Advance and publish next Apple build number",
                "Apple mobile build bump wrapper must not stage product files",
            ),
        )
        for workflow_path, step_name, expected_error in cases:
            with self.subTest(workflow=workflow_path.name):
                workflow = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))
                command = next(
                    step["run"]
                    for job in workflow["jobs"].values()
                    for step in job.get("steps", [])
                    if step.get("name") == step_name
                )
                with tempfile.TemporaryDirectory() as temp_dir:
                    root = Path(temp_dir)
                    target = root / "central-mobile-build-bump"
                    target.mkdir()
                    subprocess.run(["git", "init", "-q", str(target)], check=True)
                    subprocess.run(
                        ["git", "-C", str(target), "config", "user.name", "CI Test"],
                        check=True,
                    )
                    subprocess.run(
                        ["git", "-C", str(target), "config", "user.email", "ci-test@example.invalid"],
                        check=True,
                    )
                    version_file = target / "version.txt"
                    version_file.write_text("1\n", encoding="utf-8")
                    subprocess.run(["git", "-C", str(target), "add", "version.txt"], check=True)
                    subprocess.run(
                        ["git", "-C", str(target), "commit", "-q", "-m", "baseline"],
                        check=True,
                    )

                    wrapper = root / "scripts/ci/advance-mobile-build.sh"
                    wrapper.parent.mkdir(parents=True)
                    wrapper.write_text(
                        """#!/usr/bin/env bash
set -Eeuo pipefail
printf '2\\n' > \"${CI_MOBILE_BUMP_WORKTREE}/version.txt\"
git -C \"${CI_MOBILE_BUMP_WORKTREE}\" add -- version.txt
""",
                        encoding="utf-8",
                    )
                    wrapper.chmod(0o755)
                    ci_log = root / "ci.log"
                    ci_log.write_text("", encoding="utf-8")

                    completed = subprocess.run(
                        ["bash", "-c", command],
                        cwd=root,
                        env={
                            **os.environ,
                            "GITHUB_WORKSPACE": str(root),
                            "CI_LOG": str(ci_log),
                            "TARGET_TOKEN": "not-used-before-staged-guard",
                            "TARGET_BRANCH": "develop",
                            "RELEASE_VERSION": "1.0.0",
                            "RELEASE_BUILD_NUMBER": "1",
                        },
                        capture_output=True,
                        text=True,
                        check=False,
                    )
                    self.assertNotEqual(completed.returncode, 0)
                    self.assertIn(expected_error, completed.stderr)
                    self.assertNotIn(
                        "already advanced after the accepted release",
                        ci_log.read_text(encoding="utf-8"),
                    )
                    staged = subprocess.run(
                        ["git", "-C", str(target), "diff", "--cached", "--name-only"],
                        capture_output=True,
                        text=True,
                        check=True,
                    ).stdout.strip()
                    self.assertEqual(staged, "version.txt")

    def test_non_store_apple_package_profiles_keep_plain_tag_semantics(self) -> None:
        for profile in ("binary-package", "swiftpm-package"):
            with self.subTest(profile=profile):
                completed, values, normalized = self.run_resolver(
                    "release.apple", profile, "2.4.1"
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)
                self.assertEqual(values["inputs_json"], "{}")
                self.assertEqual(values["release_version"], "")
                self.assertEqual(normalized, "{}\n")


if __name__ == "__main__":
    unittest.main()
