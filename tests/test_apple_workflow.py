from pathlib import Path
import unittest
import os
import subprocess
import sys
import tempfile
import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/apple.yml"
DISPATCH = ROOT / ".github/workflows/central-ci-dispatch.yml"


class AppleWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.workflow = yaml.safe_load(WORKFLOW.read_text())
        cls.dispatch = yaml.safe_load(DISPATCH.read_text())
        cls.source = WORKFLOW.read_text()
        cls.plan_steps = {s.get("name"): s for s in cls.workflow["jobs"]["plan"]["steps"] if s.get("name")}
        cls.execute_steps = {s.get("name"): s for s in cls.workflow["jobs"]["execute"]["steps"] if s.get("name")}

    def test_validation_surface_keeps_only_screenshot_and_release(self) -> None:
        inputs = self.workflow["on"]["workflow_call"]["inputs"]
        self.assertNotIn("test_platform", inputs)
        self.assertNotIn("native_component", inputs)
        plan = self.plan_steps["Resolve fixed Apple execution lanes"]["run"]
        for kept in ("screenshot-review)", "testflight)"):
            self.assertIn(kept, plan)
        for retired in (
            "candidate)", "targeted-tests", "native-component", "release-build", "swift-package",
            "simulator)", "macos-test", "ios-targeted", "tvos-targeted",
        ):
            self.assertNotIn(retired, self.source)

    def test_retired_candidate_is_rejected_before_execution(self) -> None:
        plan = self.plan_steps["Resolve fixed Apple execution lanes"]["run"]
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "outputs"
            result = subprocess.run(
                ["bash", "-c", plan], capture_output=True, text=True,
                env={**os.environ, "TEST_PROFILE": "candidate", "SOURCE_IS_TAG": "false",
                     "RELEASE_VERSION": "", "BUILD_NUMBER": "", "GITHUB_OUTPUT": str(output)},
            )
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertIn("Unsupported retained Apple test_profile", result.stderr)
            self.assertFalse(output.exists())
        self.assertNotIn("test_selectors", self.workflow["on"]["workflow_call"]["inputs"])
        commands = self.execute_steps["Run fixed Apple lane"]["run"]
        for retired in ("macos-candidate-tests", "ios-build)", "tvos-build)", "candidate_test_args"):
            self.assertNotIn(retired, commands)

    def test_screenshot_review_keeps_fixed_two_lane_evidence_path(self) -> None:
        plan = self.plan_steps["Resolve fixed Apple execution lanes"]["run"]
        self.assertIn('"lane":"screenshot-ios"', plan)
        self.assertIn('"lane":"screenshot-tvos"', plan)
        commands = self.execute_steps["Run fixed Apple lane"]
        self.assertIn("run-apple-screenshot-review.sh", commands["run"])
        for name in (
            "STREAMSCAPE_API_BASE", "STREAMSCAPE_EMAIL", "STREAMSCAPE_PASSWORD",
            "STREAMSCAPE_DEMO_EMAIL", "STREAMSCAPE_DEMO_PASSWORD",
            "XTREAM_URL", "XTREAM_USERNAME", "XTREAM_PASSWORD",
        ):
            self.assertIn("screenshot-review", commands["env"][name])
        self.assertIn("Upload screenshot-review candidate evidence by route", self.execute_steps)

    def test_helper_isolation_preserves_real_product_cleanliness(self):
        isolation = self.execute_steps['Isolate Central Apple helpers from product source']['run']
        command = self.execute_steps['Run fixed Apple lane']['run']
        guard = command[command.index('workspace_dirty="$(git status'):]
        guard = guard.split('export PYTHONDONTWRITEBYTECODE=1', 1)[0]
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            product = root / 'product'; product.mkdir()
            runner = root / 'runner'; runner.mkdir()
            subprocess.run(['git', 'init', '-q', str(product)], check=True)
            (product / 'tracked').write_text('original')
            subprocess.run(['git', 'add', '.'], cwd=product, check=True)
            subprocess.run(['git', '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.test',
                            'commit', '-qm', 'fixture'], cwd=product, check=True)
            helpers = product / 'central-ci'; helpers.mkdir()
            (helpers / 'helper').write_text('central')
            env = {**os.environ, 'GITHUB_WORKSPACE': str(product), 'RUNNER_TEMP': str(runner),
                   'GITHUB_ENV': str(root / 'env')}
            before = subprocess.run(['bash', '-c', guard], cwd=product, env=env, capture_output=True)
            self.assertNotEqual(before.returncode, 0)
            subprocess.run(['bash', '-e', '-c', isolation], cwd=product, env=env, check=True)
            clean = subprocess.run(['bash', '-c', guard], cwd=product, env=env, capture_output=True)
            self.assertEqual(clean.returncode, 0, clean.stderr)
            self.assertTrue(Path((root / 'env').read_text().strip().split('=', 1)[1], 'helper').is_file())
            for kind in ('untracked', 'modified', 'symlink'):
                with self.subTest(kind=kind):
                    dirty = product / ('tracked' if kind == 'modified' else 'dirty')
                    if kind == 'symlink':
                        dirty.symlink_to(runner)
                    else:
                        dirty.write_text('dirty')
                    result = subprocess.run(['bash', '-c', guard], cwd=product, env=env, capture_output=True)
                    self.assertNotEqual(result.returncode, 0)
                    if kind == 'modified':
                        dirty.write_text('original')
                    else:
                        dirty.unlink()

    def failure_report(self, payload: bytes | None, *, symlink: bool = False) -> str:
        run = self.execute_steps["Run fixed Apple lane"]["run"]
        parser = run.split("python3 - <<'PY_SCREENSHOT_FAILURE'\n", 1)[1].split(
            "\nPY_SCREENSHOT_FAILURE", 1
        )[0]
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "summary"
            if payload is not None:
                path.write_bytes(payload)
            if symlink:
                link = Path(td) / "link"
                link.symlink_to(path)
                path = link
            result = subprocess.run(
                [sys.executable, "-c", parser],
                env={**os.environ, "GITHUB_STEP_SUMMARY": str(path)},
                capture_output=True, text=True, check=True,
            )
            self.assertEqual(result.stderr, "")
            return result.stdout

    def test_screenshot_failure_reports_each_fixed_pair(self) -> None:
        pairs = {
            "build": ("swiftpm-binary-timeout", "swiftpm-resolution-failure",
                      "xcodebuild-timeout", "xcodebuild-failure", "missing-test-product"),
            "uitest": ("authentication-assertion", "simulator-launch-failure",
                       "xctest-timeout", "xctest-failure"),
            "capture": ("unclassified",),
        }
        for stage, categories in pairs.items():
            for category in categories:
                marker = f"SCREENSHOT_REVIEW_FAILURE: stage={stage}; class={category}\n"
                with self.subTest(stage=stage, category=category):
                    self.assertEqual(self.failure_report(marker.encode()), marker)

    def test_screenshot_failure_rejects_unsafe_or_ambiguous_summary(self) -> None:
        marker = b"SCREENSHOT_REVIEW_FAILURE: stage=build; class=xcodebuild-failure\n"
        for payload in (None, b"", marker * 2, marker.rstrip(), marker + b"private text",
                        b"prefix " + marker, marker.replace(b"build;", b"unknown;"),
                        marker.replace(b"xcodebuild-failure", b"https://private.invalid"),
                        marker.replace(b"xcodebuild-failure", b"$(env)"),
                        marker.replace(b"stage=build", b"stage=build\r"), b"\xff",
                        b"x" * 8193):
            with self.subTest(payload=repr(payload)[:80]):
                self.assertEqual(self.failure_report(payload),
                    "SCREENSHOT_REVIEW_FAILURE: stage=capture; class=unclassified\n")
        self.assertIn("class=unclassified", self.failure_report(marker, symlink=True))

    def test_failed_native_wrapper_keeps_category_without_drive_and_preserves_status(self) -> None:
        run = self.execute_steps["Run fixed Apple lane"]["run"]
        functions = run[run.index("report_screenshot_failure() {"):run.index('case "${TEST_PROFILE}" in', run.index("report_screenshot_failure() {"))]
        for platform, status in (("ios", 17), ("tvos", 17), ("ios", 0)):
            with self.subTest(platform=platform, status=status), tempfile.TemporaryDirectory() as td:
                root = Path(td)
                summary = root / "summary"
                summary.write_text("")
                wrapper = root / "wrapper.sh"
                wrapper.write_text(
                    'printf "SCREENSHOT_REVIEW_FAILURE: stage=uitest; class=authentication-assertion\\n" >> "$GITHUB_STEP_SUMMARY"\n'
                    'echo private-native-detail\n'
                    f'exit {status}\n'
                )
                script = functions + f'\nif run_screenshot_logged fixed {platform} bash "{wrapper}"; then rc=0; else rc=$?; fi\n'
                script += 'false || echo drive-unavailable >&2\necho cleanup-complete\nexit "$rc"\n'
                result = subprocess.run(["bash", "-c", script], env={**os.environ,
                    "GITHUB_STEP_SUMMARY": str(summary), "RUNNER_TEMP": td,
                    "CI_LOG": str(root / "private.log")}, capture_output=True, text=True)
                self.assertEqual(result.returncode, status, result.stderr)
                self.assertIn("cleanup-complete", result.stdout)
                self.assertIn("drive-unavailable", result.stderr)
                self.assertNotIn("private-native-detail", result.stdout + result.stderr)
                self.assertEqual("SCREENSHOT_REVIEW_FAILURE:" in result.stdout,
                                 platform == "ios" and status != 0)
                self.assertFalse(list(root.glob("*-attempt-*.log")))

    def test_second_screenshot_attempt_cannot_inherit_first_failure_category(self) -> None:
        run = self.execute_steps["Run fixed Apple lane"]["run"]
        start = run.index("report_screenshot_failure() {")
        functions = run[start:run.index('case "${TEST_PROFILE}" in', start)]
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            summary = root / "summary"
            summary.write_text("prior unrelated summary\n")
            wrapper = root / "wrapper.sh"
            wrapper.write_text(
                'if test ! -e "$RUNNER_TEMP/attempted"; then\n'
                ' touch "$RUNNER_TEMP/attempted"\n'
                ' printf "SCREENSHOT_REVIEW_FAILURE: stage=uitest; class=simulator-launch-failure\\n" >> "$GITHUB_STEP_SUMMARY"\n'
                ' echo "Failed to get background assertion for target app"\n'
                ' exit 2\nfi\nexit 19\n'
            )
            script = functions + f'\nrun_screenshot_logged fixed ios bash "{wrapper}"\n'
            result = subprocess.run(["bash", "-c", script], env={**os.environ,
                "GITHUB_STEP_SUMMARY": str(summary), "RUNNER_TEMP": td,
                "CI_LOG": str(root / "private.log"),
                "CI_APPLE_SCREENSHOT_OUTPUT_DIR": str(root / "output"),
                "CI_APPLE_SCREENSHOT_PACKAGE_PATH": str(root / "package.zip")},
                capture_output=True, text=True)
            self.assertEqual(result.returncode, 19, result.stderr)
            self.assertEqual(result.stdout,
                "SCREENSHOT_REVIEW_FAILURE: stage=capture; class=unclassified\n")
            self.assertFalse(list(root.glob("*-attempt-*.log")))

    def test_summary_offset_failure_cannot_prevent_native_execution(self) -> None:
        run = self.execute_steps["Run fixed Apple lane"]["run"]
        start = run.index("report_screenshot_failure() {")
        functions = run[start:run.index('case "${TEST_PROFILE}" in', start)]
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "summary").write_text("")
            broken_wc = root / "wc"
            broken_wc.write_text("#!/bin/sh\nexit 9\n")
            broken_wc.chmod(0o700)
            script = 'set -Eeuo pipefail\n' + functions
            script += "\nrun_screenshot_logged fixed ios bash -c 'echo native-executed >> \"$CI_LOG\"; exit 17'\n"
            result = subprocess.run(["bash", "-c", script], env={**os.environ,
                "PATH": td + os.pathsep + os.environ["PATH"],
                "GITHUB_STEP_SUMMARY": str(root / "summary"), "RUNNER_TEMP": td,
                "CI_LOG": str(root / "private.log")}, capture_output=True, text=True)
            self.assertEqual(result.returncode, 17, result.stderr)
            self.assertIn("native-executed", (root / "private.log").read_text())
            self.assertIn("class=unclassified", result.stdout)

    def test_only_pretest_simulator_failures_retry_and_never_more_than_once(self) -> None:
        run = self.execute_steps["Run fixed Apple lane"]["run"]
        start = run.index("report_screenshot_failure() {")
        functions = run[start:run.index('case "${TEST_PROFILE}" in', start)]
        warning = "DebuggerLLDB.DebuggerVersionStore.StoreError error 0."
        cases = (
            ("ios", warning, True, 2, 0),
            ("ios", warning, False, 2, 17),
            ("tvos", warning, True, 1, 17),
            ("ios", warning + "\nSCREENSHOT_REVIEW_AUTH_FAILURE:unavailable", True, 1, 17),
            ("ios", warning + "\nTest Case '-[AppTests testLogin]' started.", True, 1, 17),
            ("ios", warning + "\nTest Case '-[AppTests testLogin]' failed.", True, 1, 17),
            ("ios", warning + "\nerror: Could not resolve package dependencies", True, 1, 17),
            ("ios", warning + "\n** BUILD FAILED **", True, 1, 17),
            ("ios", "ordinary capture failure", True, 1, 17),
        )
        for platform, diagnostic, recover, attempts, status in cases:
            with self.subTest(platform=platform, diagnostic=diagnostic), tempfile.TemporaryDirectory() as td:
                root = Path(td)
                wrapper = root / "wrapper.sh"
                wrapper.write_text(
                    'echo attempt >> "$RUNNER_TEMP/attempts"\n'
                    + ('test ! -e "$RUNNER_TEMP/first" || exit 0\n' if recover else '')
                    + 'touch "$RUNNER_TEMP/first"\n'
                    + "cat <<'DIAGNOSTIC'\n" + diagnostic + "\nDIAGNOSTIC\nexit 17\n"
                )
                result = subprocess.run(
                    ["bash", "-c", functions + f'\nrun_screenshot_logged fixed {platform} bash "{wrapper}"\n'],
                    env={**os.environ, "RUNNER_TEMP": td, "CI_LOG": str(root / "private.log"),
                         "GITHUB_STEP_SUMMARY": str(root / "summary"),
                         "CI_APPLE_SCREENSHOT_OUTPUT_DIR": str(root / "output"),
                         "CI_APPLE_SCREENSHOT_PACKAGE_PATH": str(root / "package.zip")},
                    capture_output=True, text=True,
                )
                self.assertEqual(result.returncode, status, result.stderr)
                self.assertEqual(len((root / "attempts").read_text().splitlines()), attempts)
                self.assertIn(diagnostic, (root / "private.log").read_text())
                self.assertNotIn(diagnostic, result.stdout + result.stderr)
                self.assertFalse(list(root.glob("*-attempt-*.log")))

    def test_testflight_remains_separately_authorized_release(self) -> None:
        job = self.dispatch["jobs"]["apple_release"]
        self.assertIn("release.apple", job["if"])
        self.assertIn("testflight", job["if"])
        self.assertEqual(job["with"]["test_profile"], "testflight")
        self.assertFalse(job["concurrency"]["cancel-in-progress"])
        plan = self.plan_steps["Resolve fixed Apple execution lanes"]["run"]
        self.assertIn('"lane":"testflight"', plan)
        run = self.execute_steps["Run fixed Apple lane"]["run"]
        self.assertIn("run-apple-testflight.sh", run)
        self.assertIn("CI_APPLE_TESTFLIGHT_BUILD_NUMBER", run)
        self.assertIn("Clean TestFlight credential and release state", self.execute_steps)
        secrets = self.workflow["on"]["workflow_call"]["secrets"]
        self.assertIn("APPLE_DISTRIBUTION_P12_BASE64", secrets)
        self.assertIn("APPLE_DISTRIBUTION_P12_PASSWORD", secrets)
        setup = self.execute_steps["Prepare ephemeral Apple Distribution signing identity"]
        self.assertIn("inputs.test_profile == 'testflight'", setup["if"])
        self.assertIn("distribution-setup", setup["run"])
        cleanup = self.execute_steps["Restore and delete ephemeral Apple Distribution signing identity"]
        self.assertIn("always()", cleanup["if"])
        self.assertIn("distribution-cleanup", cleanup["run"])

    def test_agent_state_lifecycle_has_one_start_and_one_finish(self) -> None:
        names = [s.get("name") for s in self.workflow["jobs"]["plan"]["steps"]]
        self.assertIn("Mark Agent State run as running", names)
        finish = self.workflow["jobs"]["finish"]
        self.assertIn("inputs.ci_run_id != ''", finish["if"])
        finalizers = [s for s in finish["steps"] if isinstance(s, dict) and s.get("with", {}).get("phase") == "finish"]
        self.assertEqual(len(finalizers), 1)

    def test_dispatch_apple_validation_is_keep_list_only(self) -> None:
        request = {s.get("name"): s for s in self.dispatch["jobs"]["request"]["steps"] if s.get("name")}
        keep = request["Validate retained legacy validation profile"]["run"]
        self.assertNotIn("validation.apple/candidate", keep)
        self.assertIn("validation.apple/screenshot-review", keep)
        self.assertIn("retired Apple validation profile", keep)
        job = self.dispatch["jobs"]["apple"]
        self.assertEqual(set(job["with"]), {"repository", "ref", "source_is_tag", "test_profile", "ci_run_id"})


if __name__ == "__main__":
    unittest.main()
