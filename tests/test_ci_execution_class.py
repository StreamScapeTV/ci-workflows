"""Central execution class macos-high-capacity (AS ci-workflows#1220): resolved from the repository only."""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts/ci"))
WORKFLOWS = ROOT / ".github/workflows"
STAGES = {"ci-xcode-stage.yml": "stages", "ci-xcode-release-stage.yml": "release"}
SELF_HOSTED = ["self-hosted", "macOS", "ARM64", "macos-latest-xl"]


def load(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts/ci" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


execution = load("ci_execution_class")
validate = load("ci_validate_inputs")
run_stage = load("ci_run_stage")
keychain = load("macos_keychain_guard")
CONTRACT = json.loads((ROOT / "contracts/repository-ci-v1.json").read_text())


def workflow(name: str) -> dict:
    return yaml.safe_load((WORKFLOWS / name).read_text())


class MappingTests(unittest.TestCase):
    def test_media_resolves_to_the_self_hosted_high_capacity_mac_and_everything_else_stays_hosted(self):
        self.assertEqual(execution.resolve("StreamScapeTV/streamscape-media"), "macos-high-capacity")
        self.assertEqual(execution.RUNS_ON["macos-high-capacity"], SELF_HOSTED)
        self.assertEqual(execution.RUNS_ON["macos-hosted"], ["macos-latest"])
        for repository in ("StreamScapeTV/iptv-apple", "StreamScapeTV/iptv-android", "StreamScapeTV/streamscape-media-fork",
                           "streamscapetv/streamscape-media", "StreamScapeTV/StreamScape-Media", "evil/streamscape-media", ""):
            with self.subTest(repository=repository):
                self.assertEqual(execution.resolve(repository), "macos-hosted")
        self.assertEqual(execution.REPOSITORY_CLASS, {"StreamScapeTV/streamscape-media": "macos-high-capacity"})

    def test_plan_writes_only_the_class_and_check_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / "out"
            env = {**os.environ, "GITHUB_OUTPUT": str(out)}
            script = str(ROOT / "scripts/ci/ci_execution_class.py")
            subprocess.run([sys.executable, script, "plan", "--repository", "StreamScapeTV/streamscape-media"],
                           check=True, capture_output=True, env=env)
            subprocess.run([sys.executable, script, "plan", "--repository", "StreamScapeTV/iptv-apple"],
                           check=True, capture_output=True, env=env)
            self.assertEqual(out.read_text(), "execution_class=macos-high-capacity\nexecution_class=macos-hosted\n")

            def check(repository, value):
                return subprocess.run([sys.executable, script, "check", "--repository", repository, "--execution-class", value],
                                      capture_output=True, text=True).returncode

            self.assertEqual(check("StreamScapeTV/streamscape-media", "macos-high-capacity"), 0)
            self.assertEqual(check("StreamScapeTV/iptv-apple", "macos-hosted"), 0)
            self.assertNotEqual(check("StreamScapeTV/iptv-apple", "macos-high-capacity"), 0)
            self.assertNotEqual(check("StreamScapeTV/streamscape-media", "macos-hosted"), 0)
            self.assertNotEqual(check("StreamScapeTV/streamscape-media", "macos-latest-xl"), 0)
            self.assertNotEqual(check("StreamScapeTV/streamscape-media", '["self-hosted"]'), 0)


class NoCallerSelectionTests(unittest.TestCase):
    def test_the_entry_workflow_has_no_class_or_runner_input_and_resolves_from_the_repository_only(self):
        entry = workflow("ci-xcode.yml")
        inputs = (entry.get("on") or entry[True])["workflow_dispatch"]["inputs"]
        self.assertEqual(set(inputs), {"request_id", "repository", "ref", "is_tag", "operation", "target", "options"})
        plan = entry["jobs"]["plan"]
        self.assertEqual(plan["runs-on"], "ubuntu-latest")
        self.assertEqual(plan["outputs"]["execution_class"], "${{ steps.execution.outputs.execution_class }}")
        step = next(s for s in plan["steps"] if s.get("id") == "execution")
        self.assertEqual(step["env"], {"REPOSITORY": "${{ inputs.repository }}"})
        self.assertEqual(step["run"].strip(), 'python3 central-ci/scripts/ci/ci_execution_class.py plan --repository "${REPOSITORY}"')
        for job in ("xcode", "release"):
            self.assertEqual(entry["jobs"][job]["with"]["execution_class"], "${{ needs.plan.outputs.execution_class }}", job)
        text = (WORKFLOWS / "ci-xcode.yml").read_text()
        self.assertNotIn("self-hosted", text)
        self.assertNotIn("macos-latest-xl", text)

    def test_request_options_cannot_carry_a_class_or_runner_label(self):
        for key in ("execution_class", "runs_on", "runs-on", "runner", "host_class", "labels"):
            for value in ("macos-high-capacity", ["self-hosted", "macos-latest-xl"]):
                with self.subTest(key=key, value=value), self.assertRaises(SystemExit):
                    validate.validate(contract=CONTRACT, ecosystem="xcode", request_id="req-0123456789",
                                      repository="StreamScapeTV/iptv-apple", ref="develop", is_tag="false",
                                      operation="build", options_raw=json.dumps({key: value}))

    def test_stage_workflows_map_only_the_reviewed_class_to_fixed_labels(self):
        for name, job_name in STAGES.items():
            with self.subTest(workflow=name):
                document = workflow(name)
                declared = (document.get("on") or document[True])["workflow_call"]["inputs"]["execution_class"]
                self.assertTrue(declared["required"])
                self.assertNotIn("default", declared)
                runs_on = document["jobs"][job_name]["runs-on"]
                match = re.fullmatch(r"\$\{\{ fromJSON\(inputs\.execution_class == 'macos-high-capacity' && '(\[[^']*\])'"
                                     r" \|\| '(\[[^']*\])'\) \}\}", runs_on)
                self.assertIsNotNone(match, runs_on)
                self.assertEqual(json.loads(match.group(1)), execution.RUNS_ON["macos-high-capacity"])
                self.assertEqual(json.loads(match.group(2)), execution.RUNS_ON["macos-hosted"])
                self.assertEqual((WORKFLOWS / name).read_text().count("macos-latest-xl"), 1, "labels live in runs-on only")

    def test_stage_jobs_recheck_the_class_the_caller_and_the_runner(self):
        for name, job_name in STAGES.items():
            with self.subTest(workflow=name):
                steps = workflow(name)["jobs"][job_name]["steps"]
                names = [s.get("name", "") for s in steps]
                check = steps[names.index("Assert Central execution class")]
                self.assertLess(names.index("Check out Central CI contract and helpers"), names.index("Assert Central execution class"))
                self.assertLess(names.index("Assert Central execution class"),
                                names.index("Set up source, private network and registry authentication"))
                self.assertEqual(check["env"]["EXECUTION_CLASS"], "${{ inputs.execution_class }}")
                self.assertEqual(check["env"]["REPOSITORY"], "${{ inputs.repository }}")
                self.assertNotIn("if", check)
                for needle in ("ci_execution_class.py check", 'test "${GITHUB_REPOSITORY}" = StreamScapeTV/ci-workflows',
                               'test "${RUNNER_ENVIRONMENT}" = self-hosted', 'test "${RUNNER_OS}" = macOS'):
                    self.assertIn(needle, check["run"])
                self.assertNotIn("inputs.", check["run"], "no workflow input is interpolated into shell")
                run = next(s for s in steps if s.get("id") == "run")
                self.assertEqual(run["env"]["CENTRAL_EXECUTION_CLASS"], "${{ inputs.execution_class }}")


class PersistentHostHygieneTests(unittest.TestCase):
    def test_stage_jobs_start_clean_use_per_run_temp_and_restore_the_keychain_always(self):
        for name, job_name in STAGES.items():
            with self.subTest(workflow=name):
                steps = workflow(name)["jobs"][job_name]["steps"]
                names = [s.get("name", "") for s in steps]
                clean = steps[names.index("Start from a clean workspace")]
                self.assertLess(names.index("Start from a clean workspace"), names.index("Check out Central CI contract and helpers"))
                self.assertIn('find "${GITHUB_WORKSPACE}" -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +', clean["run"])
                isolate = steps[names.index("Isolate per-run temporary files and record the keychain context")]
                self.assertLess(names.index("Isolate per-run temporary files and record the keychain context"),
                                names.index("Set up source, private network and registry authentication"))
                self.assertIn("printf 'TMPDIR=%s\\n' \"${RUNNER_TEMP}/tmp\" >> \"${GITHUB_ENV}\"", isolate["run"])
                self.assertIn("macos_keychain_guard.py snapshot", isolate["run"])
                cleanup = steps[-1]
                self.assertEqual(cleanup["if"], "${{ always() }}")
                self.assertIn("macos_keychain_guard.py restore || cleanup_status=$?", cleanup["run"])
                self.assertIn('"${RUNNER_TEMP}/tmp"', cleanup["run"])
                self.assertIn('"${GITHUB_WORKSPACE}/source"', cleanup["run"])

    def test_keychain_guard_restores_the_search_list_and_deletes_only_run_owned_keychains(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            temp = root / "runner-temp"
            temp.mkdir()
            login = "/Users/owner/Library/Keychains/login.keychain-db"
            owned = temp / "release.keychain-db"
            owned.write_text("x")
            foreign = "/Users/owner/Library/Keychains/other.keychain-db"
            state = {"search": [login], "default": login}
            calls = []

            def call(*args):
                calls.append(args)
                if args == ("list-keychains", "-d", "user"):
                    return "".join(f'    "{path}"\n' for path in state["search"])
                if args == ("default-keychain", "-d", "user"):
                    return f'    "{state["default"]}"\n'
                if args[:4] == ("list-keychains", "-d", "user", "-s"):
                    state["search"] = list(args[4:])
                elif args[:4] == ("default-keychain", "-d", "user", "-s"):
                    state["default"] = args[4]
                elif args[0] == "delete-keychain":
                    Path(args[1]).unlink()
                return ""

            record = temp / keychain.STATE_NAME
            keychain.snapshot(record, call=call)
            self.assertEqual(record.stat().st_mode & 0o777, 0o600)
            state["search"] = [str(owned), login, foreign]
            state["default"] = str(owned)
            left = keychain.restore(record, [str(temp)], call=call)
            self.assertEqual(left, [foreign])
            self.assertEqual(state, {"search": [login], "default": login})
            self.assertFalse(owned.exists())
            self.assertIn(("delete-keychain", str(owned)), calls)
            self.assertNotIn(("delete-keychain", foreign), calls)
            self.assertFalse(record.exists())
            self.assertEqual(keychain.restore(record, [str(temp)], call=call), [], "restore without a snapshot is a no-op")

    def test_stage_processes_see_the_central_class_only_when_it_is_a_reviewed_macos_class(self):
        with tempfile.TemporaryDirectory() as directory:
            def host_class(host_os, value):
                args = argparse.Namespace(stage="build", home="", gradle_user_home="", git_config="",
                                          host_os=host_os, work_dir=directory)
                with patch.dict(os.environ, {"CENTRAL_EXECUTION_CLASS": value}):
                    return run_stage.child_environment(args, {})["CI_HOST_CLASS"]

            self.assertEqual(host_class("macos", "macos-high-capacity"), "macos-high-capacity")
            self.assertEqual(host_class("macos", "macos-hosted"), "macos-hosted")
            self.assertEqual(host_class("macos", "macos-latest-xl"), "macos-hosted")
            self.assertEqual(host_class("linux", "macos-high-capacity"), "linux-hosted")


if __name__ == "__main__":
    unittest.main()
