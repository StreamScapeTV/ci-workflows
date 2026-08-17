"""Contract and smoke coverage for the primitive-backed Android reusable workflow."""
from __future__ import annotations

import json
import unittest
from pathlib import Path

import yaml

from ci_workflows.validation_model import ActionsLoader

ROOT = Path(__file__).resolve().parents[1]
REUSABLE = ROOT / ".github/workflows/reusable-android.yml"
SMOKE = ROOT / ".github/workflows/android-validation-smoke.yml"
ACTION = ROOT / "actions/validate-android/action.yml"
ANDROID_SHA = "0b1be616b4a03891b6b31918001320f09726ed93"
FOUNDATION_SHA = "70e08d4ddf8930046632a7135950e924b82e22bf"

PUBLIC_INPUTS = {
    "admitted_sha",
    "validation_scope",
    "working_directory",
    "gradle_wrapper_path",
    "gradle_tasks_json",
    "targeted_test_selector",
    "script_path",
    "script_arguments_json",
    "private_dependency_repository",
    "private_dependency_sha",
    "private_dependency_subdirectory",
    "private_dependency_id",
}


class AndroidWorkflowContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = REUSABLE.read_text(encoding="utf-8")
        cls.workflow = yaml.load(cls.source, Loader=ActionsLoader)
        cls.smoke_source = SMOKE.read_text(encoding="utf-8")
        cls.smoke = yaml.load(cls.smoke_source, Loader=ActionsLoader)
        cls.action_source = ACTION.read_text(encoding="utf-8")
        cls.action = yaml.safe_load(cls.action_source)

    def test_public_surface_is_v2_technology_data_only(self) -> None:
        call = self.workflow["on"]["workflow_call"]
        self.assertEqual(set(call["inputs"]), PUBLIC_INPUTS)
        self.assertEqual(set(call["secrets"]), {"private_dependency_token"})
        self.assertEqual(set(call["outputs"]), {"result", "test_summary", "cleanup_result"})
        for forbidden in (
            "validation_profile",
            "task_profile",
            "consumer_script_profile",
            "private_dependency_contract_id",
            "artifact_exception_id",
            "device_family",
            "device_request_id",
            "runner",
            "runs_on",
            "product_id",
            "shell",
        ):
            self.assertNotIn(forbidden, call["inputs"])

    def test_execution_uses_semantic_mobile_and_fixed_step_order(self) -> None:
        job = self.workflow["jobs"]["validate"]
        self.assertEqual(job["name"], "CI / Android validation")
        self.assertEqual(job["runs-on"], ["linux", "amd64", "mobile"])
        self.assertEqual(job["timeout-minutes"], 120)
        self.assertEqual(
            [step["id"] for step in job["steps"]],
            [
                "plan",
                "checkout",
                "workspace",
                "dependency",
                "execute",
                "evidence",
                "android_cleanup",
                "residue",
                "workspace_cleanup",
                "clean",
                "terminal",
            ],
        )
        self.assertNotIn("self-hosted", self.source)
        self.assertNotIn("fromJSON(needs.", self.source)

    def test_every_central_helper_is_immutable_and_android_pin_is_exact(self) -> None:
        uses = [
            str(step.get("uses", ""))
            for step in self.workflow["jobs"]["validate"]["steps"]
            if str(step.get("uses", "")).startswith("StreamScapeTV/ci-workflows/actions/")
        ]
        self.assertEqual(
            4,
            uses.count(
                f"StreamScapeTV/ci-workflows/actions/validate-android@{ANDROID_SHA}"
            ),
        )
        for helper in ("exact-checkout", "prepare-workspace", "checkout-private-dependency", "render-evidence", "cleanup-workspace"):
            self.assertIn(
                f"StreamScapeTV/ci-workflows/actions/{helper}@{FOUNDATION_SHA}",
                uses,
            )
        for item in uses:
            revision = item.rsplit("@", 1)[1]
            self.assertRegex(revision, r"^[0-9a-f]{40}$")

    def test_private_token_is_confined_to_dependency_checkout(self) -> None:
        steps = self.workflow["jobs"]["validate"]["steps"]
        dependency = next(step for step in steps if step["id"] == "dependency")
        self.assertEqual(
            dependency["with"]["token"],
            "${{ secrets.private_dependency_token }}",
        )
        for step in steps:
            if step is dependency:
                continue
            self.assertNotIn("private_dependency_token", json.dumps(step))

    def test_workspace_is_cache_free_exact_source_and_always_cleaned(self) -> None:
        self.assertNotIn("actions/cache", self.source)
        self.assertNotIn("upload-artifact", self.source)
        self.assertNotIn("download-artifact", self.source)
        self.assertIn("cache_mode: disabled", self.source)
        self.assertIn("Check out exact admitted caller source", self.source)
        self.assertIn("Verify exact admitted source remained clean", self.source)
        self.assertIn("if: always()", self.source)
        self.assertIn("Remove Android-specific copied source state", self.source)
        self.assertIn("Verify zero Android-specific residue", self.source)
        self.assertIn("Remove and verify all registered Android state", self.source)
        self.assertIn("Project terminal Android validation status", self.source)

    def test_workflow_yaml_contains_no_product_commands_or_identity(self) -> None:
        lowered = self.source.casefold()
        for forbidden in (
            "iptv-android",
            "streamscape-media",
            "sdkmanager ",
            "adb ",
            "gradlew ",
            "jib",
            "keystore",
            "play store",
            "helm ",
            "docker ",
        ):
            self.assertNotIn(forbidden, lowered)
        self.assertNotIn("actions/checkout@", self.source)
        self.assertNotIn("secrets: inherit", self.source)

    def test_composite_action_dispatches_only_bounded_ciw_phases(self) -> None:
        self.assertEqual(self.action["runs"]["using"], "composite")
        self.assertIn("ciw.py", self.action_source)
        self.assertIn("android validate", self.action_source)
        self.assertIn("--source-root source", self.action_source)
        for forbidden in ("arbitrary_command", "container_engine", "runner_labels"):
            self.assertNotIn(forbidden, self.action["inputs"])

    def test_smoke_calls_the_reusable_workflow_and_real_gradle_fixture(self) -> None:
        self.assertEqual(set(self.smoke["jobs"]), {"reusable_android"})
        job = self.smoke["jobs"]["reusable_android"]
        self.assertEqual(job["uses"], "./.github/workflows/reusable-android.yml")
        self.assertEqual(job["with"]["validation_scope"], "gradle")
        self.assertEqual(
            job["with"]["working_directory"],
            "tests/fixtures/android-validation/smoke-project",
        )
        self.assertEqual(job["with"]["gradle_tasks_json"], '["verifyToolchainSmoke"]')
        self.assertNotIn("uses: ./.ciw/actions/", self.smoke_source)
        self.assertNotIn("adb ", self.smoke_source.casefold())
        self.assertNotIn("physical-device", self.smoke_source.casefold())


if __name__ == "__main__":
    unittest.main()
