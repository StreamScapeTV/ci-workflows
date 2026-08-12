from __future__ import annotations

import json
import unittest
from pathlib import Path

import yaml

from ci_workflows.validation_model import ActionsLoader

ROOT = Path(__file__).resolve().parents[1]
CHECKOUT_WORKFLOWS = {
    ".github/workflows/reusable-flutter.yml": 4,
    ".github/workflows/reusable-python.yml": 2,
}
NODE_WORKFLOW = ".github/workflows/reusable-node.yml"
ANDROID_WORKFLOW = ".github/workflows/reusable-android.yml"
PRIVATE_HELPER_SHA = "70e08d4ddf8930046632a7135950e924b82e22bf"
NODE_PRIVATE_HELPERS = {
    "StreamScapeTV/ci-workflows/actions/validate-node",
    "StreamScapeTV/ci-workflows/actions/exact-checkout",
    "StreamScapeTV/ci-workflows/actions/prepare-workspace",
    "StreamScapeTV/ci-workflows/actions/render-evidence",
    "StreamScapeTV/ci-workflows/actions/cleanup-workspace",
}
ANDROID_PRIVATE_HELPERS = {
    "StreamScapeTV/ci-workflows/actions/validate-android",
    "StreamScapeTV/ci-workflows/actions/exact-checkout",
    "StreamScapeTV/ci-workflows/actions/prepare-workspace",
    "StreamScapeTV/ci-workflows/actions/checkout-private-dependency",
    "StreamScapeTV/ci-workflows/actions/render-evidence",
    "StreamScapeTV/ci-workflows/actions/cleanup-workspace",
}
ANDROID_ISSUE_HELPERS = {
    "StreamScapeTV/ci-workflows/actions/validate-android",
    "StreamScapeTV/ci-workflows/actions/checkout-private-dependency",
}


class ReusableWorkflowSourceIdentityTests(unittest.TestCase):
    @staticmethod
    def load(relative: str) -> tuple[str, dict[str, object]]:
        source = (ROOT / relative).read_text(encoding="utf-8")
        return source, yaml.load(source, Loader=ActionsLoader)

    @staticmethod
    def locked_actions() -> dict[str, dict[str, str]]:
        action_lock = json.loads(
            (ROOT / "contracts/action-tool-lock.json").read_text(encoding="utf-8")
        )
        return {
            item["uses"]: item
            for item in action_lock["third_party_actions"]
        }

    def test_checkout_based_reusable_workflows_pin_their_own_identity(self) -> None:
        for relative, expected_count in CHECKOUT_WORKFLOWS.items():
            with self.subTest(workflow=relative):
                source, workflow = self.load(relative)
                checkout_steps = [
                    step
                    for job in workflow["jobs"].values()
                    for step in job.get("steps", [])
                    if step.get("name") == "Check out exact central workflow source"
                ]
                verification_steps = [
                    step
                    for job in workflow["jobs"].values()
                    for step in job.get("steps", [])
                    if step.get("name") == "Verify exact central workflow source"
                ]
                self.assertEqual(len(checkout_steps), expected_count)
                self.assertEqual(len(verification_steps), expected_count)
                for checkout in checkout_steps:
                    self.assertEqual(
                        checkout["with"]["repository"],
                        "${{ job.workflow_repository }}",
                    )
                    self.assertEqual(
                        checkout["with"]["ref"],
                        "${{ job.workflow_sha }}",
                    )
                    self.assertEqual(checkout["with"]["persist-credentials"], False)
                    self.assertNotIn("token", checkout["with"])
                for verification in verification_steps:
                    self.assertEqual(
                        verification["env"]["EXPECTED_REPOSITORY"],
                        "${{ job.workflow_repository }}",
                    )
                    self.assertEqual(
                        verification["env"]["EXPECTED_SHA"],
                        "${{ job.workflow_sha }}",
                    )
                    self.assertIn("StreamScapeTV/ci-workflows", verification["run"])
                    self.assertIn("git rev-parse HEAD", verification["run"])
                self.assertNotIn("github.workflow_sha", source)
                self.assertNotIn("GITHUB_WORKFLOW_SHA", source)

    def test_node_uses_locked_private_action_identity_without_central_clone(self) -> None:
        source, workflow = self.load(NODE_WORKFLOW)
        steps = [
            step
            for job in workflow["jobs"].values()
            for step in job.get("steps", [])
        ]
        self.assertFalse(
            any(step.get("name") == "Check out exact central workflow source" for step in steps)
        )
        self.assertFalse(
            any(step.get("name") == "Verify exact central workflow source" for step in steps)
        )
        self.assertNotIn("actions/checkout@", source)
        self.assertNotIn("path: .ciw", source)
        self.assertNotIn("./.ciw/actions/", source)
        self.assertNotIn("secrets: inherit", source)
        self.assertNotIn("private_dependency_token", source)

        remote_helpers = {
            str(step["uses"]).split("@", 1)[0]: str(step["uses"]).split("@", 1)[1]
            for step in steps
            if str(step.get("uses", "")).startswith(
                "StreamScapeTV/ci-workflows/actions/"
            )
        }
        self.assertEqual(NODE_PRIVATE_HELPERS, set(remote_helpers))
        self.assertEqual({PRIVATE_HELPER_SHA}, set(remote_helpers.values()))

        locked = self.locked_actions()
        self.assertTrue(NODE_PRIVATE_HELPERS.issubset(locked))
        for helper in NODE_PRIVATE_HELPERS:
            entry = locked[helper]
            self.assertEqual(PRIVATE_HELPER_SHA, entry["sha"])
            self.assertEqual("composite", entry["runtime"])
            self.assertEqual(
                "issue #116 immutable private-action checkpoint", entry["release"]
            )

    def test_android_uses_locked_private_action_identity_without_central_clone(self) -> None:
        source, workflow = self.load(ANDROID_WORKFLOW)
        steps = [
            step
            for job in workflow["jobs"].values()
            for step in job.get("steps", [])
        ]
        self.assertFalse(
            any(step.get("name") == "Check out exact central workflow source" for step in steps)
        )
        self.assertFalse(
            any(step.get("name") == "Verify exact central workflow source" for step in steps)
        )
        self.assertNotIn("actions/checkout@", source)
        self.assertNotIn("path: .ciw", source)
        self.assertNotIn("./.ciw/actions/", source)
        self.assertNotIn("secrets: inherit", source)

        remote_helpers = {
            str(step["uses"]).split("@", 1)[0]: str(step["uses"]).split("@", 1)[1]
            for step in steps
            if str(step.get("uses", "")).startswith(
                "StreamScapeTV/ci-workflows/actions/"
            )
        }
        self.assertEqual(ANDROID_PRIVATE_HELPERS, set(remote_helpers))
        self.assertEqual({PRIVATE_HELPER_SHA}, set(remote_helpers.values()))

        locked = self.locked_actions()
        self.assertTrue(ANDROID_PRIVATE_HELPERS.issubset(locked))
        for helper in ANDROID_PRIVATE_HELPERS:
            entry = locked[helper]
            self.assertEqual(PRIVATE_HELPER_SHA, entry["sha"])
            self.assertEqual("composite", entry["runtime"])
            expected_release = (
                "issue #104 immutable private-action checkpoint"
                if helper in ANDROID_ISSUE_HELPERS
                else "issue #116 immutable private-action checkpoint"
            )
            self.assertEqual(expected_release, entry["release"])

    def test_private_android_consumer_cannot_control_central_source_or_credential_scope(self) -> None:
        source, workflow = self.load(ANDROID_WORKFLOW)
        public = json.loads(
            (ROOT / "contracts/public-workflows/validation.json").read_text(
                encoding="utf-8"
            )
        )
        android = next(
            item
            for item in public["workflows"]
            if item["api_name"] == "validation.android"
        )
        self.assertIn("StreamScapeTV/streamscape-media", android["supported_consumers"])
        self.assertEqual(
            set(workflow["on"]["workflow_call"].get("secrets", {})),
            {"private_dependency_token"},
        )
        self.assertNotIn("central_source", source)
        self.assertNotIn("secrets: inherit", source)
        self.assertNotIn("workflow_ref", source)
        self.assertNotIn("github.workflow", source)
        dependency = next(
            step
            for job in workflow["jobs"].values()
            for step in job.get("steps", [])
            if step.get("id") == "dependency"
        )
        self.assertEqual(
            dependency["with"]["token"],
            "${{ secrets.private_dependency_token }}",
        )
        for job in workflow["jobs"].values():
            for step in job.get("steps", []):
                if step is dependency:
                    continue
                self.assertNotIn("private_dependency_token", json.dumps(step))


if __name__ == "__main__":
    unittest.main()
