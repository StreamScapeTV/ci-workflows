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
ANDROID_SHA = "91e5ba5af11ec717f829000edad062c664fb86f7"
BACKEND_SHA = "83084efecc597d3bedacfe5f8628f1890b9bcd90"
WARM_SHA = "13de46c51efcf65df798dfec82a620c484350dfa"
FOUNDATION_SHA = "70e08d4ddf8930046632a7135950e924b82e22bf"
GRADLE_SYNC_SHA = "fa67b6a1580ff2eb7386a9e58de09896b9990696"
OWNER_GATE = "github.event.pull_request.user.login == 'mimranfaruqi'"
REPOSITORY_GATE = "github.event.pull_request.head.repo.full_name == github.repository"

PUBLIC_INPUTS = {
    "execution_backend",
    "admitted_sha",
    "validation_scope",
    "working_directory",
    "gradle_wrapper_path",
    "validation_plan_json",
    "dependency_prebuild_plan_json",
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

    def test_public_surface_is_v2_1_technology_data_only(self) -> None:
        call = self.workflow["on"]["workflow_call"]
        self.assertEqual(set(call["inputs"]), PUBLIC_INPUTS)
        self.assertEqual(
            set(call["secrets"]),
            {"private_dependency_token", "maven_package_read_token"},
        )
        self.assertEqual(set(call["outputs"]), {"result", "test_summary", "cleanup_result"})
        self.assertFalse(call["inputs"]["execution_backend"]["required"])
        self.assertEqual(call["inputs"]["execution_backend"]["default"], "organization")
        self.assertEqual(call["inputs"]["execution_backend"]["type"], "string")
        self.assertTrue(call["inputs"]["validation_plan_json"]["required"])
        self.assertFalse(call["inputs"]["dependency_prebuild_plan_json"]["required"])
        self.assertEqual(call["inputs"]["dependency_prebuild_plan_json"]["default"], "")
        for forbidden in (
            "promote_gradle_seed",
            "warm_gradle_dependencies",
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
        self.assertEqual(set(self.workflow["jobs"]), {"plan", "plan_organization", "validate"})
        hosted = self.workflow["jobs"]["plan"]
        organization = self.workflow["jobs"]["plan_organization"]
        self.assertEqual(hosted["runs-on"], ["ubuntu-latest"])
        self.assertEqual(hosted["if"], "${{ inputs.execution_backend == 'github-hosted' }}")
        self.assertEqual(organization["runs-on"], ["linux", "amd64", "mobile"])
        self.assertEqual(organization["if"], "${{ inputs.execution_backend != 'github-hosted' }}")
        for planner in (hosted, organization):
            self.assertEqual(planner["outputs"]["runs_on_json"], "${{ steps.backend.outputs.runs_on_json }}")
            by_id = {step["id"]: step for step in planner["steps"]}
            self.assertEqual(
                by_id["plan"]["uses"],
                f"StreamScapeTV/ci-workflows/actions/validate-android@{ANDROID_SHA}",
            )
            self.assertEqual(
                by_id["backend"]["uses"],
                f"StreamScapeTV/ci-workflows/actions/resolve-execution-backend@{BACKEND_SHA}",
            )
            self.assertEqual(by_id["backend"]["with"]["workflow_api"], "validation.android")
            self.assertEqual(by_id["backend"]["with"]["runner_profile"], "mobile")
            self.assertEqual(by_id["backend"]["with"]["execution_backend"], "${{ inputs.execution_backend }}")
        job = self.workflow["jobs"]["validate"]
        self.assertEqual(job["name"], "CI / Android validation")
        self.assertEqual(job["needs"], ["plan", "plan_organization"])
        self.assertEqual(
            job["if"],
            "${{ always() && (needs.plan.result == 'success' || needs.plan_organization.result == 'success') }}",
        )
        self.assertEqual(
            job["runs-on"],
            "${{ fromJSON(needs.plan.outputs.runs_on_json || needs.plan_organization.outputs.runs_on_json) }}",
        )
        self.assertEqual(job["timeout-minutes"], 120)
        self.assertNotIn("strategy", job)
        steps = job["steps"]
        self.assertEqual(
            [step["id"] for step in steps if "id" in step],
            [
                "plan",
                "prebuild_plan",
                "checkout",
                "workspace",
                "dependency",
                "dependency_warm",
                "dependency_warm_seed",
                "prebuild_execute",
                "prebuild_cleanup",
                "prebuild_residue",
                "execute",
                "evidence",
                "android_cleanup",
                "residue",
                "gradle_seed",
                "workspace_cleanup",
                "clean",
                "terminal",
            ],
        )
        hosted_java = [step for step in steps if step.get("name") == "Set up exact hosted JDK 25"]
        self.assertEqual(len(hosted_java), 1)
        self.assertEqual(hosted_java[0]["if"], "${{ inputs.execution_backend == 'github-hosted' }}")
        self.assertRegex(hosted_java[0]["uses"], r"^actions/setup-java@[0-9a-f]{40}$")
        for identifier in (
            "checkout",
            "dependency",
            "workspace",
            "dependency_warm",
            "dependency_warm_seed",
            "prebuild_plan",
            "prebuild_execute",
            "prebuild_cleanup",
            "prebuild_residue",
            "execute",
            "android_cleanup",
            "residue",
            "gradle_seed",
            "workspace_cleanup",
        ):
            self.assertEqual(sum(step.get("id") == identifier for step in steps), 1)
        self.assertNotIn("self-hosted", self.source)
        self.assertNotIn("matrix:", self.source)

    def test_every_central_helper_is_immutable_and_cache_sync_pins_are_exact(self) -> None:
        uses = [
            str(step.get("uses", ""))
            for step in self.workflow["jobs"]["validate"]["steps"]
            if str(step.get("uses", "")).startswith("StreamScapeTV/ci-workflows/actions/")
        ]
        self.assertEqual(
            8,
            uses.count(f"StreamScapeTV/ci-workflows/actions/validate-android@{ANDROID_SHA}"),
        )
        for planner_name in ("plan", "plan_organization"):
            planner = self.workflow["jobs"][planner_name]
            planner_uses = [step["uses"] for step in planner["steps"] if "uses" in step]
            self.assertEqual(
                planner_uses,
                [
                    f"StreamScapeTV/ci-workflows/actions/validate-android@{ANDROID_SHA}",
                    f"StreamScapeTV/ci-workflows/actions/resolve-execution-backend@{BACKEND_SHA}",
                ],
            )
        self.assertEqual(
            1,
            uses.count(
                f"StreamScapeTV/ci-workflows/actions/warm-gradle-dependencies@{WARM_SHA}"
            ),
        )
        self.assertEqual(
            2,
            uses.count(f"StreamScapeTV/ci-workflows/actions/upload-gradle-seed@{GRADLE_SYNC_SHA}"),
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

    def test_dependency_warm_is_cache_maintenance_only(self) -> None:
        steps = self.workflow["jobs"]["validate"]["steps"]
        by_id = {step["id"]: step for step in steps if "id" in step}
        warm = by_id["dependency_warm"]
        warm_seed = by_id["dependency_warm_seed"]
        prebuild_plan = by_id["prebuild_plan"]
        prebuild = by_id["prebuild_execute"]
        execute = by_id["execute"]
        evidence = by_id["evidence"]

        self.assertIn("inputs.validation_scope == 'gradle'", warm["if"])
        self.assertIn("private_dependency_used == 'true'", warm["if"])
        self.assertIn("inputs.dependency_prebuild_plan_json == ''", warm["if"])
        self.assertIn("steps.dependency.outcome == 'success'", warm["if"])
        self.assertNotIn("protected-full", warm["if"])
        self.assertEqual(warm["with"]["admitted_sha"], "${{ inputs.admitted_sha }}")
        self.assertEqual(
            warm["with"]["private_dependency_subdirectory"],
            "${{ steps.plan.outputs.private_dependency_subdirectory }}",
        )
        self.assertEqual(
            warm["uses"],
            f"StreamScapeTV/ci-workflows/actions/warm-gradle-dependencies@{WARM_SHA}",
        )
        self.assertTrue(warm_seed["continue-on-error"])
        self.assertEqual(warm_seed["if"], "${{ steps.dependency_warm.outcome == 'success' }}")
        self.assertEqual(warm_seed["with"]["source_sha"], "${{ inputs.admitted_sha }}")
        self.assertLess(steps.index(warm), steps.index(warm_seed))

        for step in (prebuild_plan, prebuild):
            self.assertIn("inputs.validation_scope != 'gradle'", step["if"])
            self.assertNotIn("dependency_warm", step["if"])
        self.assertIn(
            "inputs.validation_scope != 'gradle' || steps.plan.outputs.private_dependency_used != 'true'",
            execute["if"],
        )
        self.assertNotIn("dependency_warm", execute["if"])
        self.assertEqual(evidence["if"], "${{ steps.execute.outcome == 'success' }}")

    def test_normal_product_scopes_have_zero_dependency_warm_prerequisite(self) -> None:
        steps = self.workflow["jobs"]["validate"]["steps"]
        by_id = {step["id"]: step for step in steps if "id" in step}
        warm_if = by_id["dependency_warm"]["if"]
        for scope in ("protected-full", "compile", "unit", "lint", "assemble", "targeted-unit", "script"):
            self.assertNotIn(f"validation_scope == '{scope}'", warm_if)
        self.assertNotIn("dependency_warm", by_id["prebuild_execute"]["if"])
        self.assertNotIn("dependency_warm", by_id["execute"]["if"])
        terminal = by_id["terminal"]
        self.assertEqual(
            terminal["env"]["MAINTENANCE_MODE"],
            "${{ inputs.validation_scope == 'gradle' && steps.plan.outputs.private_dependency_used == 'true' && 'true' || 'false' }}",
        )
        self.assertEqual(
            terminal["env"]["EXECUTE_REQUIRED"],
            "${{ inputs.validation_scope == 'gradle' && steps.plan.outputs.private_dependency_used == 'true' && 'false' || 'true' }}",
        )

    def test_optional_dependency_prebuild_reuses_grouped_android_primitive(self) -> None:
        steps = self.workflow["jobs"]["validate"]["steps"]
        by_id = {step["id"]: step for step in steps if "id" in step}
        prebuild_plan = by_id["prebuild_plan"]
        prebuild_execute = by_id["prebuild_execute"]
        prebuild_cleanup = by_id["prebuild_cleanup"]
        prebuild_residue = by_id["prebuild_residue"]
        execute = by_id["execute"]

        self.assertEqual(prebuild_plan["with"]["validation_scope"], "protected-full")
        self.assertEqual(
            prebuild_plan["with"]["validation_plan_json"],
            "${{ inputs.dependency_prebuild_plan_json }}",
        )
        self.assertIn("private_dependency_used == 'true'", prebuild_plan["if"])
        self.assertIn("inputs.validation_scope != 'gradle'", prebuild_plan["if"])

        self.assertEqual(prebuild_execute["with"]["validation_scope"], "protected-full")
        self.assertEqual(
            prebuild_execute["with"]["validation_plan_json"],
            "${{ inputs.dependency_prebuild_plan_json }}",
        )
        self.assertIn("steps.prebuild_plan.outcome == 'success'", prebuild_execute["if"])
        self.assertIn("steps.dependency.outcome == 'success'", prebuild_execute["if"])
        self.assertIn("inputs.validation_scope != 'gradle'", prebuild_execute["if"])

        self.assertEqual(prebuild_cleanup["with"]["phase"], "cleanup")
        self.assertEqual(prebuild_residue["with"]["phase"], "residue")
        self.assertIn("always()", prebuild_cleanup["if"])
        self.assertIn("always()", prebuild_residue["if"])
        self.assertLess(steps.index(prebuild_execute), steps.index(prebuild_cleanup))
        self.assertLess(steps.index(prebuild_cleanup), steps.index(prebuild_residue))
        self.assertLess(steps.index(prebuild_residue), steps.index(execute))
        self.assertIn("steps.prebuild_execute.outcome == 'success'", execute["if"])
        self.assertIn("steps.prebuild_cleanup.outcome == 'success'", execute["if"])
        self.assertIn("steps.prebuild_residue.outcome == 'success'", execute["if"])

    def test_internal_cache_sync_has_no_oidc_and_maintenance_promotion_is_terminally_checked(self) -> None:
        self.assertEqual(self.workflow["permissions"], {"contents": "read"})
        job = self.workflow["jobs"]["validate"]
        self.assertNotIn("permissions", job)
        self.assertNotIn("id-token", self.source)
        self.assertNotIn("ACTIONS_ID_TOKEN_REQUEST", self.source)
        self.assertNotIn("authorization", self.source.casefold())
        steps = job["steps"]
        warm_index = next(index for index, step in enumerate(steps) if step.get("id") == "dependency_warm")
        warm_sync_index = next(index for index, step in enumerate(steps) if step.get("id") == "dependency_warm_seed")
        final_sync_index = next(index for index, step in enumerate(steps) if step.get("id") == "gradle_seed")
        residue_index = next(index for index, step in enumerate(steps) if step.get("id") == "residue")
        cleanup_index = next(index for index, step in enumerate(steps) if step.get("id") == "workspace_cleanup")
        self.assertLess(warm_index, warm_sync_index)
        self.assertLess(warm_sync_index, final_sync_index)
        self.assertLess(residue_index, final_sync_index)
        self.assertLess(final_sync_index, cleanup_index)
        for identifier in ("dependency_warm_seed", "gradle_seed"):
            sync = next(step for step in steps if step.get("id") == identifier)
            self.assertTrue(sync["continue-on-error"])
            self.assertEqual(sync["with"]["source_sha"], "${{ inputs.admitted_sha }}")
        cleanup = steps[cleanup_index]
        self.assertEqual(cleanup["if"], "always()")
        terminal = next(step for step in steps if step.get("id") == "terminal")
        self.assertEqual(
            terminal["env"]["WARM_SEED_OUTCOME"],
            "${{ steps.dependency_warm_seed.outcome }}",
        )
        self.assertIn('test "${WARM_SEED_OUTCOME}" = "success"', terminal["run"])
        self.assertNotIn("gradle_seed", json.dumps(terminal).casefold())

    def test_terminal_logs_bounded_maintenance_and_android_execution_summaries(self) -> None:
        terminal = next(
            step
            for step in self.workflow["jobs"]["validate"]["steps"]
            if step.get("id") == "terminal"
        )
        self.assertEqual(
            terminal["env"]["ANDROID_TEST_SUMMARY"],
            "${{ steps.execute.outputs.test_summary }}",
        )
        self.assertEqual(
            terminal["env"]["WARM_CACHE_MODE"],
            "${{ steps.dependency_warm.outputs.gradle_dependency_cache_mode }}",
        )
        self.assertEqual(
            terminal["env"]["WARM_WALL_MS"],
            "${{ steps.dependency_warm.outputs.warm_wall_ms }}",
        )
        self.assertIn("MAINTENANCE_MODE", terminal["env"])
        self.assertIn("WARM_SEED_OUTCOME", terminal["env"])
        self.assertIn("EXECUTE_REQUIRED", terminal["env"])
        self.assertIn("warm_ok=true", terminal["run"])
        self.assertIn("gradle-dependency-warm cache_mode=%s wall_ms=%s", terminal["run"])
        self.assertIn("PREBUILD_REQUIRED", terminal["env"])
        self.assertIn("prebuild_ok=true", terminal["run"])
        self.assertIn("execute_ok=true", terminal["run"])
        self.assertIn("android-test-summary=%s", terminal["run"])
        self.assertNotIn("github.event", terminal["run"])
        self.assertNotIn("env |", terminal["run"])

    def test_private_token_is_confined_to_the_single_dependency_checkout(self) -> None:
        steps = self.workflow["jobs"]["validate"]["steps"]
        dependency = next(step for step in steps if step.get("id") == "dependency")
        self.assertEqual(dependency["with"]["token"], "${{ secrets.private_dependency_token }}")
        for step in steps:
            if step is dependency:
                continue
            self.assertNotIn("private_dependency_token", json.dumps(step))

    def test_package_read_token_is_confined_to_the_primary_product_execution(self) -> None:
        steps = self.workflow["jobs"]["validate"]["steps"]
        execute = next(step for step in steps if step.get("id") == "execute")
        self.assertEqual(
            execute["env"]["CIW_MAVEN_PACKAGE_READ_TOKEN"],
            "${{ secrets.maven_package_read_token }}",
        )
        self.assertNotIn("maven_package_read_token", execute.get("with", {}))
        for step in steps:
            if step is execute:
                continue
            serialized = json.dumps(step)
            self.assertNotIn("maven_package_read_token", serialized)
            self.assertNotIn("CIW_MAVEN_PACKAGE_READ_TOKEN", serialized)

    def test_workspace_uses_no_github_cache_or_artifact_transport_and_is_terminally_cleaned(self) -> None:
        self.assertNotIn("actions/cache", self.source)
        self.assertNotIn("upload-artifact", self.source)
        self.assertNotIn("download-artifact", self.source)
        self.assertIn("cache_mode: disabled", self.source)
        self.assertIn("Check out exact admitted caller source once", self.source)
        self.assertIn("Prepare one isolated marker-bound Gradle state", self.source)
        self.assertIn("Check out exact private dependency at most once", self.source)
        self.assertIn("Resolve Gradle dependency graph for cache maintenance", self.source)
        self.assertIn("Publish cache-maintenance Gradle dependency delta", self.source)
        self.assertIn("Process-isolate optional private dependency Gradle prebuild", self.source)
        self.assertIn("Remove optional dependency-prebuild copied source state", self.source)
        self.assertIn("Verify zero optional dependency-prebuild residue", self.source)
        self.assertIn("Execute one primitive-backed Android validation plan", self.source)
        self.assertIn("Sync newly resolved Gradle dependencies to the internal cache", self.source)
        self.assertIn("Verify exact admitted source remained clean", self.source)
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
            "write-verification-metadata",
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
        self.assertNotIn("execution_backend", self.action["inputs"])
        for forbidden in (
            "gradle_tasks_json",
            "targeted_test_selector",
            "script_path",
            "script_arguments_json",
            "arbitrary_command",
            "container_engine",
            "runner_labels",
            "maven_package_read_token",
        ):
            self.assertNotIn(forbidden, self.action["inputs"])

    def test_smoke_directly_exercises_one_protected_full_hosted_executor(self) -> None:
        self.assertEqual(set(self.smoke["jobs"]), {"contracts", "android", "terminal"})
        self.assertEqual(self.smoke["jobs"]["contracts"]["runs-on"], ["ubuntu-latest"])
        self.assertEqual(self.smoke["jobs"]["terminal"]["runs-on"], ["ubuntu-latest"])
        job = self.smoke["jobs"]["android"]
        self.assertEqual(job["name"], "Android reusable-workflow smoke")
        self.assertEqual(job["needs"], "contracts")
        self.assertIn(OWNER_GATE, job["if"])
        self.assertIn(REPOSITORY_GATE, job["if"])
        self.assertIn("needs.contracts.result == 'success'", job["if"])
        self.assertNotIn("github.event.repository.private", job["if"])
        self.assertEqual(job["runs-on"], ["ubuntu-latest"])
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
