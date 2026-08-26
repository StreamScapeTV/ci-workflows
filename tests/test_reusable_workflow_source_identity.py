from __future__ import annotations

import json
import unittest
from pathlib import Path

import yaml

from ci_workflows.validation_model import ActionsLoader

ROOT = Path(__file__).resolve().parents[1]
RESOLVE_EXECUTION_BACKEND = "StreamScapeTV/ci-workflows/actions/resolve-execution-backend"

PRIVATE_WORKFLOWS: dict[str, set[str]] = {
    ".github/workflows/reusable-node.yml": {
        "StreamScapeTV/ci-workflows/actions/validate-node",
        "StreamScapeTV/ci-workflows/actions/exact-checkout",
        "StreamScapeTV/ci-workflows/actions/prepare-workspace",
        "StreamScapeTV/ci-workflows/actions/render-evidence",
        "StreamScapeTV/ci-workflows/actions/cleanup-workspace",
    },
    ".github/workflows/reusable-android.yml": {
        "StreamScapeTV/ci-workflows/actions/validate-android",
        RESOLVE_EXECUTION_BACKEND,
        "StreamScapeTV/ci-workflows/actions/warm-gradle-dependencies",
        "StreamScapeTV/ci-workflows/actions/upload-gradle-seed",
        "StreamScapeTV/ci-workflows/actions/exact-checkout",
        "StreamScapeTV/ci-workflows/actions/prepare-workspace",
        "StreamScapeTV/ci-workflows/actions/checkout-private-dependency",
        "StreamScapeTV/ci-workflows/actions/render-evidence",
        "StreamScapeTV/ci-workflows/actions/cleanup-workspace",
    },
    ".github/workflows/reusable-python.yml": {
        "StreamScapeTV/ci-workflows/actions/validate-python",
        "StreamScapeTV/ci-workflows/actions/exact-checkout",
        "StreamScapeTV/ci-workflows/actions/prepare-workspace",
        "StreamScapeTV/ci-workflows/actions/cleanup-workspace",
    },
    ".github/workflows/reusable-flutter.yml": {
        "StreamScapeTV/ci-workflows/actions/validate-flutter",
        "StreamScapeTV/ci-workflows/actions/exact-checkout",
        "StreamScapeTV/ci-workflows/actions/prepare-workspace",
        "StreamScapeTV/ci-workflows/actions/cleanup-workspace",
    },
    ".github/workflows/reusable-apple.yml": {
        "StreamScapeTV/ci-workflows/actions/validate-apple",
        RESOLVE_EXECUTION_BACKEND,
        "StreamScapeTV/ci-workflows/actions/github-app-repository-token",
        "StreamScapeTV/ci-workflows/actions/materialize-private-release-asset",
        "StreamScapeTV/ci-workflows/actions/exact-checkout",
        "StreamScapeTV/ci-workflows/actions/prepare-workspace",
        "StreamScapeTV/ci-workflows/actions/checkout-private-dependency",
        "StreamScapeTV/ci-workflows/actions/render-evidence",
        "StreamScapeTV/ci-workflows/actions/cleanup-workspace",
    },
    ".github/workflows/reusable-gitops-validation.yml": {
        "StreamScapeTV/ci-workflows/actions/validate-gitops",
        RESOLVE_EXECUTION_BACKEND,
        "StreamScapeTV/ci-workflows/actions/exact-checkout",
        "StreamScapeTV/ci-workflows/actions/prepare-workspace",
        "StreamScapeTV/ci-workflows/actions/render-evidence",
        "StreamScapeTV/ci-workflows/actions/cleanup-workspace",
    },
}

ANDROID_WORKFLOW = ".github/workflows/reusable-android.yml"
SOURCE_WORKFLOW = ".github/workflows/reusable-resolve-source.yml"
SOURCE_HELPER = "StreamScapeTV/ci-workflows/actions/resolve-source"


class ReusableWorkflowSourceIdentityTests(unittest.TestCase):
    @staticmethod
    def load(relative: str) -> tuple[str, dict[str, object]]:
        source = (ROOT / relative).read_text(encoding="utf-8")
        return source, yaml.load(source, Loader=ActionsLoader)

    def test_supported_private_reusables_follow_main_without_central_clone(self) -> None:
        for relative, expected in PRIVATE_WORKFLOWS.items():
            with self.subTest(workflow=relative):
                source, workflow = self.load(relative)
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
                    if str(step.get("uses", "")).startswith("StreamScapeTV/ci-workflows/actions/")
                }
                self.assertEqual(expected, set(remote_helpers))
                for helper, reference in remote_helpers.items():
                    self.assertEqual("main", reference, helper)

    def test_retired_oci_and_helm_public_reusables_are_absent(self) -> None:
        for relative in (
            ".github/workflows/reusable-oci-build.yml",
            ".github/workflows/reusable-oci-publish.yml",
            ".github/workflows/reusable-helm-validate.yml",
            ".github/workflows/reusable-helm-publish.yml",
        ):
            with self.subTest(workflow=relative):
                self.assertFalse((ROOT / relative).exists())

    def test_android_main_action_contains_media_lifecycle_policy(self) -> None:
        source, _ = self.load(ANDROID_WORKFLOW)
        policy = json.loads((ROOT / "contracts/android-source-policy.json").read_text(encoding="utf-8"))
        exception = next(
            item
            for item in policy["tracked_secret_exceptions"]
            if item["id"] == "streamscape_media_playback_lab_redaction_sentinels_v1"
        )
        self.assertIn(
            {
                "path": "apple/Tests/StreamscapePlaybackLabSupportTests/PlaybackLabLifecycleEvidenceTests.swift",
                "git_blob_sha1": "5df889bbf613ee7f4dabd07ca931aa81fb4f71a3",
            },
            exception["paths"],
        )
        self.assertEqual(10, source.count("actions/validate-android@main"))
        self.assertEqual(2, source.count("actions/resolve-execution-backend@main"))
        self.assertEqual(1, source.count("actions/warm-gradle-dependencies@main"))
        self.assertEqual(2, source.count("actions/upload-gradle-seed@main"))

    def test_apple_main_action_contains_media_contract_in_current_tree(self) -> None:
        source, _ = self.load(".github/workflows/reusable-apple.yml")
        fragment = ROOT / "contracts/apple-validation-media-tvos-simulator-confidence.json"
        self.assertTrue(fragment.is_file())
        self.assertEqual(4, source.count("actions/validate-apple@main"))
        self.assertEqual(1, source.count("actions/resolve-execution-backend@main"))
        self.assertEqual(3, source.count("actions/github-app-repository-token@main"))
        self.assertEqual(4, source.count("actions/materialize-private-release-asset@main"))

    def test_source_reusable_uses_mode_aware_helper_from_main(self) -> None:
        source, workflow = self.load(SOURCE_WORKFLOW)
        helper = next(
            step
            for job in workflow["jobs"].values()
            for step in job.get("steps", [])
            if str(step.get("uses", "")).startswith(f"{SOURCE_HELPER}@")
        )
        self.assertEqual(helper["uses"], f"{SOURCE_HELPER}@main")
        self.assertNotIn("secrets: inherit", source)
        self.assertNotIn("path: .ciw", source)

    def test_private_android_consumer_cannot_control_central_source_or_credential_scope(self) -> None:
        source, workflow = self.load(ANDROID_WORKFLOW)
        public = json.loads((ROOT / "contracts/public-workflows/validation.json").read_text(encoding="utf-8"))
        android = next(item for item in public["workflows"] if item["api_name"] == "validation.android")
        self.assertNotIn("supported_consumers", android)
        self.assertNotIn("supported_products", android)
        self.assertEqual(
            set(workflow["on"]["workflow_call"].get("secrets", {})),
            {"private_dependency_token", "maven_package_read_token"},
        )
        self.assertNotIn("central_source", source)
        self.assertNotIn("secrets: inherit", source)
        self.assertNotIn("workflow_ref", source)
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
        execute = next(
            step
            for job in workflow["jobs"].values()
            for step in job.get("steps", [])
            if step.get("id") == "execute"
        )
        self.assertEqual(
            execute["env"]["CIW_MAVEN_PACKAGE_READ_TOKEN"],
            "${{ secrets.maven_package_read_token }}",
        )
        self.assertNotIn("maven_package_read_token", execute.get("with", {}))
        for job in workflow["jobs"].values():
            for step in job.get("steps", []):
                if step is dependency or step is execute:
                    continue
                self.assertNotIn("private_dependency_token", json.dumps(step))
                self.assertNotIn("maven_package_read_token", json.dumps(step))


if __name__ == "__main__":
    unittest.main()
