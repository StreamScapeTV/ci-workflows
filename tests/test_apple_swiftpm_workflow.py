from __future__ import annotations

import os
from pathlib import Path
import subprocess
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/apple-swiftpm.yml"
DISPATCH = ROOT / ".github/workflows/central-ci-dispatch.yml"
HELPER = ROOT / "scripts/ci/swiftpm_binary.py"


class AppleSwiftPMWorkflowTests(unittest.TestCase):
    def test_workflow_is_reusable_github_tagged_binary_lane(self) -> None:
        workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
        call = workflow["on"]["workflow_call"]
        self.assertEqual(
            set(call["inputs"]),
            {"repository", "ref", "source_is_tag", "ci_run_id", "upload_private_log"},
        )
        self.assertEqual(
            set(call["secrets"]),
            {
                "SOURCE_APP_ID", "SOURCE_APP_PRIVATE_KEY", "AGENT_STATE_SUPABASE_URL",
                "AGENT_STATE_SUPABASE_SECRET_KEY", "GOOGLE_DRIVE_CI_LOGS_FOLDER_ID",
                "GOOGLE_DRIVE_CLIENT_ID", "GOOGLE_DRIVE_CLIENT_SECRET", "GOOGLE_DRIVE_REFRESH_TOKEN",
                "TS_OAUTH_CLIENT_ID", "TS_OAUTH_SECRET", "PACKAGE_PUBLISH_USERNAME",
                "PACKAGE_PUBLISH_TOKEN", "PACKAGE_READ_TOKEN",
            },
        )
        job = workflow["jobs"]["publish"]
        self.assertEqual(job["runs-on"], ["macOS", "ARM64"])
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("VERSION, Package.swift and fixed product wrapper", text)
        self.assertIn("scripts/ci/run-swiftpm-binary.sh", text)
        self.assertIn("CI_SWIFTPM_BINARY_PROFILE: prepare", text)
        self.assertIn("CI_SWIFTPM_BINARY_PROFILE=consumer", text)
        self.assertIn("https://github.com/%s.git", text)
        self.assertIn("refs/tags/${{ steps.request.outputs.version }}", text)
        self.assertIn("for attempt in 1 2", text)
        self.assertNotIn("CI_APPLE_SWIFTPM_VERSION", text)
        self.assertNotIn("CI_APPLE_SWIFTPM_SDK_SOURCE_SHA", text)
        self.assertNotIn("CI_APPLE_SWIFTPM_TOOLING_SHA", text)
        self.assertNotIn("streamscape-media-2.1.5-apple-binary.zip", text)
        self.assertNotIn("https://git.faruqi.dev/mimranfaruqi/streamscape-media.git", text)
        self.assertNotIn("Vendor/", text)
        self.assertFalse({"package_url", "artifact_base_url", "package_host", "runner", "command"} & set(call["inputs"]))

    def test_gitea_is_registry_only_and_never_a_git_mutation_target(self) -> None:
        workflow_text = WORKFLOW.read_text(encoding="utf-8")
        helper_text = HELPER.read_text(encoding="utf-8")
        combined = workflow_text + "\n" + helper_text
        self.assertIn('REGISTRY_HOST = "git.faruqi.dev"', helper_text)
        self.assertIn('REGISTRY_PREFIX = ("api", "packages", "mimranfaruqi", "generic")', helper_text)
        self.assertIn('connection.putrequest("PUT", parsed.path)', helper_text)
        for forbidden in (
            "git push",
            'self.run(["git", "tag"',
            'self.run(["git", "commit"',
            "ci@git.faruqi.dev",
            "gitea-askpass",
            "git.faruqi.dev/mimranfaruqi/streamscape-media.git",
        ):
            self.assertNotIn(forbidden, combined)
        self.assertNotIn("PublicationRepository", combined)
        self.assertNotIn("PackageRepository", combined)

    def test_central_owns_artifact_transport_and_product_wrapper_gets_no_registry_secret(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")
        prepare = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]["publish"]["steps"]
        prepare_step = next(step for step in prepare if step.get("name") == "Prepare exact-source binary artifacts")
        env = prepare_step["env"]
        self.assertNotIn("PACKAGE_PUBLISH_TOKEN", env)
        self.assertNotIn("PACKAGE_READ_TOKEN", env)
        self.assertNotIn("CI_SWIFTPM_BINARY_PACKAGE_TOKEN", env)
        self.assertIn("CI_SWIFTPM_BINARY_EVIDENCE_DIR", env)
        self.assertIn("CI_SWIFTPM_BINARY_RESULT_FILE", env)
        self.assertIn("swiftpm_binary.py publish", text)
        self.assertIn("Publish and read back immutable binary cohort", text)

    def test_private_controls_cover_github_git_and_generic_artifact_reads(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("Prove unauthenticated private GitHub and artifact reads fail", text)
        self.assertIn("git -c credential.helper= ls-remote", text)
        self.assertIn("Private GitHub Swift package is readable without authentication", text)
        self.assertIn("Private Swift binary artifact is readable without authentication", text)
        self.assertIn("machine github.com", text)
        self.assertIn("machine git.faruqi.dev", text)
        self.assertIn("x-access-token", text)

    def test_release_dispatch_accepts_only_empty_swiftpm_semantics(self) -> None:
        workflow = yaml.safe_load(DISPATCH.read_text(encoding="utf-8"))
        jobs = workflow["jobs"]
        validation = next(step for step in jobs["request"]["steps"] if step.get("name") == "Validate Apple release request")
        script = validation["run"]
        accepted = subprocess.run(
            ["bash", "-c", script],
            env={**os.environ, "TEST_PROFILE": "swiftpm-package", "INPUTS_JSON": "{}"},
            capture_output=True,
            text=True,
        )
        self.assertEqual(accepted.returncode, 0, accepted.stderr)
        rejected = subprocess.run(
            ["bash", "-c", script],
            env={**os.environ, "TEST_PROFILE": "swiftpm-package", "INPUTS_JSON": '{"url":"https://example.invalid"}'},
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(rejected.returncode, 0)
        job = jobs["apple_swiftpm"]
        self.assertEqual(job["uses"], "./.github/workflows/apple-swiftpm.yml")
        self.assertEqual(set(job["with"]), {"repository", "ref", "source_is_tag", "ci_run_id"})
        self.assertEqual(job["secrets"]["PACKAGE_PUBLISH_USERNAME"], "${{ secrets.FORGEJO_REGISTRY_USERNAME }}")
        self.assertEqual(job["secrets"]["PACKAGE_PUBLISH_TOKEN"], "${{ secrets.FORGEJO_REGISTRY_TOKEN }}")
        self.assertEqual(job["secrets"]["PACKAGE_READ_TOKEN"], "${{ secrets.CIW_MAVEN_PACKAGE_READ_TOKEN }}")
        self.assertFalse(job["concurrency"]["cancel-in-progress"])

    def test_inventory_and_self_check_publish_supported_surface(self) -> None:
        inventory = yaml.safe_load((ROOT / "INVENTORY.yaml").read_text(encoding="utf-8"))
        self.assertEqual(inventory["workflows"]["apple_swiftpm"], ".github/workflows/apple-swiftpm.yml")
        self.assertEqual(inventory["scripts"]["swiftpm_binary"], "scripts/ci/swiftpm_binary.py")
        self_check = (ROOT / ".github/workflows/self-check.yml").read_text(encoding="utf-8")
        self.assertIn("tests.test_apple_swiftpm_workflow", self_check)
        self.assertIn("tests.test_swiftpm_binary", self_check)


if __name__ == "__main__":
    unittest.main()
