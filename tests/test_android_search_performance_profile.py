from pathlib import Path
import os
import subprocess
import tempfile
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

    def test_profile_does_not_broaden_default_branch_dependency_cache(self) -> None:
        steps = self.workflow["jobs"]["ci"]["steps"]
        save_step = next(
            step
            for step in steps
            if step.get("name") == "Save IPTV Android default-branch dependency cache"
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

    def test_feature_and_pr_reader_scope_excludes_actual_default_branch_and_detached_tags(self) -> None:
        scope = self.by_name["Resolve IPTV Android non-default cache reader scope"]
        script = scope["run"]
        self.assertIn("StreamScapeTV/iptv-android", script)
        self.assertIn("git symbolic-ref --quiet --short HEAD", script)
        self.assertIn('[[ "${source_ref}" == refs/pull/* ]]', script)
        self.assertIn('test "${checkout_branch}" != "${DEFAULT_BRANCH}"', script)
        self.assertIn('test "${normalized_ref}" != "${DEFAULT_BRANCH}"', script)
        self.assertNotIn('test "${checkout_branch}" != develop', script)
        self.assertNotIn('test "${checkout_branch}" != main', script)
        self.assertNotIn("refs/tags/*", script)

    def test_default_writer_follows_github_repository_metadata(self) -> None:
        scope = self.by_name["Resolve IPTV Android default-branch cache scope"]
        script = scope["run"]
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            subprocess.run(["git", "init", "-b", "develop"], cwd=root, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.email", "ci@example.invalid"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.name", "CI"], cwd=root, check=True)
            (root / "README.md").write_text("fixture\n", encoding="utf-8")
            (root / "gradle" / "wrapper").mkdir(parents=True)
            (root / "gradle" / "wrapper" / "gradle-wrapper.properties").write_text(
                "distributionUrl=https://services.gradle.org/distributions/gradle-9.7.1-bin.zip\n",
                encoding="utf-8",
            )
            subprocess.run(["git", "add", "."], cwd=root, check=True)
            subprocess.run(["git", "commit", "-m", "fixture"], cwd=root, check=True, capture_output=True)
            fake_bin = root / "bin"
            fake_bin.mkdir()
            fake_curl = fake_bin / "curl"
            fake_curl.write_text(
                "#!/bin/sh\nprintf '{\"default_branch\":\"%s\"}\\n' \"${FAKE_DEFAULT_BRANCH:-develop}\"\n",
                encoding="utf-8",
            )
            fake_curl.chmod(0o755)

            def writer_enabled(ref: str, default_branch: str) -> str:
                output = root / "github-output"
                output.write_text("", encoding="utf-8")
                env = os.environ.copy()
                env.update(
                    {
                        "SOURCE_REPOSITORY": "StreamScapeTV/iptv-android",
                        "REQUESTED_REF": ref,
                        "CURRENT_REF_NAME": "",
                        "CURRENT_REF_TYPE": "branch",
                        "SOURCE_TOKEN": "token",
                        "FAKE_DEFAULT_BRANCH": default_branch,
                        "GITHUB_OUTPUT": str(output),
                        "PATH": f"{fake_bin}:{env['PATH']}",
                    }
                )
                result = subprocess.run(
                    ["bash"], cwd=root, env=env, input=script, text=True, capture_output=True, check=False
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                values = dict(line.split("=", 1) for line in output.read_text().splitlines())
                return values["enabled"]

            self.assertEqual(writer_enabled("develop", "develop"), "true")
            self.assertEqual(writer_enabled("develop", "main"), "false")
            subprocess.run(["git", "switch", "-c", "main"], cwd=root, check=True, capture_output=True)
            self.assertEqual(writer_enabled("main", "main"), "true")

    def test_dependency_fingerprint_tracks_dependencies_not_release_metadata(self) -> None:
        script = self.by_name["Resolve IPTV Android default-branch cache scope"]["run"]
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            subprocess.run(["git", "init", "-b", "develop"], cwd=root, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.email", "ci@example.invalid"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.name", "CI"], cwd=root, check=True)
            (root / "gradle" / "wrapper").mkdir(parents=True)
            (root / "gradle" / "wrapper" / "gradle-wrapper.properties").write_text(
                "distributionUrl=https://services.gradle.org/distributions/gradle-9.7.1-bin.zip\n",
                encoding="utf-8",
            )
            (root / "gradle" / "libs.versions.toml").write_text(
                "[versions]\nalpha = \"1.0\"\nbeta = \"1.0\"\n"
                "[libraries]\nalpha = { module = \"example:alpha\", version.ref = \"alpha\" }\n"
                "beta = { module = \"example:beta\", version.ref = \"beta\" }\n"
                "[plugins]\nandroid-application = { id = \"com.android.application\", version = \"9.4.0\" }\n",
                encoding="utf-8",
            )
            (root / "settings.gradle.kts").write_text(
                'rootProject.name = "Fixture"\ndependencyResolutionManagement { repositories { mavenCentral() } }\n',
                encoding="utf-8",
            )
            app = root / "app"
            app.mkdir()
            build = app / "build.gradle.kts"
            build.write_text(
                "plugins {\n    alias(libs.plugins.android.application)\n}\n"
                "android { defaultConfig { versionCode = 1 } }\n"
                "dependencies {\n    implementation(libs.alpha)\n}\n",
                encoding="utf-8",
            )
            subprocess.run(["git", "add", "."], cwd=root, check=True)
            subprocess.run(["git", "commit", "-m", "fixture"], cwd=root, check=True, capture_output=True)
            fake_bin = root / "bin"
            fake_bin.mkdir()
            fake_curl = fake_bin / "curl"
            fake_curl.write_text("#!/bin/sh\nprintf '%s\\n' '{\"default_branch\":\"develop\"}'\n", encoding="utf-8")
            fake_curl.chmod(0o755)

            def fingerprint() -> str:
                output = root / "github-output"
                output.write_text("", encoding="utf-8")
                env = os.environ.copy()
                env.update(
                    {
                        "SOURCE_REPOSITORY": "StreamScapeTV/iptv-android",
                        "REQUESTED_REF": "develop",
                        "CURRENT_REF_NAME": "",
                        "CURRENT_REF_TYPE": "branch",
                        "SOURCE_TOKEN": "token",
                        "GITHUB_OUTPUT": str(output),
                        "PATH": f"{fake_bin}:{env['PATH']}",
                    }
                )
                result = subprocess.run(
                    ["bash"], cwd=root, env=env, input=script, text=True, capture_output=True, check=False
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                values = dict(line.split("=", 1) for line in output.read_text().splitlines())
                return values["dependency_fingerprint"]

            baseline = fingerprint()
            build.write_text(build.read_text().replace("versionCode = 1", "versionCode = 2"), encoding="utf-8")
            self.assertEqual(fingerprint(), baseline)

            build.write_text(build.read_text().replace("implementation(libs.alpha)", "implementation(libs.beta)"), encoding="utf-8")
            dependency_changed = fingerprint()
            self.assertNotEqual(dependency_changed, baseline)

            wrapper = root / "gradle" / "wrapper" / "gradle-wrapper.properties"
            wrapper.write_text(wrapper.read_text().replace("9.7.1", "9.8.0"), encoding="utf-8")
            self.assertNotEqual(fingerprint(), dependency_changed)

    def test_branch_dependency_restore_is_read_only_and_uses_trusted_default_family(self) -> None:
        restore = self.by_name["Restore IPTV Android non-default dependency cache"]
        self.assertEqual(
            restore["if"],
            "${{ steps.android_branch_cache_scope.outputs.enabled == 'true' }}",
        )
        self.assertEqual(restore["uses"], "actions/cache/restore@v4")
        self.assertEqual(
            restore["with"]["path"],
            "~/.gradle/wrapper\n~/.gradle/caches/modules-2\n",
        )
        self.assertIn("iptv-android-default-gradle-deps-v2-", restore["with"]["key"])
        self.assertIn(
            "iptv-android-default-gradle-deps-v2-",
            restore["with"]["restore-keys"],
        )
        self.assertNotIn("actions/cache/save", str(restore))

    def test_gradle_task_output_cache_is_not_persisted(self) -> None:
        self.assertNotIn("Restore IPTV Android Gradle build cache", self.by_name)
        self.assertNotIn("Save IPTV Android Gradle build cache", self.by_name)
        self.assertNotIn("~/.gradle/caches/build-cache-1", self.workflow_text)
        self.assertNotIn("iptv-android-default-gradle-build-", self.workflow_text)

    def test_only_default_branch_full_or_release_can_save_dependency_cache(self) -> None:
        step = self.by_name["Save IPTV Android default-branch dependency cache"]
        condition = step["if"]
        self.assertIn(
            "steps.android_default_cache_scope.outputs.enabled == 'true'",
            condition,
        )
        self.assertIn("steps.commands.outcome == 'success'", condition)
        self.assertIn("inputs.test_profile == 'full'", condition)
        self.assertIn("inputs.test_profile == 'release'", condition)
        self.assertIn("cache-hit != 'true'", condition)

    def test_cache_paths_exclude_task_outputs_source_credentials_and_full_gradle_home(self) -> None:
        cache_steps = (
            self.by_name["Restore IPTV Android default-branch dependency cache"],
            self.by_name["Restore IPTV Android non-default dependency cache"],
            self.by_name["Save IPTV Android default-branch dependency cache"],
        )
        cached_paths = "\n".join(str(step["with"]["path"]) for step in cache_steps)
        self.assertIn("~/.gradle/wrapper", cached_paths)
        self.assertIn("~/.gradle/caches/modules-2", cached_paths)
        self.assertNotIn("~/.gradle/caches/build-cache-1", cached_paths)
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
        self.assertNotIn("gradle_build_cache", script)
        self.assertIn('>> "${CI_LOG}"', script)
        finish = self.by_name["Finish Agent State run"]
        status = finish["with"]["status"]
        self.assertIn("steps.android_cache_measurements.outcome == 'success'", status)
        self.assertIn("steps.gradle_dependency_cache_save.outcome == 'success'", status)
        self.assertNotIn("gradle_build_cache_save", status)


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
        self.assertIn('test "${TEST_SELECTORS}" = "[]"', preflight_script)
        self.assertIn('test -z "${TEST_PLATFORM}"', preflight_script)
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
        default_scope = self.by_name["Resolve IPTV Android default-branch cache scope"]["if"]
        branch_scope = self.by_name["Resolve IPTV Android non-default cache reader scope"]["if"]
        for profile in ("build", "test", "emulator"):
            token = f"inputs.test_profile != '{profile}'"
            self.assertIn(token, private_git)
            self.assertIn(token, default_scope)
            self.assertIn(token, branch_scope)

    def test_emulator_profile_uses_fixed_central_boot_and_cleanup(self) -> None:
        prepare = self.by_name["Prepare generic Android emulator"]
        cleanup = self.by_name["Stop generic Android emulator"]
        self.assertIn("inputs.test_profile == 'emulator'", prepare["if"])
        self.assertIn("inputs.test_profile == 'targeted-tests'", prepare["if"])
        self.assertIn("inputs.test_platform == 'instrumentation'", prepare["if"])
        script = prepare["run"]
        self.assertIn("system-images;android-36;google_apis;x86_64", script)
        self.assertIn("central-android-api36", script)
        self.assertIn("emulator-5554", script)
        self.assertIn("seq 1 180", script)
        self.assertIn("sys.boot_completed", script)
        self.assertIn("printf 'ANDROID_SERIAL=%s\\n'", script)
        self.assertNotIn("inputs.", script)
        self.assertIn("inputs.test_profile == 'emulator'", cleanup["if"])
        self.assertIn("inputs.test_profile == 'targeted-tests'", cleanup["if"])
        self.assertIn("inputs.test_platform == 'instrumentation'", cleanup["if"])
        self.assertIn("emu kill", cleanup["run"])



class AndroidPlayReleaseContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.workflow_text = WORKFLOW_PATH.read_text(encoding="utf-8")
        cls.workflow = yaml.safe_load(cls.workflow_text)
        steps = cls.workflow["jobs"]["ci"]["steps"]
        cls.by_name = {step.get("name"): step for step in steps if step.get("name")}

    def test_play_release_has_one_explicit_build_number_and_one_private_credential(self) -> None:
        call = self.workflow["on"]["workflow_call"]
        self.assertIn("build_number", call["inputs"])
        self.assertEqual(call["inputs"]["build_number"]["default"], "")
        self.assertIn("GOOGLE_PLAY_SERVICE_ACCOUNT_JSON_BASE64", call["secrets"])
        for secret in (
            "ANDROID_PLAY_UPLOAD_KEYSTORE_BASE64",
            "ANDROID_PLAY_UPLOAD_KEYSTORE_PASSWORD",
            "ANDROID_PLAY_UPLOAD_KEY_ALIAS",
            "ANDROID_PLAY_UPLOAD_KEY_PASSWORD",
        ):
            self.assertIn(secret, call["secrets"])
        prepare = self.by_name["Prepare fixed Google Play draft release context"]
        self.assertEqual(prepare["if"], "${{ inputs.test_profile == 'play' }}")
        self.assertEqual(prepare["env"]["BUILD_NUMBER"], "${{ inputs.build_number }}")
        self.assertEqual(
            prepare["env"]["GOOGLE_PLAY_SERVICE_ACCOUNT_JSON_BASE64"],
            "${{ secrets.GOOGLE_PLAY_SERVICE_ACCOUNT_JSON_BASE64 }}",
        )
        script = prepare["run"]
        self.assertIn('[[ "${BUILD_NUMBER}" =~ ^[1-9][0-9]{0,9}$ ]]', script)
        self.assertIn("BUILD_NUMBER <= 2100000000", script)
        self.assertIn("base64 --decode", script)
        self.assertIn('chmod 600 "${credential_path}" "${keystore_path}"', script)
        self.assertIn("ANDROID_PLAY_UPLOAD_KEYSTORE_BASE64", script)
        self.assertIn('keytool -list -keystore "${keystore_path}"', script)
        self.assertIn("CI_ANDROID_PLAY_UPLOAD_KEYSTORE_PATH", script)
        self.assertIn('value.get("type") != "service_account"', script)
        self.assertIn("client_email", script)
        self.assertIn("private_key", script)
        self.assertIn("token_uri", script)

    def test_play_destination_is_fixed_internal_draft_not_caller_selectable(self) -> None:
        prepare = self.by_name["Prepare fixed Google Play draft release context"]["run"]
        self.assertIn("CI_ANDROID_PLAY_TRACK=internal", prepare)
        self.assertIn("CI_ANDROID_PLAY_RELEASE_STATUS=draft", prepare)
        commands = self.by_name["Run fixed Android profile"]
        script = commands["run"]
        self.assertIn("play)", script)
        self.assertIn('wrapper="${repository_root}/scripts/ci/run-android-play-release.sh"', script)
        self.assertIn('test "${CI_ANDROID_PLAY_TRACK:-}" = internal', script)
        self.assertIn('test "${CI_ANDROID_PLAY_RELEASE_STATUS:-}" = draft', script)
        self.assertIn('test -f "${CI_ANDROID_PLAY_UPLOAD_KEYSTORE_PATH}"', script)
        self.assertIn('test -n "${CI_ANDROID_PLAY_UPLOAD_KEYSTORE_PASSWORD:-}"', script)
        self.assertIn("run_logged android-play-draft", script)
        for forbidden in ("production", "open testing", "closed testing", "userFraction"):
            self.assertNotIn(forbidden, script)
        self.assertNotIn("inputs.", script)

    def test_play_credential_is_cleaned_and_not_exposed_to_ordinary_profiles(self) -> None:
        cleanup = self.by_name["Clean Google Play credential and release state"]
        self.assertEqual(cleanup["if"], "${{ always() && inputs.test_profile == 'play' }}")
        self.assertIn('rm -rf -- "${release_root}"', cleanup["run"])
        scrub = self.by_name["Scrub configured CI secrets from private log"]
        self.assertEqual(
            scrub["env"]["CI_SECRET_GOOGLE_PLAY_SERVICE_ACCOUNT_JSON"],
            "${{ inputs.test_profile == 'play' && secrets.GOOGLE_PLAY_SERVICE_ACCOUNT_JSON_BASE64 || '' }}",
        )
        for key in (
            "CI_SECRET_ANDROID_PLAY_UPLOAD_KEYSTORE_BASE64",
            "CI_SECRET_ANDROID_PLAY_UPLOAD_KEYSTORE_PASSWORD",
            "CI_SECRET_ANDROID_PLAY_UPLOAD_KEY_ALIAS",
            "CI_SECRET_ANDROID_PLAY_UPLOAD_KEY_PASSWORD",
        ):
            self.assertIn(key, scrub["env"])
            self.assertIn("inputs.test_profile == 'play'", scrub["env"][key])
        finish = self.by_name["Finish Agent State run"]
        self.assertIn("steps.play_cleanup.outcome == 'success'", finish["with"]["status"])



if __name__ == "__main__":
    unittest.main()
