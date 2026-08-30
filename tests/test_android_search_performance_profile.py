from pathlib import Path
import unittest

import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "android.yml"
PERFORMANCE_TEST_CLASS = (
    "com.streamscapetv.app.foundation.performance.testing.PerformanceBudgetTest"
)


class AndroidSearchPerformanceProfileContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.workflow_text = WORKFLOW_PATH.read_text(encoding="utf-8")
        cls.workflow = yaml.safe_load(cls.workflow_text)
        steps = cls.workflow["jobs"]["ci"]["steps"]
        cls.profile_step = next(
            step for step in steps if step.get("name") == "Run fixed Android profile"
        )
        cls.profile_script = cls.profile_step["run"]

    def profile_body(self, profile: str) -> str:
        start = self.profile_script.index(f"{profile})")
        end = self.profile_script.index(";;", start)
        return self.profile_script[start:end]

    def test_search_performance_profile_uses_only_fixed_product_class(self) -> None:
        self.assertIn(
            f"performance_test_class='{PERFORMANCE_TEST_CLASS}'",
            self.profile_script,
        )
        body = self.profile_body("search-performance")
        self.assertIn(
            'run_gradle testDebugUnitTest --tests "${performance_test_class}"',
            body,
        )
        self.assertNotIn("TEST_FILTER", body)
        self.assertNotIn("inputs.", body)

    def test_full_excludes_host_sensitive_performance_class(self) -> None:
        body = self.profile_body("full")
        self.assertIn("android-full-correctness.init.gradle", body)
        self.assertIn("excludeTestsMatching", body)
        self.assertIn("${performance_test_class}", body)
        self.assertIn('--init-script "${correctness_init_script}"', body)
        self.assertIn("compileDebugKotlin", body)
        self.assertIn("testDebugUnitTest", body)
        self.assertIn("lintDebug", body)
        self.assertIn("assembleDebug", body)

    def test_profile_does_not_broaden_develop_dependency_cache(self) -> None:
        steps = self.workflow["jobs"]["ci"]["steps"]
        save_step = next(
            step
            for step in steps
            if step.get("name") == "Save IPTV Android develop dependency cache"
        )
        self.assertNotIn("search-performance", save_step["if"])
        self.assertIn("inputs.test_profile == 'full'", save_step["if"])
        self.assertIn("inputs.test_profile == 'release'", save_step["if"])


class AndroidSharedCacheContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.workflow_text = WORKFLOW_PATH.read_text(encoding="utf-8")
        cls.workflow = yaml.safe_load(cls.workflow_text)
        steps = cls.workflow["jobs"]["ci"]["steps"]
        cls.by_name = {step.get("name"): step for step in steps if step.get("name")}

    def test_feature_and_pr_reader_scope_excludes_main_develop_and_detached_tags(self) -> None:
        scope = self.by_name["Resolve IPTV Android branch cache reader scope"]
        script = scope["run"]
        self.assertIn("StreamScapeTV/iptv-android", script)
        self.assertIn("git symbolic-ref --quiet --short HEAD", script)
        self.assertIn('[[ "${source_ref}" == refs/pull/* ]]', script)
        self.assertIn('test "${checkout_branch}" != develop', script)
        self.assertIn('test "${checkout_branch}" != main', script)
        self.assertIn('test "${normalized_ref}" != develop', script)
        self.assertIn('test "${normalized_ref}" != main', script)
        self.assertNotIn("refs/tags/*", script)

    def test_branch_dependency_restore_is_read_only_and_uses_trusted_develop_family(self) -> None:
        restore = self.by_name["Restore IPTV Android branch dependency cache"]
        self.assertEqual(
            restore["if"],
            "${{ steps.android_branch_cache_scope.outputs.enabled == 'true' }}",
        )
        self.assertEqual(restore["uses"], "actions/cache/restore@v4")
        self.assertEqual(
            restore["with"]["path"],
            "~/.gradle/wrapper\n~/.gradle/caches/modules-2\n",
        )
        self.assertIn("iptv-android-develop-gradle-deps-v1-", restore["with"]["key"])
        self.assertIn(
            "iptv-android-develop-gradle-deps-v1-",
            restore["with"]["restore-keys"],
        )
        self.assertNotIn("actions/cache/save", str(restore))

    def test_gradle_build_cache_has_per_source_develop_generations_and_prefix_restore(self) -> None:
        restore = self.by_name["Restore IPTV Android Gradle build cache"]
        save = self.by_name["Save IPTV Android Gradle build cache"]
        self.assertEqual(restore["uses"], "actions/cache/restore@v4")
        self.assertEqual(restore["with"]["path"], "~/.gradle/caches/build-cache-1")
        self.assertIn("iptv-android-develop-gradle-build-v1-", restore["with"]["key"])
        self.assertIn(
            "steps.android_develop_cache_scope.outputs.source_sha",
            restore["with"]["key"],
        )
        self.assertIn(
            "iptv-android-develop-gradle-build-v1-",
            restore["with"]["restore-keys"],
        )
        self.assertNotIn("source_sha", restore["with"]["restore-keys"])
        self.assertEqual(save["uses"], "actions/cache/save@v4")
        self.assertEqual(save["with"]["path"], "~/.gradle/caches/build-cache-1")
        self.assertEqual(
            save["with"]["key"],
            "${{ steps.gradle_build_cache.outputs.cache-primary-key }}",
        )

    def test_only_develop_full_or_release_can_save_either_cache_layer(self) -> None:
        for name in (
            "Save IPTV Android develop dependency cache",
            "Save IPTV Android Gradle build cache",
        ):
            step = self.by_name[name]
            condition = step["if"]
            self.assertIn(
                "steps.android_develop_cache_scope.outputs.enabled == 'true'",
                condition,
            )
            self.assertIn("steps.commands.outcome == 'success'", condition)
            self.assertIn("inputs.test_profile == 'full'", condition)
            self.assertIn("inputs.test_profile == 'release'", condition)
            self.assertIn("cache-hit != 'true'", condition)

    def test_cache_paths_exclude_raw_build_outputs_source_credentials_and_full_gradle_home(self) -> None:
        cache_steps = (
            self.by_name["Restore IPTV Android develop dependency cache"],
            self.by_name["Restore IPTV Android branch dependency cache"],
            self.by_name["Restore IPTV Android Gradle build cache"],
            self.by_name["Save IPTV Android develop dependency cache"],
            self.by_name["Save IPTV Android Gradle build cache"],
        )
        cached_paths = "\n".join(str(step["with"]["path"]) for step in cache_steps)
        self.assertIn("~/.gradle/wrapper", cached_paths)
        self.assertIn("~/.gradle/caches/modules-2", cached_paths)
        self.assertIn("~/.gradle/caches/build-cache-1", cached_paths)
        for forbidden in (
            "app/build",
            "build/outputs",
            "apk",
            "aab",
            "signing",
            "credentials",
            "${{ github.workspace }}",
            "~/.gradle\n",
        ):
            self.assertNotIn(forbidden, cached_paths)

    def test_cache_measurements_are_private_log_only_and_lifecycle_checked(self) -> None:
        measurement = self.by_name["Record IPTV Android cache measurements"]
        script = measurement["run"]
        self.assertIn("===== android-cache =====", script)
        self.assertIn("gradle_wrapper", script)
        self.assertIn("gradle_modules", script)
        self.assertIn("gradle_build_cache", script)
        self.assertIn('>> "${CI_LOG}"', script)
        finish = self.by_name["Finish Agent State run"]
        status = finish["with"]["status"]
        self.assertIn("steps.android_cache_measurements.outcome == 'success'", status)
        self.assertIn("steps.gradle_dependency_cache_save.outcome == 'success'", status)
        self.assertIn("steps.gradle_build_cache_save.outcome == 'success'", status)


class AndroidGenericHostedProfileContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.workflow_text = WORKFLOW_PATH.read_text(encoding="utf-8")
        cls.workflow = yaml.safe_load(cls.workflow_text)
        steps = cls.workflow["jobs"]["ci"]["steps"]
        cls.by_name = {step.get("name"): step for step in steps if step.get("name")}

    def test_generic_profiles_use_one_fixed_product_wrapper(self) -> None:
        preflight = self.by_name["Validate generic Android hosted request"]
        for profile in ("build", "test", "emulator"):
            self.assertIn(f"inputs.test_profile == '{profile}'", preflight["if"])
        preflight_script = preflight["run"]
        self.assertIn('test "${PROJECT_DIRECTORY}" = "."', preflight_script)
        self.assertIn('test -z "${TEST_FILTER}"', preflight_script)
        self.assertIn('test "${ROOM_SCHEMA}" = "false"', preflight_script)
        self.assertIn("scripts/ci/run-android-hosted-validation.sh", preflight_script)

        script = self.by_name["Run fixed Android profile"]["run"]
        self.assertIn('build|test|emulator)', script)
        self.assertIn(
            'wrapper="${repository_root}/scripts/ci/run-android-hosted-validation.sh"',
            script,
        )
        self.assertIn('export CI_ANDROID_HOSTED_PROFILE="${TEST_PROFILE}"', script)
        self.assertIn('run_logged "android-${TEST_PROFILE}" bash "${wrapper}"', script)
        self.assertLess(script.index('build|test|emulator)'), script.index('test -x gradlew'))
        self.assertNotIn("streamscape-media", script.lower())

    def test_generic_profiles_do_not_enter_iptv_private_git_or_cache_paths(self) -> None:
        private_git = self.by_name["Connect to private Git service"]["if"]
        develop_scope = self.by_name["Resolve IPTV Android develop dependency cache scope"]["if"]
        branch_scope = self.by_name["Resolve IPTV Android branch cache reader scope"]["if"]
        for profile in ("build", "test", "emulator"):
            token = f"inputs.test_profile != '{profile}'"
            self.assertIn(token, private_git)
            self.assertIn(token, develop_scope)
            self.assertIn(token, branch_scope)

    def test_emulator_profile_uses_fixed_central_boot_and_cleanup(self) -> None:
        prepare = self.by_name["Prepare generic Android emulator"]
        cleanup = self.by_name["Stop generic Android emulator"]
        self.assertEqual(prepare["if"], "${{ inputs.test_profile == 'emulator' }}")
        script = prepare["run"]
        self.assertIn("system-images;android-36;google_apis;x86_64", script)
        self.assertIn("central-android-api36", script)
        self.assertIn("emulator-5554", script)
        self.assertIn("seq 1 180", script)
        self.assertIn("sys.boot_completed", script)
        self.assertIn("printf 'ANDROID_SERIAL=%s\\n'", script)
        self.assertNotIn("inputs.", script)
        self.assertEqual(
            cleanup["if"],
            "${{ always() && inputs.test_profile == 'emulator' }}",
        )
        self.assertIn("emu kill", cleanup["run"])



if __name__ == "__main__":
    unittest.main()
