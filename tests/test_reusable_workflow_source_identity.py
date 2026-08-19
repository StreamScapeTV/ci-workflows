from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

import yaml

from ci_workflows.validation_model import ActionsLoader

ROOT = Path(__file__).resolve().parents[1]
LOCK = ROOT / "contracts/action-tool-lock.json"
ANDROID_WORKFLOW = ".github/workflows/reusable-android.yml"
ANDROID_SHA = "ac56fd7b3fac55f231e7b2ba715a5aebebbe51ef"
APPLE_SHA = "9c17a0a72372f59e3023baaa8fab293caa6af89e"
OCI_SHA = "5c56ce21efc609059f687d2d8cd96ebcd85007cb"
SOURCE_WORKFLOW = ".github/workflows/reusable-resolve-source.yml"
SOURCE_SHA = "70e08d4ddf8930046632a7135950e924b82e22bf"
HELM_SIMPLE_SHA = "9d132cbb36bba24f3df207f9403de65927316962"
HELM_LEGACY_SHA = "70e08d4ddf8930046632a7135950e924b82e22bf"

HELM_WORKFLOWS = {
    ".github/workflows/reusable-helm-validate.yml": "StreamScapeTV/ci-workflows/actions/helm-validate",
    ".github/workflows/reusable-helm-publish.yml": "StreamScapeTV/ci-workflows/actions/helm-publish",
}


class ReusableWorkflowSourceIdentityTests(unittest.TestCase):
    def load(self, relative: str) -> tuple[str, dict]:
        source = (ROOT / relative).read_text(encoding="utf-8")
        return source, yaml.load(source, Loader=ActionsLoader)

    def locked_actions(self) -> dict[str, dict]:
        payload = json.loads(LOCK.read_text(encoding="utf-8"))
        return payload["actions"]

    def test_locked_remote_helpers_are_immutable_and_current(self) -> None:
        locked = self.locked_actions()
        for relative in sorted((ROOT / ".github/workflows").glob("reusable-*.yml")):
            source = relative.read_text(encoding="utf-8")
            remote_helpers = {
                match.group("path"): match.group("sha")
                for match in re.finditer(
                    r"uses:\s+(?P<path>StreamScapeTV/ci-workflows/actions/[A-Za-z0-9._/-]+)@(?P<sha>[0-9a-f]{40})",
                    source,
                )
            }
            expected = {
                helper: (entry["sha"], entry["release"])
                for helper, entry in locked.items()
                if helper in remote_helpers
            }
            if not expected:
                continue
            with self.subTest(workflow=relative.name):
                self.assertEqual(set(expected), set(remote_helpers))
                for helper, (sha, release) in expected.items():
                    self.assertEqual(sha, remote_helpers[helper])
                    self.assertIn(helper, locked)
                    self.assertEqual(sha, locked[helper]["sha"])
                    self.assertEqual("composite", locked[helper]["runtime"])
                    self.assertEqual(release, locked[helper]["release"])

    def test_simple_helm_reusables_use_reviewed_checkpoint_without_action_lock_requirement(self) -> None:
        locked = self.locked_actions()
        for relative, helper in HELM_WORKFLOWS.items():
            with self.subTest(workflow=relative):
                source, workflow = self.load(relative)
                uses = [str(step.get("uses", "")) for job in workflow["jobs"].values() for step in job.get("steps", [])]
                self.assertIn(f"{helper}@{HELM_SIMPLE_SHA}", uses)
                self.assertNotIn(f"{helper}@{HELM_LEGACY_SHA}", uses)
                self.assertNotIn("bootstrap_validation_runtime.py", source)
                self.assertNotIn("action-tool-lock.json", source)
                self.assertIn(helper, locked)
                self.assertNotEqual(locked[helper]["sha"], HELM_SIMPLE_SHA)

    def test_android_private_action_checkpoint_contains_media_lifecycle_policy(self) -> None:
        source, _ = self.load(ANDROID_WORKFLOW)
        policy = json.loads((ROOT / "contracts/android-source-policy.json").read_text(encoding="utf-8"))
        exception = next(item for item in policy["tracked_secret_exceptions"] if item["id"] == "streamscape_media_playback_lab_redaction_sentinels_v1")
        self.assertIn({"path": "apple/Tests/StreamscapePlaybackLabSupportTests/PlaybackLabLifecycleEvidenceTests.swift", "git_blob_sha1": "5df889bbf613ee7f4dabd07ca931aa81fb4f71a3"}, exception["paths"])
        self.assertEqual(8, source.count(f"actions/validate-android@{ANDROID_SHA}"))
        self.assertNotIn("actions/validate-android@275ee86f0f5de3d8f3330b92c84d7c0188fb10f8", source)

    def test_apple_private_action_checkpoint_contains_media_contract_in_current_tree(self) -> None:
        source, _ = self.load(".github/workflows/reusable-apple.yml")
        fragment = ROOT / "contracts/apple-validation-media-tvos-simulator-confidence.json"
        self.assertTrue(fragment.is_file())
        self.assertEqual(4, source.count(f"actions/validate-apple@{APPLE_SHA}"))
        self.assertNotIn("actions/validate-apple@293dee450e3464032d67f702b768f493abf65d7b", source)

    def test_oci_public_input_evidence_projection_does_not_change_action_pins(self) -> None:
        source, workflow = self.load(".github/workflows/reusable-oci-build.yml")
        public_outputs = workflow["on"]["workflow_call"]["outputs"]
        job_outputs = workflow["jobs"]["build"]["outputs"]
        self.assertEqual("${{ jobs.build.outputs.resolved_inputs_json }}", public_outputs["resolved_inputs_json"]["value"])
        self.assertEqual("${{ steps.execute.outputs.resolved_inputs_json }}", job_outputs["resolved_inputs_json"])
        self.assertEqual(4, source.count(f"actions/validate-oci@{OCI_SHA}"))

    def test_source_reusable_uses_locked_mode_aware_helper_checkpoint(self) -> None:
        source, workflow = self.load(SOURCE_WORKFLOW)
        self.assertEqual(
            workflow["jobs"]["resolve"]["steps"][0]["uses"],
            f"StreamScapeTV/ci-workflows/actions/resolve-source@{SOURCE_SHA}",
        )
        self.assertIn("source_mode", source)
        self.assertNotIn("source_request_profile", source)
        self.assertNotIn("bootstrap_source_request.py", source)


if __name__ == "__main__":
    unittest.main()
