from __future__ import annotations

import os
from pathlib import Path
import subprocess
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/apple-swiftpm.yml"
DISPATCH = ROOT / ".github/workflows/central-ci-dispatch.yml"


class AppleSwiftPMWorkflowTests(unittest.TestCase):
    def test_workflow_is_fixed_owner_controlled_release_lane(self) -> None:
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
        env = job["env"]
        self.assertEqual(env["CI_APPLE_SWIFTPM_VERSION"], "2.1.5")
        self.assertEqual(env["CI_APPLE_SWIFTPM_SDK_SOURCE_SHA"], "7e0eeb834758cc87c061fcedcc7bf806a3183f15")
        self.assertEqual(env["CI_APPLE_SWIFTPM_TOOLING_SHA"], "d828ba9e9ddd9237ea697ac88ba49549786001e0")
        self.assertEqual(env["CI_APPLE_SWIFTPM_PACKAGE_URL"], "https://git.faruqi.dev/mimranfaruqi/streamscape-media.git")
        self.assertEqual(env["CI_APPLE_SWIFTPM_ARTIFACT_BASE_URL"], "https://git.faruqi.dev/api/packages/mimranfaruqi/generic/streamscape-media-apple")
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertNotIn("command:", text)
        self.assertFalse({"package_url", "artifact_base_url", "package_host", "runner"} & set(call["inputs"]))

    def test_fixed_product_composer_transaction_and_consumer_are_invoked(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")
        for fixed_path in (
            "scripts/release/create-apple-swiftpm-distribution.py",
            "scripts/release/apple_swiftpm_publication.py",
            "scripts/ci/run-apple-swiftpm-consumer-validation.sh",
        ):
            self.assertIn(fixed_path, text)
        self.assertIn("publication.publish(", text)
        self.assertIn('for attempt in 1 2', text)
        self.assertIn("xcodebuild", text)
        self.assertIn("Unauthenticated private Git and binary HTTPS controls rejected as required.", text)
        self.assertNotIn("Vendor/StreamscapeMediaApple", text)

    def test_existing_aggregate_is_reused_and_not_deleted(self) -> None:
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("streamscape-media-${CI_APPLE_SWIFTPM_VERSION}-apple-binary.zip", text)
        self.assertIn("f58c72bcf56dac6d1c2288369d89e01d988e0db085f2fdd5a46b9caf5b9ca6b6", text)
        self.assertNotIn('curl -X DELETE', text)
        self.assertNotIn('git push --force', text)
        self.assertNotIn('git tag -f', text)

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

    def test_inventory_and_self_check_publish_the_supported_surface(self) -> None:
        inventory = yaml.safe_load((ROOT / "INVENTORY.yaml").read_text(encoding="utf-8"))
        self.assertEqual(inventory["workflows"]["apple_swiftpm"], ".github/workflows/apple-swiftpm.yml")
        self.assertIn("tests.test_apple_swiftpm_workflow", (ROOT / ".github/workflows/self-check.yml").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
