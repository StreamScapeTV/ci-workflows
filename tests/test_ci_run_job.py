from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


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


def run_job(ecosystem, source, stages, top, bin_dir=None):
    base = Path(tempfile.mkdtemp())
    (base / "logs").mkdir()
    out = base / "github_output"
    env = dict(os.environ, CI_OPTIONS_JSON="{}", CI_SECRET_X="supersecretvalue", GITHUB_OUTPUT=str(out))
    if bin_dir:
        env["PATH"] = f"{bin_dir}:{env['PATH']}"
    done = subprocess.run(
        [sys.executable, str(ROOT / "scripts/ci/ci_run_job.py"), "--ecosystem", ecosystem, "--stages", json.dumps(stages),
         "--top", top, "--source", str(source), "--contract", str(ROOT / "contracts/repository-ci-v1.json"),
         "--work-dir", str(base / "work"), "--log-dir", str(base / "logs"), "--s3-base", "logs/app/req1/"],
        env=env, capture_output=True, text=True)
    outputs = dict(line.split("=", 1) for line in out.read_text().split()) if out.exists() else {}
    return done, outputs, base / "logs"


def fake_npm(calls: Path, fail=()) -> str:
    """A fake npm: records its calls, prints output carrying a secret, fails the named scripts."""
    directory = Path(tempfile.mkdtemp())
    script = directory / "npm"
    script.write_text('#!/bin/sh\necho "$@" >> "%s"\necho "npm said: $@ token=supersecretvalue"\nfor f in %s; do [ "$2" = "$f" ] || [ "$1" = "$f" ] && exit 1; done\nexit 0\n'
                      % (calls, " ".join(fail) or "none"))
    script.chmod(0o755)
    return str(directory)


PACKAGE = json.dumps({"scripts": {"lint": "x", "test": "x", "build": "x"}})


class NodeOneJobTests(unittest.TestCase):
    def test_one_install_then_the_stages_in_parallel_with_their_own_logs(self):
        calls = Path(tempfile.mkdtemp()) / "calls"
        source = repo({"package.json": PACKAGE, "package-lock.json": "{}"})
        done, outputs, logs = run_job("node", source, ["lint", "test", "build"], "build", fake_npm(calls))
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(outputs, {"build": "passed", "test": "passed", "lint": "passed", "request_ok": "true"})
        seen = calls.read_text().splitlines()
        self.assertEqual(sum(1 for line in seen if line.startswith("ci ")), 1, seen)  # one install for all stages
        self.assertEqual(sorted(line for line in seen if not line.startswith("ci ")), ["run build", "run lint", "test"])
        self.assertIn("npm said: run build", (logs / "stage-build.log").read_text())
        self.assertNotIn("run build", (logs / "stage-lint.log").read_text().replace("npm ci", ""))
        for stage in ("lint", "test", "build"):
            self.assertNotIn("supersecretvalue", (logs / f"stage-{stage}.log").read_text())
        self.assertNotIn("npm said", done.stdout)  # the Actions log carries status lines only

    def test_lint_failure_is_a_warning_and_test_or_build_failure_fails_the_request(self):
        source = repo({"package.json": PACKAGE, "package-lock.json": "{}"})
        calls = Path(tempfile.mkdtemp()) / "calls"
        _d, outputs, _l = run_job("node", source, ["lint", "test", "build"], "build", fake_npm(calls, fail=("lint",)))
        self.assertEqual((outputs["lint"], outputs["request_ok"]), ("failed", "true"))
        _d, outputs, _l = run_job("node", source, ["lint", "test", "build"], "build", fake_npm(calls, fail=("test",)))
        self.assertEqual((outputs["test"], outputs["build"], outputs["request_ok"]), ("failed", "passed", "false"))
        _d, outputs, _l = run_job("node", source, ["lint"], "lint", fake_npm(calls, fail=("lint",)))
        self.assertEqual((outputs["lint"], outputs["request_ok"]), ("failed", "false"))  # lint alone fails the request

    def test_a_failed_install_fails_every_stage_and_a_missing_script_is_skipped(self):
        calls = Path(tempfile.mkdtemp()) / "calls"
        source = repo({"package.json": PACKAGE, "package-lock.json": "{}"})
        _d, outputs, _l = run_job("node", source, ["lint", "test", "build"], "build", fake_npm(calls, fail=("ci",)))
        self.assertEqual((outputs["lint"], outputs["test"], outputs["build"], outputs["request_ok"]), ("failed",) * 3 + ("false",))
        source = repo({"package.json": json.dumps({"scripts": {"build": "x"}}), "package-lock.json": "{}"})
        _d, outputs, logs = run_job("node", source, ["lint", "test", "build"], "build", fake_npm(calls))
        self.assertEqual((outputs["lint"], outputs["test"], outputs["build"], outputs["request_ok"]), ("skipped", "skipped", "passed", "true"))
        self.assertIn("skipped: no lint convention", (logs / "stage-lint.log").read_text())

    def test_transition_scripts_run_one_after_the_other_and_never_stop_early(self):
        files = {"package.json": PACKAGE, "package-lock.json": "{}", ".ci/build.sh": "#!/bin/sh\nexit 2\n", ".ci/test.sh": "#!/bin/sh\necho test-ran\n"}
        source = repo(files, executable=(".ci/build.sh", ".ci/test.sh"))
        calls = Path(tempfile.mkdtemp()) / "calls"
        _d, outputs, logs = run_job("node", source, ["lint", "test", "build"], "build", fake_npm(calls))
        self.assertEqual((outputs["build"], outputs["test"], outputs["request_ok"]), ("failed", "passed", "false"))
        self.assertIn("test-ran", (logs / "stage-test.log").read_text())

    def test_bad_stage_lists_and_a_missing_manifest_write_no_verdict(self):
        source = repo({"package.json": PACKAGE, "package-lock.json": "{}"})
        done, outputs, _l = run_job("node", source, ["release"], "build")
        self.assertNotEqual(done.returncode, 0)
        self.assertEqual(outputs, {})
        done, outputs, _l = run_job("node", repo({"README.md": "x"}), ["build"], "build")
        self.assertNotEqual(done.returncode, 0)
        self.assertEqual(outputs, {})


class PythonOneJobTests(unittest.TestCase):
    def test_one_venv_and_install_then_ruff_and_pytest_in_parallel(self):
        source = repo({"requirements.txt": "x\n", "pytest.ini": "[pytest]\n", "ruff.toml": "line-length = 100\n", "tests/test_a.py": "def test_a():\n    assert True\n"})
        bin_dir = Path(tempfile.mkdtemp())
        python = bin_dir / "python"
        python.write_text('#!/bin/sh\necho "python $@" >> "$FAKE_PY"\nexit 0\n'); python.chmod(0o755)
        python3 = bin_dir / "python3"  # the venv creation: only the activate file matters to the convention
        python3.write_text('#!/bin/sh\nmkdir -p .ci-venv/bin && : > .ci-venv/bin/activate\nexit 0\n'); python3.chmod(0o755)
        ruff = bin_dir / "ruff"
        ruff.write_text('#!/bin/sh\necho "ruff $@" >> "$FAKE_PY"\nexit 1\n'); ruff.chmod(0o755)
        calls = Path(tempfile.mkdtemp()) / "py"
        os.environ["FAKE_PY"] = str(calls)
        try:
            done, outputs, logs = run_job("python", source, ["lint", "test"], "build", str(bin_dir))
        finally:
            del os.environ["FAKE_PY"]
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(outputs["request_ok"], "true", outputs)  # ruff failed = warning
        self.assertEqual(outputs["lint"], "failed")
        self.assertEqual(outputs["test"], "passed")
        seen = calls.read_text()
        self.assertEqual(seen.count("pip install --quiet -r requirements.txt"), 1)
        self.assertEqual(seen.count("pip install --quiet ruff"), 1)
        self.assertIn("python -m unittest discover -s tests", seen)

    def test_pure_decisions(self):
        sys.path.insert(0, str(ROOT / "scripts/ci"))
        import ci_run_job
        self.assertTrue(ci_run_job.request_ok(["lint", "build"], {"lint": "failed", "build": "passed"}, "build"))
        self.assertTrue(ci_run_job.request_ok(["test", "build"], {"test": "skipped", "build": "passed"}, "build"))
        self.assertFalse(ci_run_job.request_ok(["lint"], {"lint": "failed"}, "lint"))
        self.assertFalse(ci_run_job.request_ok(["build"], {}, "build"))


if __name__ == "__main__":
    unittest.main()
