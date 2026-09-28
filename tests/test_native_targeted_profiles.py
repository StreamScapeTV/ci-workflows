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
            if step.get("name") == "Validate remaining Android legacy targeted request"
        )["run"]
        self.assertIn("1 through 20 selectors", preflight)
        self.assertIn("len(selector_payload) > 2048", preflight)
        self.assertIn("test_platform=jvm or instrumentation", preflight)
        self.assertIn("legacy targeted-unit", preflight)
        self.assertNotIn("validation.apple", preflight)
        self.assertNotIn("native-component", preflight)
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

    def _run_android_targeted_command(
        self,
        *,
        platform: str,
        gradlew_body: str,
        precreate_results: dict[str, bytes] | None = None,
    ) -> tuple[subprocess.CompletedProcess[str], str]:
        workflow = yaml.safe_load((ROOT / ".github/workflows/android.yml").read_text(encoding="utf-8"))
        command = next(
            step for step in workflow["jobs"]["ci"]["steps"]
            if step.get("name") == "Run fixed Android profile"
        )["run"]
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            runner_temp = temp / "runner-temp"
            runner_temp.mkdir()
            log_path = temp / "central-ci.log"
            log_path.write_text("", encoding="utf-8")
            gradlew = temp / "gradlew"
            gradlew.write_text("#!/usr/bin/env bash\nset -Eeuo pipefail\n" + gradlew_body, encoding="utf-8")
            gradlew.chmod(0o755)
            for relative, data in (precreate_results or {}).items():
                result_path = temp / relative
                result_path.parent.mkdir(parents=True, exist_ok=True)
                result_path.write_bytes(data)
            result = subprocess.run(
                ["bash", "-c", command],
                cwd=temp,
                env={
                    **os.environ,
                    "CI_LOG": str(log_path),
                    "RUNNER_TEMP": str(runner_temp),
                    "TEST_PROFILE": "targeted-tests",
                    "TEST_PLATFORM": platform,
                    "TEST_SELECTORS": json.dumps(
                        ["com.example.testing.integration.ExampleInstrumentationTest#fails"]
                    ),
                    "TEST_FILTER": "",
                    "ROOM_SCHEMA": "false",
                    "PROJECT_DIRECTORY": ".",
                },
                text=True,
                capture_output=True,
                check=False,
            )
            return result, log_path.read_text(encoding="utf-8", errors="replace")

    def test_android_instrumentation_failure_appends_private_machine_readable_result(self) -> None:
        failure_xml = b'''<?xml version="1.0" encoding="UTF-8"?>\n<testsuite tests="1" failures="1">\n  <testcase classname="com.example.testing.integration.ExampleInstrumentationTest" name="fails">\n    <failure message="expected true but was false" type="java.lang.AssertionError">java.lang.AssertionError: expected true but was false\n\tat com.example.testing.integration.ExampleInstrumentationTest.fails(ExampleInstrumentationTest.kt:42)</failure>\n  </testcase>\n</testsuite>\n'''
        result, private_log = self._run_android_targeted_command(
            platform="instrumentation",
            gradlew_body='printf "%s\\n" "There were failing tests. See the report at: ephemeral/index.html"\nexit 1\n',
            precreate_results={
                "app/build/outputs/androidTest-results/connected/debug/TEST-example.xml": failure_xml,
            },
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("gradle-targeted-tests failed; inspect the private Google Drive CI log.", result.stderr)
        self.assertNotIn("expected true but was false", result.stderr)
        self.assertNotIn("ExampleInstrumentationTest.kt:42", result.stderr)
        self.assertIn("===== android-instrumentation-failure-evidence =====", private_log)
        self.assertIn("max_files=20", private_log)
        self.assertIn("max_result_bytes=262144", private_log)
        self.assertIn(
            "android-instrumentation-result path=app/build/outputs/androidTest-results/connected/debug/TEST-example.xml",
            private_log,
        )
        self.assertIn("ExampleInstrumentationTest", private_log)
        self.assertIn("name=\"fails\"", private_log)
        self.assertIn("java.lang.AssertionError: expected true but was false", private_log)
        self.assertIn("ExampleInstrumentationTest.kt:42", private_log)
        self.assertIn("captured_files=1", private_log)
        self.assertIn("result_files=1", private_log)

    def test_android_instrumentation_failure_evidence_is_bounded(self) -> None:
        oversized = b"<failure>" + (b"x" * 300000) + b"</failure>\n"
        result, private_log = self._run_android_targeted_command(
            platform="instrumentation",
            gradlew_body="exit 1\n",
            precreate_results={
                "app/build/outputs/androidTest-results/connected/debug/TEST-oversized.xml": oversized,
            },
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("captured_files=1", private_log)
        self.assertIn("result_files=1", private_log)
        self.assertIn("captured_result_bytes=262144", private_log)
        self.assertIn(
            "[TRUNCATED android instrumentation failure evidence at reviewed file/byte cap]",
            private_log,
        )

    def test_android_instrumentation_failure_evidence_file_cap_is_stable(self) -> None:
        results = {
            f"module/build/outputs/androidTest-results/connected/debug/TEST-{index:02}.xml":
                f"<failure>{index:02}</failure>\n".encode()
            for index in reversed(range(22))
        }
        result, private_log = self._run_android_targeted_command(
            platform="instrumentation",
            gradlew_body="exit 1\n",
            precreate_results=results,
        )
        self.assertEqual(result.returncode, 1)
        headers = [
            line
            for line in private_log.splitlines()
            if line.startswith("--- android-instrumentation-result path=")
        ]
        self.assertEqual(len(headers), 20)
        self.assertEqual(headers, sorted(headers))
        self.assertIn("captured_files=20", private_log)
        self.assertIn("result_files=22", private_log)
        self.assertNotIn("TEST-20.xml size=", private_log)
        self.assertNotIn("TEST-21.xml size=", private_log)
        self.assertIn(
            "[TRUNCATED android instrumentation failure evidence at reviewed file/byte cap]",
            private_log,
        )

    def test_android_instrumentation_success_does_not_append_failure_evidence(self) -> None:
        result, private_log = self._run_android_targeted_command(
            platform="instrumentation",
            gradlew_body="exit 0\n",
            precreate_results={
                "app/build/outputs/androidTest-results/connected/debug/TEST-success.xml": b"<testsuite failures=\"0\"/>\n",
            },
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("android-instrumentation-failure-evidence", private_log)
        self.assertIn("===== gradle-wall-time =====", private_log)

    def test_android_jvm_failure_does_not_append_instrumentation_evidence(self) -> None:
        result, private_log = self._run_android_targeted_command(
            platform="jvm",
            gradlew_body="exit 1\n",
            precreate_results={
                "app/build/outputs/androidTest-results/connected/debug/TEST-stale.xml": b"<failure>private</failure>\n",
            },
        )
        self.assertEqual(result.returncode, 1)
        self.assertNotIn("android-instrumentation-failure-evidence", private_log)
        self.assertNotIn("<failure>private</failure>", private_log)


    def test_targeted_profiles_fail_closed_on_selector_shape(self) -> None:
        android = (ROOT / ".github/workflows/android.yml").read_text(encoding="utf-8")
        self.assertIn("1 through 20 test selectors", android)
        self.assertIn("must be unique", android)
        self.assertIn("startswith", android)
        self.assertIn(r"[A-Za-z0-9_.$*?-]{1,240}", android)

if __name__ == "__main__":
    unittest.main()
