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
  assertion_failure)
    printf '%s\\n' \"Test Case '-[CatalogToolbarSmokeTests testMovies]' failed (0.1 seconds)\"
    printf '%s\\n' 'XCTAssertEqual failed: expected value differs'
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

    def test_assertion_failure_is_never_retried(self) -> None:
        result, private_log, count, _ = self._run("assertion_failure")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(count, 1)
        self.assertNotIn("ios-targeted-tests attempt=2", private_log)

    def test_unrelated_build_failure_is_never_retried(self) -> None:
        result, private_log, count, _ = self._run("build_failure")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(count, 1)
        self.assertNotIn("ios-targeted-tests attempt=2", private_log)


if __name__ == "__main__":
    unittest.main()
