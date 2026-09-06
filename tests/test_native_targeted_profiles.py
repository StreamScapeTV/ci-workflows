from pathlib import Path
import json
import os
import subprocess
import tempfile
import unittest
import yaml

ROOT = Path(__file__).resolve().parents[1]


class NativeTargetedProfileTests(unittest.TestCase):
    def test_dispatch_passes_bounded_selector_inputs(self) -> None:
        text = (ROOT / ".github/workflows/central-ci-dispatch.yml").read_text(encoding="utf-8")
        self.assertIn("test_selectors: ${{ fromJSON(needs.request.outputs.inputs_json).test_selectors || '[]' }}", text)
        self.assertIn("test_platform: ${{ fromJSON(needs.request.outputs.inputs_json).test_platform || '' }}", text)
        self.assertIn("test_filter: ${{ fromJSON(needs.request.outputs.inputs_json).test_filter || '' }}", text)
        workflow = yaml.safe_load(text)
        preflight = next(
            step for step in workflow["jobs"]["request"]["steps"]
            if step.get("name") == "Validate native targeted test request"
        )["run"]
        self.assertIn("1 through 20 selectors", preflight)
        self.assertIn("len(selector_payload) > 2048", preflight)
        self.assertIn("test_platform=jvm or instrumentation", preflight)
        self.assertIn("test_platform=macos, ios, or tvos", preflight)
        self.assertIn("legacy targeted-unit", preflight)
        self.assertIn('profile in {"targeted-tests", "candidate"}', preflight)
        self.assertIn("candidate is supported only by validation.apple", preflight)
        self.assertIn("Apple candidate fixes macOS tests and does not accept test_platform", preflight)

        valid_candidate = subprocess.run(
            ["bash", "-c", preflight],
            env={
                **os.environ,
                "WORKFLOW_KEY": "validation.apple",
                "TEST_PROFILE": "candidate",
                "INPUTS_JSON": json.dumps(
                    {"test_selectors": json.dumps(["streamscapetvTests/AppTourScreenLogicIntegrationTests"])}
                ),
            },
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(valid_candidate.returncode, 0, valid_candidate.stderr)

        candidate_with_platform = subprocess.run(
            ["bash", "-c", preflight],
            env={
                **os.environ,
                "WORKFLOW_KEY": "validation.apple",
                "TEST_PROFILE": "candidate",
                "INPUTS_JSON": json.dumps(
                    {
                        "test_selectors": json.dumps(["streamscapetvTests/AppTourScreenLogicIntegrationTests"]),
                        "test_platform": "macos",
                    }
                ),
            },
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertNotEqual(candidate_with_platform.returncode, 0)
        self.assertIn("does not accept test_platform", candidate_with_platform.stderr)

    def test_android_targeted_profile_is_multi_selector_and_fixed_task(self) -> None:
        workflow = yaml.safe_load((ROOT / ".github/workflows/android.yml").read_text(encoding="utf-8"))
        inputs = workflow["on"]["workflow_call"]["inputs"]
        self.assertIn("test_selectors", inputs)
        self.assertEqual(inputs["test_platform"]["default"], "")
        self.assertEqual(inputs["test_filter"]["default"], "")
        command = next(
            step for step in workflow["jobs"]["ci"]["steps"]
            if step.get("name") == "Run fixed Android profile"
        )["run"]
        self.assertIn("targeted-tests)", command)
        self.assertIn("1 through 20 test selectors", command)
        self.assertIn("targeted-tests requires test_platform=jvm or instrumentation", command)
        self.assertIn("targeted_args=(testDebugUnitTest)", command)
        self.assertIn('targeted_args+=(--tests "${selector}")', command)
        self.assertIn('run_gradle "${targeted_args[@]}"', command)
        self.assertIn("connectedDebugAndroidTest", command)
        self.assertIn("android.testInstrumentationRunnerArguments.class=", command)
        self.assertIn("targeted-unit)", command)
        self.assertIn("Transitional compatibility for active Agent State callers", command)

    def test_android_instrumentation_installs_emulator_before_runtime_tool_checks(self) -> None:
        workflow = yaml.safe_load((ROOT / ".github/workflows/android.yml").read_text(encoding="utf-8"))
        prepare = next(
            step for step in workflow["jobs"]["ci"]["steps"]
            if step.get("name") == "Prepare generic Android emulator"
        )["run"]
        install = '"${sdkmanager}" --install platform-tools emulator "${image}"'
        preinstall_tools = 'for tool in "${sdkmanager}" "${avdmanager}"; do'
        runtime_tools = 'for tool in "${adb}" "${emulator}"; do'
        self.assertIn(preinstall_tools, prepare)
        self.assertIn(install, prepare)
        self.assertIn(runtime_tools, prepare)
        self.assertLess(prepare.index(preinstall_tools), prepare.index(install))
        self.assertLess(prepare.index(install), prepare.index('adb="$(command -v adb || true)"'))
        self.assertLess(prepare.index(install), prepare.index('emulator="$(command -v emulator || true)"'))
        self.assertLess(prepare.index(install), prepare.index(runtime_tools))
        self.assertNotIn(
            'for tool in "${sdkmanager}" "${avdmanager}" "${adb}" "${emulator}"; do',
            prepare,
        )
        self.assertIn("system-images;android-36;google_apis;x86_64", prepare)
        self.assertIn("avd_name='central-android-api36'", prepare)
        self.assertIn("serial='emulator-5554'", prepare)
        self.assertIn("seq 1 180", prepare)
        self.assertIn('avd_home="${RUNNER_TEMP}/central-android-avd"', prepare)
        self.assertIn('avd_path="${avd_home}/${avd_name}.avd"', prepare)
        self.assertIn('export ANDROID_AVD_HOME="${avd_home}"', prepare)
        self.assertIn('-p "${avd_path}"', prepare)
        self.assertIn('"${emulator}" -list-avds', prepare)
        self.assertLess(
            prepare.index('export ANDROID_AVD_HOME="${avd_home}"'),
            prepare.index('create avd --force'),
        )
        self.assertLess(
            prepare.index('create avd --force'),
            prepare.index('"${emulator}" -list-avds'),
        )
        self.assertLess(
            prepare.index('"${emulator}" -list-avds'),
            prepare.index('"${emulator}" -avd "${avd_name}"'),
        )
        cleanup = next(
            step for step in workflow["jobs"]["ci"]["steps"]
            if step.get("name") == "Stop generic Android emulator"
        )["run"]
        self.assertIn(
            'test "${ANDROID_AVD_HOME}" = "${RUNNER_TEMP}/central-android-avd"',
            cleanup,
        )
        self.assertIn('rm -rf -- "${ANDROID_AVD_HOME}"', cleanup)

    def test_apple_targeted_profile_uses_fixed_lanes_and_no_product_test_name(self) -> None:
        text = (ROOT / ".github/workflows/apple.yml").read_text(encoding="utf-8")
        workflow = yaml.safe_load(text)
        inputs = workflow["on"]["workflow_call"]["inputs"]
        self.assertEqual(inputs["test_selectors"]["default"], "[]")
        self.assertEqual(inputs["test_platform"]["default"], "")
        plan = next(
            step for step in workflow["jobs"]["plan"]["steps"]
            if step.get("name") == "Resolve fixed Apple execution lanes"
        )["run"]
        for lane in ("macos-targeted-tests", "ios-targeted-tests", "tvos-targeted-tests"):
            self.assertIn(lane, plan)
        self.assertIn("test_platform=macos, ios, or tvos", plan)
        command = next(
            step for step in workflow["jobs"]["execute"]["steps"]
            if step.get("name") == "Run fixed Apple lane"
        )["run"]
        self.assertIn('targeted_test_args+=("-only-testing:${selector}")', command)
        self.assertIn("-destination 'platform=macOS'", command)
        self.assertIn("-destination 'platform=iOS Simulator,name=iPhone 17'", command)
        self.assertIn("-destination 'platform=tvOS Simulator,name=Apple TV'", command)
        self.assertNotIn("SelectedBackendStateSyncRoutingIntegrationTests", text)

    def test_apple_candidate_profile_combines_platform_builds_with_macos_targeted_tests(self) -> None:
        workflow = yaml.safe_load((ROOT / ".github/workflows/apple.yml").read_text(encoding="utf-8"))
        plan = next(
            step for step in workflow["jobs"]["plan"]["steps"]
            if step.get("name") == "Resolve fixed Apple execution lanes"
        )["run"]
        expected_matrix = {
            "include": [
                {"lane": "ios-build", "cache_save": False},
                {"lane": "tvos-build", "cache_save": False},
                {"lane": "macos-targeted-tests", "cache_save": True},
            ]
        }
        self.assertIn("candidate)", plan)
        self.assertIn("candidate fixes macOS test execution and does not accept test_platform", plan)
        self.assertIn(
            "matrix='{\"include\":[{\"lane\":\"ios-build\",\"cache_save\":false},{\"lane\":\"tvos-build\",\"cache_save\":false},{\"lane\":\"macos-targeted-tests\",\"cache_save\":true}]}'",
            plan,
        )
        # Existing broad and targeted semantics remain visibly distinct.
        self.assertIn(
            "matrix='{\"include\":[{\"lane\":\"ios-build\",\"cache_save\":false},{\"lane\":\"tvos-build\",\"cache_save\":false},{\"lane\":\"macos-test\",\"cache_save\":true}]}'",
            plan,
        )
        self.assertIn(
            "macos) matrix='{\"include\":[{\"lane\":\"macos-targeted-tests\",\"cache_save\":false}]}' ;;",
            plan,
        )

        selectors = [
            "streamscapetvTests/AppTourScreenLogicIntegrationTests",
            "streamscapetvTests/AppTourSettledHomePresentationIntegrationTests",
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "github-output"
            result = subprocess.run(
                ["bash", "-c", plan],
                env={
                    **os.environ,
                    "TEST_PROFILE": "candidate",
                    "TEST_SELECTORS": json.dumps(selectors),
                    "TEST_PLATFORM": "",
                    "GITHUB_OUTPUT": str(output_path),
                },
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            matrix_line = output_path.read_text(encoding="utf-8").strip()
            self.assertTrue(matrix_line.startswith("matrix="), matrix_line)
            self.assertEqual(json.loads(matrix_line.removeprefix("matrix=")), expected_matrix)

        command = next(
            step for step in workflow["jobs"]["execute"]["steps"]
            if step.get("name") == "Run fixed Apple lane"
        )["run"]
        self.assertIn(
            'if test "${TEST_PROFILE}" = targeted-tests || test "${TEST_PROFILE}" = candidate; then',
            command,
        )
        self.assertIn('targeted_test_args+=("-only-testing:${selector}")', command)

    def test_targeted_profiles_fail_closed_on_selector_shape(self) -> None:
        android = (ROOT / ".github/workflows/android.yml").read_text(encoding="utf-8")
        apple = (ROOT / ".github/workflows/apple.yml").read_text(encoding="utf-8")
        for text in (android, apple):
            self.assertIn("1 through 20 test selectors", text)
            self.assertIn("must be unique", text)
            self.assertIn("startswith", text)
        self.assertIn(r"[A-Za-z0-9_.$*?-]{1,240}", android)
        self.assertIn(r"[A-Za-z0-9_.$/-]{1,240}", apple)


if __name__ == "__main__":
    unittest.main()
