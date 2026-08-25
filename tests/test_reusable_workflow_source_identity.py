from __future__ import annotations

import json
import unittest
from pathlib import Path

import yaml

from ci_workflows.validation_model import ActionsLoader

ROOT = Path(__file__).resolve().parents[1]
SOURCE_SHA = "0b55b5f4bc2623815e47759d186e4955b6444075"
FOUNDATION_SHA = "70e08d4ddf8930046632a7135950e924b82e22bf"
BACKEND_SHA = "7d5d839c6e90491e165f1358ecb5e80129805764"
PYTHON_SHA = "3d3689fda11b03a188789f03d6d64cab50f1873a"
EXECUTION_BACKEND_SHA = "83084efecc597d3bedacfe5f8628f1890b9bcd90"
FLUTTER_SHA = "d2e1c7a7601e1caeeb976311fb13cf41fef94d4a"
ANDROID_SHA = "91e5ba5af11ec717f829000edad062c664fb86f7"
GRADLE_WARM_SHA = "13de46c51efcf65df798dfec82a620c484350dfa"
GRADLE_SEED_SHA = "fa67b6a1580ff2eb7386a9e58de09896b9990696"
APPLE_SHA = "33da58aed7f0423d33cea69ebd7eb829b283ec0d"
REPOSITORY_TOKEN_SHA = "56f4859ae09944df6eaaafa7c808e5a1081e61af"
RELEASE_ASSET_SHA = "3fa645e1aa7ab8ae8c681afeb7c73627617b09c8"
GITOPS_SHA = "b99c4296fe7cad06ef6c5956b1b0eb86a49f0145"

FOUNDATION = "issue #116 immutable private-action checkpoint"
ISSUE_350 = "issue #350 PR-merge snapshot race checkpoint"
ISSUE_405 = "issue #405 simplified execution-backend checkpoint"
ISSUE_473_PYTHON = "issue #473 product-neutral Python checkpoint"
ISSUE_495_BACKEND = "issue #495 hosted Apple backend checkpoint"
ISSUE_104 = "issue #104 immutable private-action checkpoint"
ISSUE_534_PREFIX = "issue #534 prefix-isolated protected-full checkpoint"
ISSUE_346_WARM = "issue #346 dependency warm checkpoint"
ISSUE_346_CACHE = "issue #346 bounded Gradle cache sync diagnostics checkpoint"
ISSUE_475_GITOPS = "issue #475 bounded GitOps source validation checkpoint"
ISSUE_495_APPLE = "issue #495 external-source identity checkpoint"
ISSUE_495_REPOSITORY_TOKEN = "issue #495 bounded repository token primitive"
ISSUE_495_RELEASE_ASSET = "issue #495 corrected release-asset checkpoint"
ISSUE_27 = "issue #27 Finance composition publication checkpoint"

PRIVATE_WORKFLOWS: dict[str, dict[str, tuple[str, str]]] = {
    ".github/workflows/reusable-node.yml": {
        "StreamScapeTV/ci-workflows/actions/validate-node": (BACKEND_SHA, ISSUE_405),
        "StreamScapeTV/ci-workflows/actions/exact-checkout": (FOUNDATION_SHA, FOUNDATION),
        "StreamScapeTV/ci-workflows/actions/prepare-workspace": (FOUNDATION_SHA, FOUNDATION),
        "StreamScapeTV/ci-workflows/actions/render-evidence": (FOUNDATION_SHA, FOUNDATION),
        "StreamScapeTV/ci-workflows/actions/cleanup-workspace": (FOUNDATION_SHA, FOUNDATION),
    },
    ".github/workflows/reusable-android.yml": {
        "StreamScapeTV/ci-workflows/actions/validate-android": (ANDROID_SHA, ISSUE_534_PREFIX),
        "StreamScapeTV/ci-workflows/actions/warm-gradle-dependencies": (GRADLE_WARM_SHA, ISSUE_346_WARM),
        "StreamScapeTV/ci-workflows/actions/upload-gradle-seed": (GRADLE_SEED_SHA, ISSUE_346_CACHE),
        "StreamScapeTV/ci-workflows/actions/exact-checkout": (FOUNDATION_SHA, FOUNDATION),
        "StreamScapeTV/ci-workflows/actions/prepare-workspace": (FOUNDATION_SHA, FOUNDATION),
        "StreamScapeTV/ci-workflows/actions/checkout-private-dependency": (FOUNDATION_SHA, ISSUE_104),
        "StreamScapeTV/ci-workflows/actions/render-evidence": (FOUNDATION_SHA, FOUNDATION),
        "StreamScapeTV/ci-workflows/actions/cleanup-workspace": (FOUNDATION_SHA, FOUNDATION),
    },
    ".github/workflows/reusable-python.yml": {
        "StreamScapeTV/ci-workflows/actions/validate-python": (PYTHON_SHA, ISSUE_473_PYTHON),
        "StreamScapeTV/ci-workflows/actions/exact-checkout": (FOUNDATION_SHA, FOUNDATION),
        "StreamScapeTV/ci-workflows/actions/prepare-workspace": (FOUNDATION_SHA, FOUNDATION),
        "StreamScapeTV/ci-workflows/actions/cleanup-workspace": (FOUNDATION_SHA, FOUNDATION),
    },
    ".github/workflows/reusable-flutter.yml": {
        "StreamScapeTV/ci-workflows/actions/validate-flutter": (FLUTTER_SHA, ISSUE_27),
        "StreamScapeTV/ci-workflows/actions/exact-checkout": (FOUNDATION_SHA, FOUNDATION),
        "StreamScapeTV/ci-workflows/actions/prepare-workspace": (FOUNDATION_SHA, FOUNDATION),
        "StreamScapeTV/ci-workflows/actions/cleanup-workspace": (FOUNDATION_SHA, FOUNDATION),
    },
    ".github/workflows/reusable-apple.yml": {
        "StreamScapeTV/ci-workflows/actions/validate-apple": (APPLE_SHA, ISSUE_495_APPLE),
        "StreamScapeTV/ci-workflows/actions/resolve-execution-backend": (EXECUTION_BACKEND_SHA, ISSUE_495_BACKEND),
        "StreamScapeTV/ci-workflows/actions/github-app-repository-token": (REPOSITORY_TOKEN_SHA, ISSUE_495_REPOSITORY_TOKEN),
        "StreamScapeTV/ci-workflows/actions/materialize-private-release-asset": (RELEASE_ASSET_SHA, ISSUE_495_RELEASE_ASSET),
        "StreamScapeTV/ci-workflows/actions/exact-checkout": (FOUNDATION_SHA, FOUNDATION),
        "StreamScapeTV/ci-workflows/actions/prepare-workspace": (FOUNDATION_SHA, FOUNDATION),
        "StreamScapeTV/ci-workflows/actions/checkout-private-dependency": (FOUNDATION_SHA, ISSUE_104),
        "StreamScapeTV/ci-workflows/actions/render-evidence": (FOUNDATION_SHA, FOUNDATION),
        "StreamScapeTV/ci-workflows/actions/cleanup-workspace": (FOUNDATION_SHA, FOUNDATION),
    },
    ".github/workflows/reusable-gitops-validation.yml": {
        "StreamScapeTV/ci-workflows/actions/validate-gitops": (GITOPS_SHA, ISSUE_475_GITOPS),
        "StreamScapeTV/ci-workflows/actions/resolve-execution-backend": (EXECUTION_BACKEND_SHA, ISSUE_495_BACKEND),
        "StreamScapeTV/ci-workflows/actions/exact-checkout": (FOUNDATION_SHA, FOUNDATION),
        "StreamScapeTV/ci-workflows/actions/prepare-workspace": (FOUNDATION_SHA, FOUNDATION),
        "StreamScapeTV/ci-workflows/actions/render-evidence": (FOUNDATION_SHA, FOUNDATION),
        "StreamScapeTV/ci-workflows/actions/cleanup-workspace": (FOUNDATION_SHA, FOUNDATION),
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

    @staticmethod
    def locked_actions() -> dict[str, dict[str, str]]:
        action_lock = json.loads((ROOT / "contracts/action-tool-lock.json").read_text(encoding="utf-8"))
        return {item["uses"]: item for item in action_lock["third_party_actions"]}

    def test_supported_private_reusables_use_only_locked_immutable_central_actions(self) -> None:
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
                    if str(step.get("uses", "")).startswith("StreamScapeTV/ci-workflows/actions/")
                }
                self.assertEqual(set(expected), set(remote_helpers))
                for helper, (sha, release) in expected.items():
                    self.assertEqual(sha, remote_helpers[helper])
                    self.assertIn(helper, locked)
                    self.assertEqual(sha, locked[helper]["sha"])
                    self.assertEqual("composite", locked[helper]["runtime"])
                    self.assertEqual(release, locked[helper]["release"])

    def test_retired_oci_and_helm_public_reusables_are_absent(self) -> None:
        for relative in (
            ".github/workflows/reusable-oci-build.yml",
            ".github/workflows/reusable-oci-publish.yml",
            ".github/workflows/reusable-helm-validate.yml",
            ".github/workflows/reusable-helm-publish.yml",
        ):
            with self.subTest(workflow=relative):
                self.assertFalse((ROOT / relative).exists())

    def test_android_private_action_checkpoint_contains_media_lifecycle_policy(self) -> None:
        source, _ = self.load(ANDROID_WORKFLOW)
        policy = json.loads((ROOT / "contracts/android-source-policy.json").read_text(encoding="utf-8"))
        exception = next(item for item in policy["tracked_secret_exceptions"] if item["id"] == "streamscape_media_playback_lab_redaction_sentinels_v1")
        self.assertIn({"path": "apple/Tests/StreamscapePlaybackLabSupportTests/PlaybackLabLifecycleEvidenceTests.swift", "git_blob_sha1": "5df889bbf613ee7f4dabd07ca931aa81fb4f71a3"}, exception["paths"])
        self.assertEqual(8, source.count(f"actions/validate-android@{ANDROID_SHA}"))
        self.assertEqual(1, source.count(f"actions/warm-gradle-dependencies@{GRADLE_WARM_SHA}"))
        self.assertNotIn("actions/validate-android@275ee86f0f5de3d8f3330b92c84d7c0188fb10f8", source)

    def test_apple_private_action_checkpoint_contains_media_contract_in_current_tree(self) -> None:
        source, _ = self.load(".github/workflows/reusable-apple.yml")
        fragment = ROOT / "contracts/apple-validation-media-tvos-simulator-confidence.json"
        self.assertTrue(fragment.is_file())
        self.assertEqual(4, source.count(f"actions/validate-apple@{APPLE_SHA}"))
        self.assertEqual(1, source.count(f"actions/resolve-execution-backend@{EXECUTION_BACKEND_SHA}"))
        self.assertEqual(3, source.count(f"actions/github-app-repository-token@{REPOSITORY_TOKEN_SHA}"))
        self.assertEqual(4, source.count(f"actions/materialize-private-release-asset@{RELEASE_ASSET_SHA}"))
        self.assertNotIn("actions/validate-apple@293dee450e3464032d67f702b768f493abf65d7b", source)

    def test_source_reusable_uses_locked_mode_aware_helper_checkpoint(self) -> None:
        source, workflow = self.load(SOURCE_WORKFLOW)
        locked = self.locked_actions()
        helper = next(step for job in workflow["jobs"].values() for step in job.get("steps", []) if str(step.get("uses", "")).startswith(f"{SOURCE_HELPER}@"))
        self.assertEqual(helper["uses"], f"{SOURCE_HELPER}@{SOURCE_SHA}")
        self.assertNotIn("actions/checkout@", source)
        self.assertNotIn("secrets: inherit", source)
        self.assertEqual(locked[SOURCE_HELPER]["sha"], SOURCE_SHA)
        self.assertEqual(locked[SOURCE_HELPER]["release"], ISSUE_350)
        self.assertEqual(locked[SOURCE_HELPER]["runtime"], "composite")
        self.assertEqual(locked[SOURCE_HELPER]["source"], f"https://github.com/StreamScapeTV/ci-workflows/tree/{SOURCE_SHA}/actions/resolve-source")

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
        self.assertNotIn("github.workflow", source)
        dependency = next(step for job in workflow["jobs"].values() for step in job.get("steps", []) if step.get("id") == "dependency")
        self.assertEqual(dependency["with"]["token"], "${{ secrets.private_dependency_token }}")
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
