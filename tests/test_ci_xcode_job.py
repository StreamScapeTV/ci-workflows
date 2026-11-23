from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts/ci"))
import ci_stage  # noqa: E402

OPTIONS = {"scheme": "App", "product_target": "macos"}


def repo(files: dict, executable=()) -> Path:
    directory = Path(tempfile.mkdtemp())
    subprocess.run(["git", "init", "-q", str(directory)], check=True)
    for name, content in files.items():
        path = directory / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        if name in executable:
            path.chmod(0o755)
    subprocess.run(["git", "add", "."], cwd=directory, check=True)
    return directory


def fakes(fail=()) -> tuple:
    """Fake xcodebuild, swiftlint and swift: record the calls, print a line with a secret, fail named actions."""
    directory = Path(tempfile.mkdtemp())
    calls = directory / "calls"
    for name in ("xcodebuild", "swiftlint", "swift"):
        script = directory / name
        script.write_text('#!/bin/sh\necho "%s $*" >> "%s"\necho "%s output token=supersecretvalue"\nfor f in %s; do case " $* " in *" $f "*) exit 1;; esac; done\nexit 0\n'
                          % (name, calls, name, " ".join(fail) or "none-such"))
        script.chmod(0o755)
    return str(directory), calls


def run_job(source, stages, top, bin_dir, options=OPTIONS, extra_env=None):
    base = Path(tempfile.mkdtemp())
    (base / "logs").mkdir()
    out = base / "github_output"
    env = dict(os.environ, CI_OPTIONS_JSON=json.dumps(options), CI_SECRET_X="supersecretvalue", GITHUB_OUTPUT=str(out),
               PATH=f"{bin_dir}:{os.environ['PATH']}")
    env.update(extra_env or {})
    done = subprocess.run(
        [sys.executable, str(ROOT / "scripts/ci/ci_run_xcode_job.py"), "--stages", json.dumps(stages), "--top", top,
         "--source", str(source), "--contract", str(ROOT / "contracts/repository-ci-v1.json"),
         "--work-dir", str(base / "work"), "--log-dir", str(base / "logs"), "--s3-base", "logs/app/req1/"],
        env=env, capture_output=True, text=True)
    outputs = dict(line.split("=", 1) for line in out.read_text().split()) if out.exists() else {}
    return done, outputs, base / "logs"


PROJECT = {"App.xcodeproj/project.pbxproj": "x", ".swiftlint.yml": "rules: []\n"}


class XcodeOneJobTests(unittest.TestCase):
    def test_build_for_testing_once_then_test_without_building_and_lint_alongside(self):
        bin_dir, calls = fakes()
        done, outputs, logs = run_job(repo(PROJECT), ["lint", "test", "build"], "build", bin_dir)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(outputs, {"build": "passed", "test": "passed", "lint": "passed", "request_ok": "true"})
        seen = calls.read_text().splitlines()
        xcode = [line for line in seen if line.startswith("xcodebuild")]
        self.assertEqual(len(xcode), 2)
        self.assertIn(" build-for-testing ", xcode[0] + " ")
        self.assertIn(" test-without-building ", xcode[1] + " ")
        self.assertEqual(len({line.split("-derivedDataPath ")[1].split()[0] for line in xcode}), 1)  # one shared DerivedData
        self.assertIn("swiftlint lint --strict", seen)
        self.assertNotIn("supersecretvalue", "".join((logs / f"stage-{s}.log").read_text() for s in ("lint", "test", "build")))
        self.assertIn("xcodebuild output", (logs / "stage-build.log").read_text())
        self.assertNotIn("output", done.stdout)  # status lines only

    def test_a_failed_build_fails_the_test_that_needs_it_and_lint_stays_a_warning(self):
        bin_dir, calls = fakes(fail=("build-for-testing", "--strict"))
        _d, outputs, logs = run_job(repo(PROJECT), ["lint", "test", "build"], "build", bin_dir)
        self.assertEqual((outputs["build"], outputs["test"], outputs["lint"], outputs["request_ok"]), ("failed", "failed", "failed", "false"))
        self.assertNotIn("test-without-building", calls.read_text())
        self.assertIn("the build it needs failed", (logs / "stage-test.log").read_text())
        bin_dir, _calls = fakes(fail=("--strict",))
        _d, outputs, _l = run_job(repo(PROJECT), ["lint", "test", "build"], "build", bin_dir)
        self.assertEqual((outputs["lint"], outputs["request_ok"]), ("failed", "true"))
        _d, outputs, _l = run_job(repo(PROJECT), ["lint"], "lint", bin_dir)
        self.assertEqual((outputs["lint"], outputs["request_ok"]), ("failed", "false"))

    def test_a_failing_test_fails_the_request_but_not_the_build(self):
        bin_dir, _calls = fakes(fail=("test-without-building",))
        _d, outputs, _l = run_job(repo(PROJECT), ["lint", "test", "build"], "build", bin_dir)
        self.assertEqual((outputs["build"], outputs["test"], outputs["request_ok"]), ("passed", "failed", "false"))

    def test_build_alone_is_a_plain_build_and_a_missing_swiftlint_config_skips_lint(self):
        bin_dir, calls = fakes()
        files = {"App.xcodeproj/project.pbxproj": "x"}
        _d, outputs, logs = run_job(repo(files), ["lint", "build"], "build", bin_dir)
        self.assertEqual(outputs, {"build": "passed", "lint": "skipped", "request_ok": "true"})
        self.assertIn(" build ", calls.read_text())
        self.assertNotIn("build-for-testing", calls.read_text())
        self.assertIn("skipped: no lint convention", (logs / "stage-lint.log").read_text())

    def test_simulator_tests_are_skipped_not_failed(self):
        bin_dir, _calls = fakes()
        _d, outputs, _l = run_job(repo(PROJECT), ["test", "build"], "build", bin_dir, options={"scheme": "App", "product_target": "ios"})
        self.assertEqual((outputs["build"], outputs["test"], outputs["request_ok"]), ("passed", "skipped", "true"))

    def test_a_swift_package_builds_then_tests(self):
        bin_dir, calls = fakes()
        _d, outputs, _l = run_job(repo({"Package.swift": "x"}), ["test", "build"], "build", bin_dir, options={})
        self.assertEqual((outputs["build"], outputs["test"]), ("passed", "passed"))
        self.assertEqual([line for line in calls.read_text().splitlines() if line.startswith("swift ")], ["swift build -c debug", "swift test -c debug"])

    def test_only_the_test_stage_sees_the_fixture_file(self):
        bin_dir = Path(tempfile.mkdtemp())
        probe = bin_dir / "swift"
        out = bin_dir / "seen"
        probe.write_text('#!/bin/sh\necho "$1=${CI_TEST_FIXTURE_FILE:-none}" >> "%s"\n' % out)
        probe.chmod(0o755)
        _d, _o, _l = run_job(repo({"Package.swift": "x"}), ["test", "build"], "build", str(bin_dir), options={},
                             extra_env={"CI_TEST_FIXTURE_FILE": "/tmp/fixtures.json"})
        self.assertEqual(out.read_text().splitlines(), ["build=none", "test=/tmp/fixtures.json"])

    def test_transition_scripts_run_in_sequence_and_never_stop_early(self):
        files = {**PROJECT, ".ci/build.sh": "#!/bin/sh\nexit 2\n", ".ci/test.sh": "#!/bin/sh\necho test-ran\n", ".ci/lint.sh": "#!/bin/sh\nexit 1\n"}
        source = repo(files, executable=(".ci/build.sh", ".ci/test.sh", ".ci/lint.sh"))
        bin_dir, _calls = fakes()
        _d, outputs, logs = run_job(source, ["lint", "test", "build"], "build", bin_dir)
        self.assertEqual((outputs["build"], outputs["test"], outputs["lint"], outputs["request_ok"]), ("failed", "passed", "failed", "false"))
        self.assertIn("test-ran", (logs / "stage-test.log").read_text())

    def test_chain_planning_swaps_only_the_action_word(self):
        source = repo({"App.xcodeproj/project.pbxproj": "x"})
        plan = ci_stage.xcode_chain(source, ["build", "test"], {"scheme": "test", "product_target": "macos"}, "dd")
        build, test = (command["argv"] for _stage, command in plan["chain"])
        self.assertEqual((build[build.index("-scheme") + 1], build[build.index("CODE_SIGNING_ALLOWED=NO") - 1]), ("test", "build-for-testing"))
        self.assertEqual(test[test.index("CODE_SIGNING_ALLOWED=NO") - 1], "test-without-building")
        alone = ci_stage.xcode_chain(source, ["test"], {"scheme": "App", "product_target": "macos"}, "dd")
        self.assertEqual(alone["chain"][0][1]["argv"][-3], "test")  # test alone stays a normal `test`
        self.assertIsNone(alone["lint"])

    def test_the_compilation_cache_settings_are_added_to_every_xcodebuild_when_a_directory_is_given(self):
        source = repo({"App.xcodeproj/project.pbxproj": "x"})
        options = {"scheme": "App", "product_target": "macos"}
        plan = ci_stage.xcode_chain(source, ["build", "test"], options, "dd", "/cache/cas")
        for _stage, command in plan["chain"]:
            argv = command["argv"]
            self.assertIn("COMPILATION_CACHE_ENABLE_CACHING=YES", argv)
            self.assertIn("COMPILATION_CACHE_CAS_PATH=/cache/cas", argv)
            self.assertEqual(argv[argv.index("CODE_SIGNING_ALLOWED=NO") - 1] in ("build-for-testing", "test-without-building"), True)
        plain = ci_stage.xcode_chain(source, ["build"], options, "dd")
        self.assertFalse(any(word.startswith("COMPILATION_CACHE") for word in plain["chain"][0][1]["argv"]))

    def test_bad_inputs_write_no_verdict(self):
        bin_dir, _calls = fakes()
        done, outputs, _l = run_job(repo(PROJECT), ["release"], "build", bin_dir)
        self.assertNotEqual(done.returncode, 0)
        done, outputs, _l = run_job(repo(PROJECT), ["build"], "build", bin_dir, options={})  # no scheme
        self.assertNotEqual(done.returncode, 0)
        self.assertEqual(outputs, {})


if __name__ == "__main__":
    unittest.main()
