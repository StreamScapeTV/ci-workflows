from __future__ import annotations

import json
import unittest
from pathlib import Path

import yaml

from ci_workflows.validation_model import ActionsLoader

ROOT = Path(__file__).resolve().parents[1]
FOUNDATION_SHA = "70e08d4ddf8930046632a7135950e924b82e22bf"
APPLE_SHA = "88d179740145ccea00b6986d78ceb67ea365face"
OCI_SHA = "29cb88e406a0490834bd556bb825d0e227c862ac"
GITOPS_SHA = "8445e63dd9fa9468b60b6d0c61e543da9681b47b"

FOUNDATION = "issue #116 immutable private-action checkpoint"
ISSUE_104 = "issue #104 immutable private-action checkpoint"
ISSUE_125 = "issue #125 immutable private-action checkpoint"
ISSUE_131 = "issue #131 immutable Release-aware Apple checkpoint"

PRIVATE_WORKFLOWS: dict[str, dict[str, tuple[str, str]]] = {
    ".github/workflows/reusable-node.yml": {
        "StreamScapeTV/ci-workflows/actions/validate-node": (FOUNDATION_SHA, FOUNDATION),
        "StreamScapeTV/ci-workflows/actions/exact-checkout": (FOUNDATION_SHA, FOUNDATION),
        "StreamScapeTV/ci-workflows/actions/prepare-workspace": (FOUNDATION_SHA, FOUNDATION),
        "StreamScapeTV/ci-workflows/actions/render-evidence": (FOUNDATION_SHA, FOUNDATION),
        "StreamScapeTV/ci-workflows/actions/cleanup-workspace": (FOUNDATION_SHA, FOUNDATION),
    },
    ".github/workflows/reusable-android.yml": {
        "StreamScapeTV/ci-workflows/actions/validate-android": (FOUNDATION_SHA, ISSUE_104),
        "StreamScapeTV/ci-workflows/actions/exact-checkout": (FOUNDATION_SHA, FOUNDATION),
        "StreamScapeTV/ci-workflows/actions/prepare-workspace": (FOUNDATION_SHA, FOUNDATION),
        "StreamScapeTV/ci-workflows/actions/checkout-private-dependency": (FOUNDATION_SHA, ISSUE_104),
        "StreamScapeTV/ci-workflows/actions/render-evidence": (FOUNDATION_SHA, FOUNDATION),
        "StreamScapeTV/ci-workflows/actions/cleanup-workspace": (FOUNDATION_SHA, FOUNDATION),
    },
    ".github/workflows/reusable-python.yml": {
        "StreamScapeTV/ci-workflows/actions/validate-python": (FOUNDATION_SHA, ISSUE_125),
        "StreamScapeTV/ci-workflows/actions/exact-checkout": (FOUNDATION_SHA, FOUNDATION),
        "StreamScapeTV/ci-workflows/actions/prepare-workspace": (FOUNDATION_SHA, FOUNDATION),
        "StreamScapeTV/ci-workflows/actions/verify-toolchain": (FOUNDATION_SHA, ISSUE_125),
        "StreamScapeTV/ci-workflows/actions/render-evidence": (FOUNDATION_SHA, FOUNDATION),
        "StreamScapeTV/ci-workflows/actions/cleanup-workspace": (FOUNDATION_SHA, FOUNDATION),
    },
    ".github/workflows/reusable-flutter.yml": {
        "StreamScapeTV/ci-workflows/actions/validate-flutter": (FOUNDATION_SHA, ISSUE_125),
        "StreamScapeTV/ci-workflows/actions/exact-checkout": (FOUNDATION_SHA, FOUNDATION),
        "StreamScapeTV/ci-workflows/actions/prepare-workspace": (FOUNDATION_SHA, FOUNDATION),
        "StreamScapeTV/ci-workflows/actions/cleanup-workspace": (FOUNDATION_SHA, FOUNDATION),
    },
    ".github/workflows/reusable-apple.yml": {
        "StreamScapeTV/ci-workflows/actions/validate-apple": (APPLE_SHA, ISSUE_131),
        "StreamScapeTV/ci-workflows/actions/exact-checkout": (FOUNDATION_SHA, FOUNDATION),
        "StreamScapeTV/ci-workflows/actions/prepare-workspace": (FOUNDATION_SHA, FOUNDATION),
        "StreamScapeTV/ci-workflows/actions/cleanup-workspace": (FOUNDATION_SHA, FOUNDATION),
    },
    ".github/workflows/reusable-oci-build.yml": {
        "StreamScapeTV/ci-workflows/actions/validate-oci": (OCI_SHA, ISSUE_125),
        "StreamScapeTV/ci-workflows/actions/exact-checkout": (FOUNDATION_SHA, FOUNDATION),
        "StreamScapeTV/ci-workflows/actions/prepare-workspace": (FOUNDATION_SHA, FOUNDATION),
        "StreamScapeTV/ci-workflows/actions/render-evidence": (FOUNDATION_SHA, FOUNDATION),
        "StreamScapeTV/ci-workflows/actions/cleanup-workspace": (FOUNDATION_SHA, FOUNDATION),
    },
    ".github/workflows/reusable-gitops-validation.yml": {
        "StreamScapeTV/ci-workflows/actions/validate-gitops": (GITOPS_SHA, ISSUE_125),
        "StreamScapeTV/ci-workflows/actions/exact-checkout": (FOUNDATION_SHA, FOUNDATION),
        "StreamScapeTV/ci-workflows/actions/prepare-workspace": (FOUNDATION_SHA, FOUNDATION),
        "StreamScapeTV/ci-workflows/actions/render-evidence": (FOUNDATION_SHA, FOUNDATION),
        "StreamScapeTV/ci-workflows/actions/cleanup-workspace": (FOUNDATION_SHA, FOUNDATION),
    },
}

ANDROID_WORKFLOW = ".github/workflows/reusable-android.yml"


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

    def test_private_reusable_validators_use_only_locked_immutable_central_actions(self) -> None:
        locked = self.locked_actions()
        for relative, expected in PRIVATE_WORKFLOWS.items():
            with self.subTest(workflow=relative):
                source, workflow = self.load(relative)
                self.assertNotIn("actions/checkout@", source)
                self.assertNotIn("repository: ${{ job.workflow_repository }}", source)
                self.assertNotIn("repository: StreamScapeTV/ci-workflows", source)
                self.assertNotIn("ref: ${{ job.workflow_sha }}", source)
                self.assertNotIn("ref: ${{ github.workflow_sha }}", source)
                self.assertNotIn("path: .ciw", source)
                self.assertNotIn("./.ciw/actions/", source)
                self.assertNotIn("secrets: inherit", source)

                remote_helpers = {
                    str(step["uses"]).split("@", 1)[0]: str(step["uses"]).split("@", 1)[1]
                    for job in workflow["jobs"].values()
                    for step in job.get("steps", [])
                    if str(step.get("uses", "")).startswith(
                        "StreamScapeTV/ci-workflows/actions/"
                    )
                }
                self.assertEqual(set(expected), set(remote_helpers))
                for helper, (sha, release) in expected.items():
                    self.assertEqual(sha, remote_helpers[helper])
                    self.assertIn(helper, locked)
                    self.assertEqual(sha, locked[helper]["sha"])
                    self.assertEqual("composite", locked[helper]["runtime"])
                    self.assertEqual(release, locked[helper]["release"])

    def test_oci_public_input_evidence_projection_does_not_change_action_pins(self) -> None:
        source, workflow = self.load(".github/workflows/reusable-oci-build.yml")
        public_outputs = workflow["on"]["workflow_call"]["outputs"]
        job_outputs = workflow["jobs"]["build"]["outputs"]
        self.assertEqual(
            "${{ jobs.build.outputs.resolved_inputs_json }}",
            public_outputs["resolved_inputs_json"]["value"],
        )
        self.assertEqual(
            "${{ steps.execute.outputs.resolved_inputs_json }}",
            job_outputs["resolved_inputs_json"],
        )
        self.assertEqual(4, source.count(f"actions/validate-oci@{OCI_SHA}"))

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
