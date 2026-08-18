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
ANDROID_SHA = "a01e29210603dc8b4cb9e31b9b0c926c2ab5cf37"
GRADLE_SEED_SHA = "7a0977db839468aac24448831a9a0ffd97b3067b"
FOUNDATION_SHA = "70e08d4ddf8930046632a7135950e924b82e22bf"

PUBLIC_INPUTS = {
    "admitted_sha",
    "validation_scope",
    "working_directory",
    "gradle_wrapper_path",
    "validation_plan_json",
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
        self.assertTrue(call["inputs"]["validation_plan_json"]["required"])
        self.assertNotIn("promote_gradle_seed", call["inputs"])
        for forbidden in (
            "validation_profile",
            "task_profile",
            "consumer_script_profile",
            "private_dependency_contract_id",
            "artifact_exception_id",
            "device_family",
            "device_request_id",
            "gradle_tasks_json",
            "targeted_test_selector",
            "script_path",
            "script_arguments_json",
            "runner",
            "runs_on",
            "product_id",
            "shell",
            "cache_path",
            "cache_host",
            "cache_endpoint",
        ):
            self.assertNotIn(forbidden, call["inputs"])

    def test_protected_full_is_one_heavy_mobile_executor_and_one_workspace_boundary(self) -> None:
        self.assertEqual(set(self.workflow["jobs"]), {"validate"})
        job = self.workflow["jobs"]["validate"]
        self.assertEqual(job["name"], "CI / Android validation")
        self.assertEqual(job["runs-on"], ["linux", "amd64", "mobile"])
        self.assertEqual(job["timeout-minutes"], 120)
        self.assertNotIn("strategy", job)
        steps = job["steps"]
        self.assertEqual(
            [step["id"] for step in steps],
            [
                "plan",
                "checkout",
                "workspace",
                "dependency",
                "execute",
                "evidence",
                "android_cleanup",
                "residue",
                "gradle_seed_authority",
                "gradle_seed",
                "workspace_cleanup",
                "clean",
                "terminal",
            ],
        )
        self.assertEqual(sum(step["id"] == "checkout" for step in steps), 1)
        self.assertEqual(sum(step["id"] == "dependency" for step in steps), 1)
        self.assertEqual(sum(step["id"] == "workspace" for step in steps), 1)
        self.assertEqual(sum(step["id"] == "execute" for step in steps), 1)
        self.assertEqual(sum(step["id"] == "android_cleanup" for step in steps), 1)
        self.assertEqual(sum(step["id"] == "residue" for step in steps), 1)
        self.assertEqual(sum(step["id"] == "gradle_seed_authority" for step in steps), 1)
        self.assertEqual(sum(step["id"] == "gradle_seed" for step in steps), 1)
        self.assertNotIn("self-hosted", self.source)
        self.assertNotIn("fromJSON(needs.", self.source)
        self.assertNotIn("matrix:", self.source)

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
        self.assertEqual(
            1,
            uses.count(
                f"StreamScapeTV/ci-workflows/actions/upload-gradle-seed@{GRADLE_SEED_SHA}"
            ),
        )
        for helper in (
            "exact-checkout",
            "prepare-workspace",
            "checkout-private-dependency",
            "render-evidence",
            "cleanup-workspace",
        ):
            self.assertIn(
                f"StreamScapeTV/ci-workflows/actions/{helper}@{FOUNDATION_SHA}",
                uses,
            )
        for item in uses:
            revision = item.rsplit("@", 1)[1]
            self.assertRegex(revision, r"^[0-9a-f]{40}$")

    def test_seed_warming_requires_protected_push_and_caller_oidc_and_is_non_authoritative(self) -> None:
        self.assertEqual(self.workflow["permissions"], {"contents": "read"})
        job = self.workflow["jobs"]["validate"]
        self.assertEqual(
            job["permissions"],
            {"contents": "read", "id-token": "write"},
        )
        steps = job["steps"]
        authority = next(step for step in steps if step["id"] == "gradle_seed_authority")
        self.assertIn("github.event_name == 'push'", authority["if"])
        self.assertIn("github.ref_protected", authority["if"])
        self.assertIn("ACTIONS_ID_TOKEN_REQUEST_URL", authority["run"])
        self.assertIn("ACTIONS_ID_TOKEN_REQUEST_TOKEN", authority["run"])
        self.assertIn('echo "enabled=${enabled}"', authority["run"])

        promotion = next(step for step in steps if step["id"] == "gradle_seed")
        self.assertTrue(promotion["continue-on-error"])
        self.assertEqual(
            promotion["uses"],
            f"StreamScapeTV/ci-workflows/actions/upload-gradle-seed@{GRADLE_SEED_SHA}",
        )
        self.assertEqual(promotion["with"], {"source_sha": "${{ inputs.admitted_sha }}"})
        condition = promotion["if"]
        self.assertIn("steps.gradle_seed_authority.outputs.enabled == 'true'", condition)
        self.assertIn("steps.execute.outcome == 'success'", condition)
        self.assertIn("steps.android_cleanup.outcome == 'success'", condition)
        self.assertIn("steps.residue.outcome == 'success'", condition)
        self.assertNotIn("inputs.promote_gradle_seed", condition)

        fallback = next(step for step in steps if step["id"] == "workspace_cleanup")
        self.assertEqual(
            fallback["if"],
            "always() && steps.gradle_seed.outputs.cleanup_verified != 'true'",
        )
        terminal = next(step for step in steps if step["id"] == "terminal")
        terminal_script = terminal["run"]
        self.assertIn("GRADLE_SEED_CLEANUP_VERIFIED", terminal["env"])
        self.assertIn("workspace_cleanup_ok", terminal_script)
        self.assertNotIn("GRADLE_SEED_OUTCOME", terminal["env"])
        self.assertNotIn("GRADLE_SEED_OUTCOME", terminal_script)

    def test_private_token_is_confined_to_the_single_dependency_checkout(self) -> None:
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

    def test_workspace_uses_no_github_cache_or_artifact_transport_and_is_terminally_cleaned(self) -> None:
        self.assertNotIn("actions/cache", self.source)
        self.assertNotIn("upload-artifact", self.source)
        self.assertNotIn("download-artifact", self.source)
        self.assertIn("cache_mode: disabled", self.source)
        self.assertIn("Check out exact admitted caller source once", self.source)
        self.assertIn("Prepare one isolated marker-bound Gradle state", self.source)
        self.assertIn("Check out exact private dependency at most once", self.source)
        self.assertIn("Execute one primitive-backed Android validation plan", self.source)
        self.assertIn("Verify exact admitted source remained clean", self.source)
        self.assertIn("if: always()", self.source)
        self.assertIn("Remove Android-specific copied source state once", self.source)
        self.assertIn("Verify zero Android-specific residue once", self.source)
        self.assertIn("Remove and verify the one registered Android workspace", self.source)
        self.assertIn("Project terminal Android validation status", self.source)

    def test_workflow_yaml_contains_no_product_commands_or_identity(self) -> None:
        lowered = self.source.casefold()
        for forbidden in (
            "iptv-android",
            "streamscape-media",
            "compiledebugkotlin",
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

    def test_composite_action_dispatches_only_bounded_ciw_phases_and_plan_json(self) -> None:
        self.assertEqual(self.action["runs"]["using"], "composite")
        self.assertIn("ciw.py", self.action_source)
        self.assertIn("android validate", self.action_source)
        self.assertIn("--source-root source", self.action_source)
        self.assertIn("validation_plan_json", self.action["inputs"])
        for forbidden in (
            "gradle_tasks_json",
            "targeted_test_selector",
            "script_path",
            "script_arguments_json",
            "arbitrary_command",
            "container_engine",
            "runner_labels",
        ):
            self.assertNotIn(forbidden, self.action["inputs"])

    def test_smoke_directly_exercises_one_protected_full_mobile_executor(self) -> None:
        self.assertEqual(set(self.smoke["jobs"]), {"android"})
        job = self.smoke["jobs"]["android"]
        self.assertEqual(job["name"], "Android reusable-workflow smoke")
        self.assertEqual(job["runs-on"], ["linux", "amd64", "mobile"])
        self.assertNotIn("strategy", job)
        self.assertNotIn("uses", job)
        self.assertNotIn("./.github/workflows/reusable-android.yml", self.smoke_source)
        steps = job["steps"]
        self.assertEqual(sum(step.get("id") == "execute" for step in steps), 1)
        self.assertEqual(sum(step.get("id") == "workspace" for step in steps), 1)
        self.assertEqual(sum(step.get("id") == "android_cleanup" for step in steps), 1)
        execute = next(step for step in steps if step.get("id") == "execute")
        self.assertEqual(execute["with"]["validation_scope"], "protected-full")
        plan = json.loads(execute["with"]["validation_plan_json"])
        self.assertEqual(plan["unit_tasks"], ["help"])
        self.assertEqual(plan["lint_tasks"], ["tasks"])
        self.assertEqual(plan["assemble_tasks"], ["verifyToolchainSmoke"])
        self.assertEqual(plan["schema"], {"mode": "none"})
        self.assertNotIn("compile", json.dumps(plan).casefold())
        self.assertIn("uses: ./.ciw/actions/validate-android", self.smoke_source)
        self.assertIn("git rev-parse HEAD", self.smoke_source)
        self.assertIn("Verify zero Actions artifacts", self.smoke_source)
        self.assertNotIn("adb ", self.smoke_source.casefold())
        self.assertNotIn("physical-device", self.smoke_source.casefold())


if __name__ == "__main__":
    unittest.main()
