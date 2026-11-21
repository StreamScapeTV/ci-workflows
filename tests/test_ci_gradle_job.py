from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("ci_run_gradle_job", ROOT / "scripts/ci/ci_run_gradle_job.py")
job = importlib.util.module_from_spec(spec)
sys.path.insert(0, str(ROOT / "scripts/ci"))
spec.loader.exec_module(job)
import ci_stage  # noqa: E402

# A fake gradlew: --dry-run lists the closure of the requested tasks, a real run prints a Gradle-like
# transcript (tasks interleave with their own output) and fails the task named in FAKE_FAIL.
FAKE_GRADLEW = r"""#!/bin/sh
echo "$@" >> "$FAKE_CALLS"
case "$*" in
  *--dry-run*)
    case "$*" in
      *"check -x test"*) printf ':compile SKIPPED\n:lintDebug SKIPPED\n' ;;
      *assemble*) printf ':compile SKIPPED\n:assembleDebug SKIPPED\n' ;;
      *test*) printf ':compile SKIPPED\n:testDebugUnitTest SKIPPED\n' ;;
    esac
    exit 0 ;;
esac
echo "Starting a Gradle Daemon"
for task in :compile :assembleDebug :lintDebug :testDebugUnitTest; do
  case "$*" in
    *assemble*) : ;; esac
  line="> Task $task"
  [ "$task" = "$FAKE_FAIL" ] && line="$line FAILED"
  echo "$line"
  echo "output of $task token=$FAKE_SECRET"
done
if [ -n "$FAKE_FAIL" ]; then echo "FAILURE: Build failed with an exception."; echo "BUILD FAILED in 1s"; exit 1; fi
echo "BUILD SUCCESSFUL in 1s"
"""


def repo(files: dict, executable=()) -> Path:
    directory = Path(tempfile.mkdtemp())
    subprocess.run(["git", "init", "-q", str(directory)], check=True)
    for name, content in files.items():
        path = directory / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        if name in executable or name == "gradlew":
            path.chmod(0o755)
    subprocess.run(["git", "add", "."], cwd=directory, check=True)
    return directory


def run_job(source: Path, stages: list, top: str, fail: str = "", extra_env=None):
    base = Path(tempfile.mkdtemp())
    (base / "logs").mkdir()
    out = base / "github_output"
    env = dict(os.environ, CI_OPTIONS_JSON="{}", FAKE_CALLS=str(base / "calls"), FAKE_FAIL=fail,
               FAKE_SECRET="supersecretvalue", CI_SECRET_X="supersecretvalue", GITHUB_OUTPUT=str(out))
    env.update(extra_env or {})
    done = subprocess.run(
        [sys.executable, str(ROOT / "scripts/ci/ci_run_gradle_job.py"), "--stages", json.dumps(stages), "--top", top,
         "--source", str(source), "--contract", str(ROOT / "contracts/repository-ci-v1.json"),
         "--work-dir", str(base / "work"), "--log-dir", str(base / "logs"), "--s3-base", "logs/app/req1/"],
        env=env, capture_output=True, text=True)
    outputs = dict(line.split("=", 1) for line in out.read_text().split()) if out.exists() else {}
    calls = (base / "calls").read_text().splitlines() if (base / "calls").exists() else []
    return done, outputs, calls, base / "logs"


class GradleOneJobTests(unittest.TestCase):
    def setUp(self):
        self.source = repo({"gradlew": FAKE_GRADLEW})

    def test_one_gradle_invocation_serves_all_three_stages(self):
        done, outputs, calls, logs = run_job(self.source, ["lint", "test", "build"], "build")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(outputs, {"build": "passed", "test": "passed", "lint": "passed", "request_ok": "true"})
        real = [c for c in calls if "--dry-run" not in c]
        self.assertEqual(real, ["--no-daemon --console=plain --continue --parallel --max-workers=2 assemble check"])  # check holds test
        self.assertEqual(len([c for c in calls if "--dry-run" in c]), 3)
        self.assertIn("output of :compile", (logs / "stage-lint.log").read_text())
        self.assertIn("output of :assembleDebug", (logs / "stage-build.log").read_text())
        self.assertNotIn("assembleDebug", (logs / "stage-lint.log").read_text())  # build output is not in the lint log
        for stage in ("lint", "test", "build"):
            self.assertNotIn("supersecretvalue", (logs / f"stage-{stage}.log").read_text())
            self.assertIn("BUILD SUCCESSFUL", (logs / f"stage-{stage}.log").read_text())  # summary reaches all streams

    def test_dry_run_lines_with_a_task_prefix_or_other_statuses_still_count(self):
        for line in (":app:lintDebug SKIPPED", "> Task :app:lintDebug SKIPPED", ":app:lintDebug NO-SOURCE"):
            self.assertEqual(job.DRY_TASK.match(line).group(1), ":app:lintDebug")

    def test_the_closure_sizes_are_a_status_line_of_the_actions_log(self):
        done, _, _, _ = run_job(self.source, ["lint", "test", "build"], "build")
        self.assertIn("gradle closures build=2 test=2 lint=2", done.stdout + done.stderr)

    def test_an_android_project_runs_the_debug_variant_only(self):
        android = repo({"gradlew": FAKE_GRADLEW, "app/build.gradle.kts": 'plugins { id("com.android.application") }'})
        self.assertEqual(job.root_tasks(["lint", "test", "build"], android), ["assembleDebug", "testDebugUnitTest", "lintDebug"])
        self.assertEqual(job.root_tasks(["lint"], android), ["lintDebug"])
        done, outputs, calls, _ = run_job(android, ["lint", "test", "build"], "build")
        self.assertEqual(done.returncode, 0, done.stderr)
        real = [c for c in calls if "--dry-run" not in c]
        self.assertEqual(real, ["--no-daemon --console=plain --continue --parallel --max-workers=2 assembleDebug testDebugUnitTest lintDebug"])
        catalog = repo({"gradlew": FAKE_GRADLEW, "gradle/libs.versions.toml": 'agp = { id = "com.android.application" }'})
        self.assertTrue(ci_stage.android_project(catalog))
        self.assertFalse(ci_stage.android_project(self.source))

    def test_android_build_configuration_release_builds_release_while_lint_and_test_stay_debug(self):
        android = repo({"gradlew": FAKE_GRADLEW, "app/build.gradle.kts": 'plugins { id("com.android.application") }'})
        release = {"build_configuration": "release"}
        self.assertEqual(job.root_tasks(["lint", "test", "build"], android, release),
                         ["assembleRelease", "testDebugUnitTest", "lintDebug"])
        self.assertEqual(job.root_tasks(["build"], android, {"build_configuration": "debug"}), ["assembleDebug"])
        self.assertEqual(job.root_tasks(["build"], android, {}), ["assembleDebug"])
        done, _, calls, _ = run_job(android, ["lint", "test", "build"], "build", extra_env={"CI_OPTIONS_JSON": json.dumps(release)})
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("assembleRelease testDebugUnitTest lintDebug", [c for c in calls if "--dry-run" not in c][0])

    def test_the_actions_log_carries_status_lines_only(self):
        done, _o, _c, _l = run_job(self.source, ["lint", "build"], "build")
        self.assertNotIn("output of", done.stdout)
        self.assertNotIn("Gradle Daemon", done.stdout)
        self.assertIn("== stage build started s3=logs/app/req1/build/ ==", done.stdout.splitlines())
        self.assertIn("== stage lint finished result=ok s3=logs/app/req1/lint/ ==", done.stdout.splitlines())
        self.assertIn("== request ok lint_is_warning_only=True ==", done.stdout.splitlines())

    def test_a_lint_failure_is_a_warning_the_request_stays_green(self):
        done, outputs, _c, logs = run_job(self.source, ["lint", "test", "build"], "build", fail=":lintDebug")
        self.assertEqual(outputs, {"build": "passed", "test": "passed", "lint": "failed", "request_ok": "true"})
        self.assertIn("FAILED", (logs / "stage-lint.log").read_text())

    def test_a_test_or_build_failure_fails_the_request_and_only_that_stage(self):
        _d, outputs, _c, _l = run_job(self.source, ["lint", "test", "build"], "build", fail=":testDebugUnitTest")
        self.assertEqual((outputs["test"], outputs["build"], outputs["request_ok"]), ("failed", "passed", "false"))
        _d, outputs, _c, _l = run_job(self.source, ["lint", "test", "build"], "build", fail=":assembleDebug")
        self.assertEqual((outputs["build"], outputs["test"], outputs["request_ok"]), ("failed", "passed", "false"))

    def test_a_shared_compile_failure_fails_every_stage_that_needs_it(self):
        _d, outputs, _c, _l = run_job(self.source, ["lint", "test", "build"], "build", fail=":compile")
        self.assertEqual((outputs["lint"], outputs["test"], outputs["build"]), ("failed", "failed", "failed"))

    def test_a_lint_alone_request_fails_when_lint_fails(self):
        _d, outputs, calls, _l = run_job(self.source, ["lint"], "lint", fail=":lintDebug")
        self.assertEqual((outputs["lint"], outputs["request_ok"]), ("failed", "false"))
        self.assertIn("check -x test", [c for c in calls if "--dry-run" not in c][0])

    def test_transition_scripts_keep_working_one_after_the_other_and_never_stop_early(self):
        files = {"gradlew": "x", ".ci/build.sh": "#!/bin/sh\necho build-ran; exit 4\n", ".ci/test.sh": "#!/bin/sh\necho test-ran\n",
                 ".ci/lint.sh": "#!/bin/sh\necho lint-ran; exit 1\n"}
        source = repo(files, executable=(".ci/build.sh", ".ci/test.sh", ".ci/lint.sh"))
        done, outputs, _c, logs = run_job(source, ["lint", "test", "build"], "build")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(outputs, {"build": "failed", "test": "passed", "lint": "failed", "request_ok": "false"})
        self.assertIn("build-ran", (logs / "stage-build.log").read_text())
        self.assertIn("test-ran", (logs / "stage-test.log").read_text())  # a failed build did not stop test

    def test_pure_decisions(self):
        self.assertEqual(job.root_tasks(["build", "test"], self.source), ["assemble", "test"])
        self.assertEqual(job.root_tasks(["lint", "test", "build"], self.source), ["assemble", "check"])
        self.assertEqual(job.root_tasks(["lint"], self.source), ["check", "-x", "test"])
        self.assertTrue(job.request_ok(["lint", "build"], {"lint": "failed", "build": "passed"}, "build"))
        self.assertFalse(job.request_ok(["lint"], {"lint": "failed"}, "lint"))
        self.assertFalse(job.request_ok(["test"], {}, "test"))  # no result is never ok

    def test_bad_stage_lists_are_refused_and_a_missing_wrapper_stops_the_step(self):
        done, outputs, _c, _l = run_job(self.source, ["release"], "build")
        self.assertNotEqual(done.returncode, 0)
        self.assertEqual(outputs, {})
        done, outputs, _c, _l = run_job(repo({"README.md": "x"}), ["build"], "build")
        self.assertNotEqual(done.returncode, 0)  # no verdict is written: the job fails, never a silent pass
        self.assertEqual(outputs, {})


if __name__ == "__main__":
    unittest.main()
