from pathlib import Path
import json
import os
import subprocess
import tempfile
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]


class AppleIosSimulatorRecoveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        workflow = yaml.safe_load(
            (ROOT / ".github/workflows/apple.yml").read_text(encoding="utf-8")
        )
        cls.command = next(
            step
            for step in workflow["jobs"]["execute"]["steps"]
            if step.get("name") == "Run fixed Apple lane"
        )["run"]

    def test_ios_targeted_lane_uses_exact_fresh_simulator_and_bounded_retry(self) -> None:
        command = self.command
        for token in (
            "xcrun simctl list runtimes --json",
            "xcrun simctl list devicetypes --json",
            "xcrun simctl create",
            "xcrun simctl boot",
            "xcrun simctl bootstatus",
            'platform=iOS Simulator,id=${ios_targeted_udid}',
            "-parallel-testing-enabled NO",
            "-maximum-parallel-testing-workers 1",
            "Failed to launch app with identifier: .*\\.xctrunner",
            "NSMachErrorDomain Code=-308",
            "\\(ipc/mig\\) server died",
            "Failed to get background assertion for target app",
            "DebuggerLLDB\\.DebuggerVersionStore\\.StoreError",
            "dyld(\\[[0-9]+\\])?: Library not loaded:",
            "Deterministic product dependency loader failure evidence detected; simulator retry suppressed.",
            "===== ios-targeted-tests attempt=%s =====",
            "===== ios-targeted-simulator-diagnostics attempt=%s =====",
        ):
            self.assertIn(token, command)
        self.assertIn('while test "${attempt}" -le 2', command)
        self.assertIn('attempt=$((attempt + 1))', command)

    def test_existing_screenshot_retry_contract_remains_present(self) -> None:
        self.assertIn("run_screenshot_logged()", self.command)
        self.assertIn(
            "Failed to get background assertion for target app|DebuggerLLDB\\.DebuggerVersionStore\\.StoreError",
            self.command,
        )

    def _run(self, scenario: str) -> tuple[subprocess.CompletedProcess[str], str, int, list[str]]:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            fake_bin = root / "bin"
            fake_bin.mkdir()
            state = root / "state"
            state.mkdir()
            runner_temp = root / "runner"
            runner_temp.mkdir()
            ci_log = root / "central-ci.log"
            ci_log.write_text("", encoding="utf-8")
            args_log = state / "xcodebuild-args.log"

            xcrun = fake_bin / "xcrun"
            xcrun.write_text(
                """#!/usr/bin/env bash
set -Eeuo pipefail
state=${SIM_STATE_DIR:?}
if [[ \"${1:-}\" != simctl ]]; then exit 2; fi
shift
case \"${1:-}\" in
  list)
    case \"${2:-}\" in
      runtimes)
        printf '%s\\n' '{\"runtimes\":[{\"identifier\":\"com.apple.CoreSimulator.SimRuntime.iOS-26-0\",\"version\":\"26.0\",\"isAvailable\":true}]}'
        ;;
      devicetypes)
        printf '%s\\n' '{\"devicetypes\":[{\"name\":\"iPhone 17\",\"identifier\":\"com.apple.CoreSimulator.SimDeviceType.iPhone-17\"}]}'
        ;;
      *) printf '%s\\n' '== Devices ==' ;;
    esac
    ;;
  create)
    count_file=\"${state}/create-count\"
    count=0
    [[ -f \"${count_file}\" ]] && count=$(cat \"${count_file}\")
    count=$((count + 1))
    printf '%s' \"${count}\" > \"${count_file}\"
    printf '00000000-0000-0000-0000-%012d\\n' \"${count}\"
    ;;
  boot|bootstatus|shutdown|delete) exit 0 ;;
  spawn) printf '%s\\n' 'mock simulator diagnostic line' ;;
  *) exit 0 ;;
esac
""",
                encoding="utf-8",
            )
            xcrun.chmod(0o755)

            xcodebuild = fake_bin / "xcodebuild"
            xcodebuild.write_text(
                """#!/usr/bin/env bash
set -Eeuo pipefail
if [[ \"${1:-}\" == -version ]]; then
  printf '%s\\n' 'Xcode 26.0' 'Build version 17A000'
  exit 0
fi
state=${SIM_STATE_DIR:?}
count_file=\"${state}/xcodebuild-count\"
count=0
[[ -f \"${count_file}\" ]] && count=$(cat \"${count_file}\")
count=$((count + 1))
printf '%s' \"${count}\" > \"${count_file}\"
printf '%s\\n' \"$*\" >> \"${XCODEBUILD_ARGS_LOG:?}\"
case \"${XCODEBUILD_SCENARIO:?}\" in
  infra_then_success)
    if [[ \"${count}\" -eq 1 ]]; then
      printf '%s\\n' 'Failed to launch app with identifier: com.streamscapetvUITests.xctrunner'
      printf '%s\\n' 'Error Domain=NSMachErrorDomain Code=-308 \"(ipc/mig) server died\"'
      exit 1
    fi
    ;;
  infra_twice)
    printf '%s\\n' 'Failed to launch app with identifier: com.streamscapetvUITests.xctrunner'
    printf '%s\\n' 'Error Domain=NSMachErrorDomain Code=-308 \"(ipc/mig) server died\"'
    exit 1
    ;;
  pre_xctest_bootstrap_then_success)
    if [[ "${count}" -eq 1 ]]; then
      printf '%s\\n' 'Early unexpected exit, operation never finished bootstrapping'
      printf '%s\\n' 'Test crashed with signal abrt before establishing connection'
      exit 1
    fi
    ;;
  pre_xctest_bootstrap_twice)
    printf '%s\\n' 'Early unexpected exit, operation never finished bootstrapping'
    printf '%s\\n' 'Test crashed with signal abrt before establishing connection'
    exit 1
    ;;
  generic_sigabrt)
    printf '%s\\n' 'Test crashed with signal SIGABRT'
    exit 1
    ;;
  post_connection_crash)
    printf '%s\\n' "Test Case '-[CatalogToolbarSmokeTests testMovies]' started."
    printf '%s\\n' "Test Case '-[CatalogToolbarSmokeTests testMovies]' crashed with signal SIGABRT"
    exit 1
    ;;
  bootstrap_text_with_crashed_case_no_started)
    printf '%s\\n' 'Early unexpected exit, operation never finished bootstrapping'
    printf '%s\\n' "Test Case '-[CatalogToolbarSmokeTests testMovies]' crashed with signal SIGABRT"
    exit 1
    ;;
  bootstrap_text_with_other_exc_failure)
    printf '%s\\n' 'Early unexpected exit, operation never finished bootstrapping'
    printf '%s\\n' 'EXC_BAD_INSTRUCTION (code=EXC_I386_INVOP, subcode=0x0)'
    exit 1
    ;;
  bootstrap_text_with_dyld_missing_dependency)
    printf '%s\\n' 'Early unexpected exit, operation never finished bootstrapping'
    printf '%s\\n' 'dyld[4242]: Library not loaded: @rpath/libexample_engine.dylib'
    printf '%s\\n' '  Reason: tried: /tmp/libexample_engine.dylib (no such file)'
    printf '%s\\n' 'Test crashed with signal abrt before establishing connection'
    exit 1
    ;;
  dyld_missing_dependency)
    printf '%s\\n' 'dyld: Library not loaded: @rpath/libexample_engine.dylib'
    printf '%s\\n' '  Reason: tried: /tmp/libexample_engine.dylib (no such file)'
    exit 1
    ;;
  generic_library_not_loaded_text)
    printf '%s\\n' 'launcher error: library not loaded: @rpath/libexample_engine.dylib'
    exit 1
    ;;
  assertion_failure)
    printf '%s\\n' \"Test Case '-[CatalogToolbarSmokeTests testMovies]' failed (0.1 seconds)\"
    printf '%s\\n' 'XCTAssertEqual failed: expected value differs'
    exit 1
    ;;
  assertion_with_launch_warning)
    printf '%s\n' 'DebuggerLLDB.DebuggerVersionStore.StoreError error 0'
    printf '%s\n' "Test Case '-[CatalogToolbarSmokeTests testMovies]' started."
    printf '%s\n' 'CatalogToolbarSmokeTests.swift:41: error: XCTAssertTrue failed - canonical shared catalog screen did not mount'
    printf '%s\n' "Test Case '-[CatalogToolbarSmokeTests testMovies]' failed (58.2 seconds)."
    exit 1
    ;;
  product_crash_with_launch_warning)
    printf '%s\n' 'DebuggerLLDB.DebuggerVersionStore.StoreError error 0'
    printf '%s\n' "Test Case '-[CatalogToolbarSmokeTests testMovies]' started."
    printf '%s\n' 'Fatal error: unexpectedly found nil while unwrapping an Optional value'
    exit 1
    ;;
  build_failure)
    printf '%s\\n' 'error: package resolution failed before XCTest launch'
    exit 1
    ;;
  *) exit 2 ;;
esac
exit 0
""",
                encoding="utf-8",
            )
            xcodebuild.chmod(0o755)

            result = subprocess.run(
                ["bash", "-c", self.command],
                cwd=root,
                env={
                    **os.environ,
                    "PATH": f"{fake_bin}:{os.environ.get('PATH', '')}",
                    "CI_LOG": str(ci_log),
                    "RUNNER_TEMP": str(runner_temp),
                    "SIM_STATE_DIR": str(state),
                    "XCODEBUILD_ARGS_LOG": str(args_log),
                    "XCODEBUILD_SCENARIO": scenario,
                    "EXECUTION_LANE": "ios-targeted-tests",
                    "TEST_PROFILE": "targeted-tests",
                    "TEST_SELECTORS": json.dumps(
                        [
                            "streamscapetvUITests/CatalogToolbarSmokeTests/testMoviesExposeIndependentFilterAndSearchToolbarActions",
                            "streamscapetvUITests/CatalogToolbarSmokeTests/testSeriesExposeIndependentFilterAndSearchToolbarActions",
                        ]
                    ),
                    "NATIVE_COMPONENT": "",
                    "SOURCE_REPOSITORY": "StreamScapeTV/iptv-apple",
                    "OBSERVED_SOURCE_SHA": "0" * 40,
                    "APPLE_DEFAULT_CACHE_ENABLED": "false",
                    "GITHUB_RUN_ID": "888",
                    "GITHUB_RUN_ATTEMPT": "1",
                },
                text=True,
                capture_output=True,
                check=False,
            )
            count_file = state / "xcodebuild-count"
            count = int(count_file.read_text()) if count_file.exists() else 0
            args = args_log.read_text(encoding="utf-8").splitlines() if args_log.exists() else []
            return result, ci_log.read_text(encoding="utf-8", errors="replace"), count, args

    def test_infrastructure_failure_retries_once_on_a_fresh_simulator(self) -> None:
        result, private_log, count, args = self._run("infra_then_success")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(count, 2)
        self.assertIn("ios-targeted-tests attempt=1", private_log)
        self.assertIn("ios-targeted-tests attempt=2", private_log)
        self.assertIn("NSMachErrorDomain Code=-308", private_log)
        self.assertEqual(len(args), 2)
        self.assertIn("id=00000000-0000-0000-0000-000000000001", args[0])
        self.assertIn("id=00000000-0000-0000-0000-000000000002", args[1])
        for invocation in args:
            self.assertIn("-parallel-testing-enabled NO", invocation)
            self.assertIn("-maximum-parallel-testing-workers 1", invocation)
            self.assertIn("CatalogToolbarSmokeTests/testMoviesExposeIndependentFilterAndSearchToolbarActions", invocation)
            self.assertIn("CatalogToolbarSmokeTests/testSeriesExposeIndependentFilterAndSearchToolbarActions", invocation)

    def test_two_infrastructure_failures_stop_after_second_attempt_with_diagnostics(self) -> None:
        result, private_log, count, _ = self._run("infra_twice")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(count, 2)
        self.assertEqual(private_log.count("===== ios-targeted-tests attempt="), 2)
        self.assertEqual(private_log.count("===== ios-targeted-simulator-diagnostics attempt="), 2)

    def test_pre_xctest_bootstrap_abort_retries_once_and_can_recover(self) -> None:
        result, private_log, count, _ = self._run("pre_xctest_bootstrap_then_success")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(count, 2)
        self.assertIn("Pre-XCTest test-host bootstrap infrastructure failure detected.", private_log)
        self.assertIn("ios-targeted-tests attempt=2", private_log)

    def test_two_pre_xctest_bootstrap_aborts_stop_after_second_attempt(self) -> None:
        result, private_log, count, _ = self._run("pre_xctest_bootstrap_twice")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(count, 2)
        self.assertEqual(private_log.count("===== ios-targeted-tests attempt="), 2)
        self.assertEqual(private_log.count("===== ios-targeted-simulator-diagnostics attempt="), 2)

    def test_generic_sigabrt_without_bootstrap_evidence_is_never_retried(self) -> None:
        result, private_log, count, _ = self._run("generic_sigabrt")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(count, 1)
        self.assertIn("Product XCTest failure evidence detected; simulator retry suppressed.", private_log)
        self.assertNotIn("ios-targeted-tests attempt=2", private_log)

    def test_post_connection_crash_is_never_retried(self) -> None:
        result, private_log, count, _ = self._run("post_connection_crash")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(count, 1)
        self.assertIn("Product XCTest failure evidence detected; simulator retry suppressed.", private_log)
        self.assertNotIn("ios-targeted-tests attempt=2", private_log)

    def test_bootstrap_text_with_executed_crash_but_no_started_line_is_never_retried(self) -> None:
        result, private_log, count, _ = self._run("bootstrap_text_with_crashed_case_no_started")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(count, 1)
        self.assertIn("Product XCTest failure evidence detected; simulator retry suppressed.", private_log)
        self.assertNotIn("Pre-XCTest test-host bootstrap infrastructure failure detected.", private_log)
        self.assertNotIn("ios-targeted-tests attempt=2", private_log)

    def test_bootstrap_text_with_other_exc_failure_is_never_retried(self) -> None:
        result, private_log, count, _ = self._run("bootstrap_text_with_other_exc_failure")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(count, 1)
        self.assertIn("Product XCTest failure evidence detected; simulator retry suppressed.", private_log)
        self.assertNotIn("Pre-XCTest test-host bootstrap infrastructure failure detected.", private_log)
        self.assertNotIn("ios-targeted-tests attempt=2", private_log)

    def test_bootstrap_dyld_missing_dependency_is_never_retried(self) -> None:
        result, private_log, count, _ = self._run("bootstrap_text_with_dyld_missing_dependency")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(count, 1)
        self.assertIn(
            "Deterministic product dependency loader failure evidence detected; simulator retry suppressed.",
            private_log,
        )
        self.assertNotIn("Pre-XCTest test-host bootstrap infrastructure failure detected.", private_log)
        self.assertNotIn("ios-targeted-tests attempt=2", private_log)

    def test_dyld_missing_dependency_without_bootstrap_is_never_retried(self) -> None:
        result, private_log, count, _ = self._run("dyld_missing_dependency")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(count, 1)
        self.assertIn(
            "Deterministic product dependency loader failure evidence detected; simulator retry suppressed.",
            private_log,
        )
        self.assertNotIn("ios-targeted-tests attempt=2", private_log)

    def test_generic_library_not_loaded_text_does_not_match_dyld_classification(self) -> None:
        result, private_log, count, _ = self._run("generic_library_not_loaded_text")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(count, 1)
        self.assertNotIn("Deterministic product dependency loader failure evidence detected", private_log)
        self.assertNotIn("ios-targeted-tests attempt=2", private_log)

    def test_assertion_failure_is_never_retried(self) -> None:
        result, private_log, count, _ = self._run("assertion_failure")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(count, 1)
        self.assertNotIn("ios-targeted-tests attempt=2", private_log)

    def test_assertion_with_incidental_launch_warning_is_never_retried(self) -> None:
        result, private_log, count, _ = self._run("assertion_with_launch_warning")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(count, 1)
        self.assertIn("Product XCTest failure evidence detected; simulator retry suppressed.", private_log)
        self.assertNotIn("ios-targeted-tests attempt=2", private_log)

    def test_product_crash_with_incidental_launch_warning_is_never_retried(self) -> None:
        result, private_log, count, _ = self._run("product_crash_with_launch_warning")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(count, 1)
        self.assertIn("Product XCTest failure evidence detected; simulator retry suppressed.", private_log)
        self.assertNotIn("ios-targeted-tests attempt=2", private_log)

    def test_unrelated_build_failure_is_never_retried(self) -> None:
        result, private_log, count, _ = self._run("build_failure")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(count, 1)
        self.assertNotIn("ios-targeted-tests attempt=2", private_log)


if __name__ == "__main__":
    unittest.main()
