from __future__ import annotations

import json
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
            "${{\n  steps.claim.outputs.is_tag == 'true' &&\n  (\n    steps.claim.outputs.workflow_key == 'release.repository' ||\n    steps.claim.outputs.workflow_key == 'release.android' ||\n    steps.claim.outputs.workflow_key == 'release.apple' ||\n    steps.claim.outputs.workflow_key == 'release.maven' ||\n    steps.claim.outputs.workflow_key == 'release.library-package'\n  )\n}}",
        )

    def test_generic_repository_release_derives_only_host_and_tag_semantics(self) -> None:
        completed, values, normalized = self.run_resolver(
            "release.repository", "linux", "1.0.0_257"
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        inputs = json.loads(values["inputs_json"])
        self.assertEqual(inputs["host_os"], "linux")
        self.assertEqual(
            json.loads(inputs["semantic_inputs"]),
            {"build_identity": "1.0.0_257", "release_kind": "prepare"},
        )
        self.assertEqual(normalized, values["inputs_json"] + "\n")
        self.assertEqual(values["release_version"], "")

        invalid_host, _, _ = self.run_resolver(
            "release.repository", "windows", "1.0.0_257"
        )
        self.assertNotEqual(invalid_host.returncode, 0)
        self.assertIn("linux or macos", invalid_host.stderr)

        invalid_identity, _, _ = self.run_resolver(
            "release.repository", "linux", "release/1.0.0"
        )
        self.assertNotEqual(invalid_identity.returncode, 0)
        self.assertIn("build_identity", invalid_identity.stderr)

    def test_generic_repository_release_route_is_separate_and_legacy_compatible(self) -> None:
        jobs = self.workflow["jobs"]
        validation = jobs["repository"]
        self.assertEqual(
            validation["if"],
            "${{ needs.request.outputs.workflow_key == 'validation.repository' }}",
        )
        self.assertTrue(validation["concurrency"]["cancel-in-progress"])
        self.assertNotIn("release_authorized", validation["with"])

        release = jobs["repository_release"]
        self.assertEqual(
            release["if"],
            "${{ needs.request.outputs.workflow_key == 'release.repository' }}",
        )
        self.assertEqual(release["uses"], "./.github/workflows/repository.yml")
        self.assertEqual(release["with"]["operation"], "release")
        self.assertTrue(release["with"]["release_authorized"])
        self.assertFalse(release["concurrency"]["cancel-in-progress"])

        legacy = jobs["android_release"]
        self.assertEqual(
            legacy["if"],
            "${{ needs.request.outputs.workflow_key == 'release.android' && needs.request.outputs.test_profile == 'play' }}",
        )
        self.assertEqual(legacy["uses"], "./.github/workflows/android.yml")
        self.assertNotIn("release.repository", str(legacy))

        settlement = jobs["settle_cancelled"]
        self.assertIn("repository", settlement["needs"])
        self.assertIn("repository_release", settlement["needs"])
        self.assertIn("needs.repository_release.result == 'cancelled'", settlement["if"])

    def test_generic_repository_release_capability_binding_is_exact_and_tag_bound(self) -> None:
        repository = yaml.safe_load(
            (ROOT / ".github/workflows/repository.yml").read_text(encoding="utf-8")
        )
        step = next(
            step
            for step in repository["jobs"]["execute"]["steps"]
            if step.get("name") == "Resolve trusted private infrastructure capabilities"
        )
        self.assertEqual(step["env"]["HOST_OS"], "${{ needs.resolve_host.outputs.host_os }}")
        script = step["run"]
        for marker in (
            "capability_context=repository_lifecycle",
            'test "${run_workflow}" = release.repository',
            'test "${run_profile}" = "${HOST_OS}"',
            'test "${run_is_tag}" = true',
            'test "${run_workflow}" = validation.repository',
            'test "${run_profile}" = "${OPERATION}"',
        ):
            self.assertIn(marker, script)

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
            ("release.repository", "linux", "2.4.1", '{"build_number":"2.4.1"}'),
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

    def _store_bump_cases(self) -> tuple[tuple[Path, str], ...]:
        return (
            (ROOT / ".github/workflows/android.yml", "Android"),
            (ROOT / ".github/workflows/apple.yml", "Apple"),
        )

    def _workflow_steps(self, workflow_path: Path) -> list[dict]:
        workflow = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))
        return [
            step
            for job in workflow["jobs"].values()
            for step in job.get("steps", [])
            if step.get("name")
        ]

    def _step_command(self, workflow_path: Path, name: str) -> str:
        return next(
            step["run"]
            for step in self._workflow_steps(workflow_path)
            if step.get("name") == name
        )

    def _run_isolated_product_wrapper(
        self,
        workflow_path: Path,
        platform: str,
        wrapper_body: str,
        *,
        extra_files: dict[str, str] | None = None,
    ) -> tuple[subprocess.CompletedProcess[str], str, Path, Path, tempfile.TemporaryDirectory[str]]:
        temp = tempfile.TemporaryDirectory()
        root = Path(temp.name)
        runner = root / "runner"
        runner.mkdir()
        staging = runner / "central-mobile-build-bump-stage"
        staging.mkdir()
        (staging / "version.txt").write_text("1\n", encoding="utf-8")
        for relative, value in (extra_files or {}).items():
            target = staging / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(value, encoding="utf-8")

        wrapper = root / "scripts/ci/advance-mobile-build.sh"
        wrapper.parent.mkdir(parents=True)
        wrapper.write_text(wrapper_body, encoding="utf-8")
        wrapper.chmod(0o755)
        ci_log = root / "ci.log"
        ci_log.write_text("", encoding="utf-8")
        command = self._step_command(
            workflow_path,
            f"Run product-owned {platform} build bump in isolated staging tree",
        )
        completed = subprocess.run(
            ["bash", "-c", command],
            cwd=root,
            env={
                **os.environ,
                "GITHUB_WORKSPACE": str(root),
                "RUNNER_TEMP": str(runner),
                "CI_LOG": str(ci_log),
                "TARGET_TOKEN": "write-token-must-not-reach-wrapper",
                "GH_TOKEN": "source-token-must-not-reach-wrapper",
                "GITHUB_TOKEN": "github-token-must-not-reach-wrapper",
                "RELEASE_VERSION": "1.0.0",
                "RELEASE_BUILD_NUMBER": "1",
            },
            capture_output=True,
            text=True,
            check=False,
        )
        return completed, ci_log.read_text(encoding="utf-8"), root, staging, temp

    def _create_validation_checkout(
        self,
        root: Path,
        *,
        extra_files: dict[str, str] | None = None,
    ) -> tuple[Path, str]:
        target = root / "central-mobile-build-bump"
        target.mkdir()
        subprocess.run(["git", "init", "-q", str(target)], check=True)
        subprocess.run(["git", "-C", str(target), "config", "user.name", "CI Test"], check=True)
        subprocess.run(
            ["git", "-C", str(target), "config", "user.email", "ci-test@example.invalid"],
            check=True,
        )
        (target / "version.txt").write_text("1\n", encoding="utf-8")
        for relative, value in (extra_files or {}).items():
            path = target / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(value, encoding="utf-8")
        subprocess.run(["git", "-C", str(target), "add", "."], check=True)
        subprocess.run(["git", "-C", str(target), "commit", "-q", "-m", "baseline"], check=True)
        head = subprocess.run(
            ["git", "-C", str(target), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        return target, head

    def _run_validation(
        self,
        workflow_path: Path,
        platform: str,
        root: Path,
        expected_head: str,
    ) -> tuple[subprocess.CompletedProcess[str], dict[str, str]]:
        output = root / "github-output.txt"
        output.write_text("", encoding="utf-8")
        command = self._step_command(workflow_path, f"Validate {platform} staged build bump output")
        completed = subprocess.run(
            ["bash", "-c", command],
            cwd=root,
            env={
                **os.environ,
                "GITHUB_WORKSPACE": str(root),
                "RUNNER_TEMP": str(root / "runner"),
                "GITHUB_OUTPUT": str(output),
                "CI_LOG": str(root / "ci.log"),
                "EXPECTED_HEAD": expected_head,
            },
            capture_output=True,
            text=True,
            check=False,
        )
        values: dict[str, str] = {}
        for line in output.read_text(encoding="utf-8").splitlines():
            key, value = line.split("=", 1)
            values[key] = value
        return completed, values

    def test_store_bump_transport_validates_product_output_before_write_token(self) -> None:
        for workflow_path, platform in self._store_bump_cases():
            with self.subTest(workflow=workflow_path.name):
                steps = self._workflow_steps(workflow_path)
                names = [step["name"] for step in steps]
                stage_checkout = names.index(f"Check out {platform} development branch for isolated bump staging")
                stage = names.index(f"Prepare Git-metadata-free {platform} build bump staging")
                product = names.index(f"Run product-owned {platform} build bump in isolated staging tree")
                checkout = names.index(f"Check out current {platform} development branch for post-publication bump")
                validate = names.index(f"Validate {platform} staged build bump output")
                token = names.index(f"Create post-publication {platform} build bump token")
                publish = names.index(f"Advance and publish next {platform} build number")
                self.assertLess(stage_checkout, stage)
                self.assertLess(stage, product)
                self.assertLess(product, checkout)
                self.assertLess(checkout, validate)
                self.assertLess(validate, token)
                self.assertLess(token, publish)

                by_name = {step["name"]: step for step in steps}
                workflow = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))
                source_job = "ci" if platform == "Android" else "execute"
                source_token = next(
                    step
                    for step in workflow["jobs"][source_job]["steps"]
                    if step.get("name") == "Prepare source token"
                )
                self.assertEqual(source_token["with"]["permission-contents"], "read")
                self.assertEqual(source_token["with"]["permission-metadata"], "read")
                for checkout_name in (
                    f"Check out {platform} development branch for isolated bump staging",
                    f"Check out current {platform} development branch for post-publication bump",
                ):
                    checkout_step = by_name[checkout_name]
                    self.assertEqual(checkout_step["with"]["token"], "${{ steps.source.outputs.token || github.token }}")
                    self.assertFalse(checkout_step["with"]["persist-credentials"])
                token_step = by_name[f"Create post-publication {platform} build bump token"]
                self.assertIn("steps.mobile_bump_validate.outcome == 'success'", token_step["if"])
                product_script = by_name[f"Run product-owned {platform} build bump in isolated staging tree"]["run"]
                self.assertIn("env -u TARGET_TOKEN -u GH_TOKEN -u GITHUB_TOKEN -u GITHUB_ENV -u GITHUB_PATH -u GITHUB_OUTPUT -u GITHUB_STATE", product_script)
                self.assertIn("must not create Git metadata", product_script)
                publish_script = by_name[f"Advance and publish next {platform} build number"]["run"]
                self.assertIn("GIT_CONFIG_NOSYSTEM=1", publish_script)
                self.assertIn("GIT_CONFIG_GLOBAL=/dev/null", publish_script)
                self.assertIn("-c core.hooksPath=/dev/null", publish_script)
                self.assertIn("push --porcelain", publish_script)
                self.assertNotIn("--force", publish_script)

    def test_store_bump_isolated_wrapper_cannot_observe_write_token_and_imports_one_file(self) -> None:
        wrapper_body = """#!/usr/bin/env bash
set -Eeuo pipefail
if test -n "${TARGET_TOKEN:-}" || test -n "${GH_TOKEN:-}" || test -n "${GITHUB_TOKEN:-}"; then
  printf '%s\n' 'wrapper-observed-token'
  exit 97
fi
printf '%s\n' 'wrapper-token-boundary-ok'
printf '2\n' > "${CI_MOBILE_BUMP_WORKTREE}/version.txt"
"""
        for workflow_path, platform in self._store_bump_cases():
            with self.subTest(workflow=workflow_path.name):
                completed, ci_log, root, _, temp = self._run_isolated_product_wrapper(
                    workflow_path, platform, wrapper_body
                )
                try:
                    self.assertEqual(completed.returncode, 0, completed.stderr)
                    self.assertIn("wrapper-token-boundary-ok", ci_log)
                    self.assertNotIn("wrapper-observed-token", ci_log)
                    target, head = self._create_validation_checkout(root)
                    validated, outputs = self._run_validation(workflow_path, platform, root, head)
                    self.assertEqual(validated.returncode, 0, validated.stderr)
                    self.assertEqual(outputs, {"changed": "true", "canonical_path": "version.txt"})
                    self.assertEqual((target / "version.txt").read_text(encoding="utf-8"), "2\n")
                    self.assertEqual(
                        subprocess.run(
                            ["git", "-C", str(target), "diff", "--name-only"],
                            capture_output=True,
                            text=True,
                            check=True,
                        ).stdout.strip(),
                        "version.txt",
                    )
                finally:
                    temp.cleanup()

    def test_store_bump_idempotent_noop_stays_clean_without_transport_delta(self) -> None:
        wrapper_body = """#!/usr/bin/env bash
set -Eeuo pipefail
printf '%s\n' 'already-advanced-noop'
"""
        for workflow_path, platform in self._store_bump_cases():
            with self.subTest(workflow=workflow_path.name):
                completed, ci_log, root, _, temp = self._run_isolated_product_wrapper(
                    workflow_path, platform, wrapper_body
                )
                try:
                    self.assertEqual(completed.returncode, 0, completed.stderr)
                    self.assertIn("already-advanced-noop", ci_log)
                    target, head = self._create_validation_checkout(root)
                    validated, outputs = self._run_validation(workflow_path, platform, root, head)
                    self.assertEqual(validated.returncode, 0, validated.stderr)
                    self.assertEqual(outputs, {"changed": "false", "canonical_path": ""})
                    self.assertEqual(
                        subprocess.run(
                            ["git", "-C", str(target), "status", "--porcelain", "--untracked-files=all"],
                            capture_output=True,
                            text=True,
                            check=True,
                        ).stdout,
                        "",
                    )
                finally:
                    temp.cleanup()

    def test_store_bump_rejects_assume_unchanged_index_attack(self) -> None:
        wrapper_body = """#!/usr/bin/env bash
set -Eeuo pipefail
git -C "${CI_MOBILE_BUMP_WORKTREE}" init -q
git -C "${CI_MOBILE_BUMP_WORKTREE}" config user.name Wrapper
git -C "${CI_MOBILE_BUMP_WORKTREE}" config user.email wrapper@example.invalid
git -C "${CI_MOBILE_BUMP_WORKTREE}" add version.txt
git -C "${CI_MOBILE_BUMP_WORKTREE}" commit -q -m baseline
git -C "${CI_MOBILE_BUMP_WORKTREE}" update-index --assume-unchanged version.txt
printf '2\n' > "${CI_MOBILE_BUMP_WORKTREE}/version.txt"
"""
        for workflow_path, platform in self._store_bump_cases():
            with self.subTest(workflow=workflow_path.name):
                completed, ci_log, _, _, temp = self._run_isolated_product_wrapper(
                    workflow_path, platform, wrapper_body
                )
                try:
                    self.assertNotEqual(completed.returncode, 0)
                    self.assertIn(
                        f"{platform} mobile build bump wrapper must not create Git metadata",
                        completed.stderr,
                    )
                    self.assertNotIn("already advanced after the accepted release", ci_log)
                finally:
                    temp.cleanup()

    def test_store_bump_rejects_product_created_pre_push_hook(self) -> None:
        wrapper_body = """#!/usr/bin/env bash
set -Eeuo pipefail
mkdir -p "${CI_MOBILE_BUMP_WORKTREE}/.git/hooks"
printf '%s\n' '#!/usr/bin/env bash' 'echo hook-ran >&2' > "${CI_MOBILE_BUMP_WORKTREE}/.git/hooks/pre-push"
chmod +x "${CI_MOBILE_BUMP_WORKTREE}/.git/hooks/pre-push"
printf '2\n' > "${CI_MOBILE_BUMP_WORKTREE}/version.txt"
"""
        for workflow_path, platform in self._store_bump_cases():
            with self.subTest(workflow=workflow_path.name):
                completed, _, _, _, temp = self._run_isolated_product_wrapper(
                    workflow_path, platform, wrapper_body
                )
                try:
                    self.assertNotEqual(completed.returncode, 0)
                    self.assertIn(
                        f"{platform} mobile build bump wrapper must not create Git metadata",
                        completed.stderr,
                    )
                finally:
                    temp.cleanup()

    def test_store_bump_validation_rejects_multiple_file_output(self) -> None:
        wrapper_body = """#!/usr/bin/env bash
set -Eeuo pipefail
printf '2\n' > "${CI_MOBILE_BUMP_WORKTREE}/version.txt"
printf 'changed\n' > "${CI_MOBILE_BUMP_WORKTREE}/other.txt"
"""
        for workflow_path, platform in self._store_bump_cases():
            with self.subTest(workflow=workflow_path.name):
                completed, _, root, _, temp = self._run_isolated_product_wrapper(
                    workflow_path, platform, wrapper_body, extra_files={"other.txt": "base\n"}
                )
                try:
                    self.assertEqual(completed.returncode, 0, completed.stderr)
                    _, head = self._create_validation_checkout(root, extra_files={"other.txt": "base\n"})
                    validated, _ = self._run_validation(workflow_path, platform, root, head)
                    self.assertNotEqual(validated.returncode, 0)
                    self.assertIn(
                        f"{platform} mobile build bump changed more than one tracked product file",
                        validated.stderr,
                    )
                finally:
                    temp.cleanup()

    def test_library_package_tag_keeps_plain_semver_and_no_inputs(self) -> None:
        completed, values, normalized = self.run_resolver(
            "release.library-package", "publish", "2.4.1"
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(values["inputs_json"], "{}")
        self.assertEqual(values["release_version"], "2.4.1")
        self.assertEqual(normalized, "{}\n")

        for tag in ("v2.4.1", "2.4", "2.4.1_7", "latest"):
            with self.subTest(tag=tag):
                rejected, _, _ = self.run_resolver(
                    "release.library-package", "publish", tag
                )
                self.assertNotEqual(rejected.returncode, 0)

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
