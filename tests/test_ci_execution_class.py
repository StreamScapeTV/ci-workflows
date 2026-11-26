"""Central execution class macos-high-capacity (AS ci-workflows#1220): resolved from the repository only.

The allow-list lives in Central (scripts/ci/ci_execution_class.py); these tests read it from there and use only
the shared non-identifying repository fixtures (example-*, other) for every other repository.
"""
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
STORE_SECRETS = ("APPLE_TEAM_ID", "APP_STORE_CONNECT_KEY_ID", "APP_STORE_CONNECT_ISSUER_ID", "APP_STORE_CONNECT_API_KEY_P8_BASE64")


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
ORG = execution.ORGANIZATION
LISTED_NAME = sorted(name for name, cls in execution.REPOSITORY_CLASS.items() if cls == "macos-high-capacity")[0]
LISTED_REPOSITORY = f"{ORG}/{LISTED_NAME}"
HOSTED_REPOSITORY = f"{ORG}/example-app"


def workflow(name: str) -> dict:
    return yaml.safe_load((WORKFLOWS / name).read_text())


class MappingTests(unittest.TestCase):
    def test_the_allow_list_maps_only_to_the_self_hosted_high_capacity_mac_and_everything_else_stays_hosted(self):
        self.assertEqual(len(execution.REPOSITORY_CLASS), 1, "a new high-capacity repository is an owner decision")
        self.assertEqual(set(execution.REPOSITORY_CLASS.values()), {"macos-high-capacity"})
        self.assertEqual(execution.resolve(LISTED_REPOSITORY), "macos-high-capacity")
        self.assertEqual(execution.RUNS_ON["macos-high-capacity"], SELF_HOSTED)
        self.assertEqual(execution.RUNS_ON["macos-hosted"], ["macos-latest"])
        for repository in (HOSTED_REPOSITORY, f"{ORG}/other", f"{ORG}/{LISTED_NAME}-fork", f"{ORG}/{LISTED_NAME.upper()}",
                           f"{ORG.lower()}/{LISTED_NAME}", f"example/{LISTED_NAME}", f"{LISTED_REPOSITORY}/x", LISTED_NAME, ""):
            with self.subTest(repository=repository):
                self.assertEqual(execution.resolve(repository), "macos-hosted")

    def test_plan_writes_only_the_class_and_check_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / "out"
            env = {**os.environ, "GITHUB_OUTPUT": str(out)}
            script = str(ROOT / "scripts/ci/ci_execution_class.py")
            subprocess.run([sys.executable, script, "plan", "--repository", LISTED_REPOSITORY],
                           check=True, capture_output=True, env=env)
            subprocess.run([sys.executable, script, "plan", "--repository", HOSTED_REPOSITORY],
                           check=True, capture_output=True, env=env)
            self.assertEqual(out.read_text(), "execution_class=macos-high-capacity\nexecution_class=macos-hosted\n")

            def check(repository, value):
                return subprocess.run([sys.executable, script, "check", "--repository", repository, "--execution-class", value],
                                      capture_output=True, text=True).returncode

            self.assertEqual(check(LISTED_REPOSITORY, "macos-high-capacity"), 0)
            self.assertEqual(check(HOSTED_REPOSITORY, "macos-hosted"), 0)
            self.assertNotEqual(check(HOSTED_REPOSITORY, "macos-high-capacity"), 0)
            self.assertNotEqual(check(LISTED_REPOSITORY, "macos-hosted"), 0)
            self.assertNotEqual(check(LISTED_REPOSITORY, "macos-latest-xl"), 0)
            self.assertNotEqual(check(LISTED_REPOSITORY, '["self-hosted"]'), 0)


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
                                      repository=HOSTED_REPOSITORY, ref="develop", is_tag="false",
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
        caller = f'test "${{GITHUB_REPOSITORY}}" = {ORG}/ci-workflows'
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
                for needle in ("ci_execution_class.py check", caller,
                               'test "${RUNNER_ENVIRONMENT}" = self-hosted', 'test "${RUNNER_OS}" = macOS'):
                    self.assertIn(needle, check["run"])
                self.assertNotIn("inputs.", check["run"], "no workflow input is interpolated into shell")
                run = next(s for s in steps if s.get("id") == "run")
                self.assertEqual(run["env"]["CENTRAL_EXECUTION_CLASS"], "${{ inputs.execution_class }}")


class SelfHostedSecretBoundaryTests(unittest.TestCase):
    def test_store_credentials_reach_only_the_hosted_class(self):
        release = workflow("ci-xcode.yml")["jobs"]["release"]
        for name in STORE_SECRETS:
            self.assertEqual(release["secrets"][name],
                             f"${{{{ needs.plan.outputs.execution_class == 'macos-hosted' && secrets.{name} || '' }}}}", name)
        text = (WORKFLOWS / "ci-xcode-release-stage.yml").read_text()
        steps = workflow("ci-xcode-release-stage.yml")["jobs"]["release"]["steps"]
        for name in STORE_SECRETS:
            self.assertEqual(text.count(f"secrets.{name} "), 1, name)
            holders = [s for s in steps if f"secrets.{name} " in str(s.get("env") or {})]
            self.assertEqual(len(holders), 1, name)
            self.assertEqual(holders[0]["env"][f"CI_RELEASE_SECRET_CI_{name}"],
                             f"${{{{ inputs.execution_class == 'macos-hosted' && secrets.{name} || '' }}}}", name)
            self.assertNotIn(name, (WORKFLOWS / "ci-xcode-stage.yml").read_text())


class PrivateNetworkStateTests(unittest.TestCase):
    def test_tailscale_changes_state_only_on_hosted_runners(self):
        # On a self-hosted runner the private network is persistent: the action only probes reachability, so a job
        # leaves no Tailscale login, route, DNS change or daemon to tear down.
        steps = yaml.safe_load((ROOT / "actions/private-git/action.yml").read_text())["runs"]["steps"]
        probe = [s for s in steps if s.get("name") == "Verify private Git service reachability"]
        self.assertEqual(len(probe), 1)
        self.assertNotIn("if", probe[0])
        for step in steps:
            if step is not probe[0]:
                self.assertIn("runner.environment == 'github-hosted'", step.get("if", ""), step.get("name"))
        for name in ("s3-log-stream", "s3-upload"):
            connects = [s for s in yaml.safe_load((ROOT / "actions" / name / "action.yml").read_text())["runs"]["steps"]
                        if "private-git" in s.get("uses", "")]
            self.assertTrue(connects, name)
            for step in connects:
                self.assertIn("runner.environment == 'github-hosted'", step["if"], name)
        self.assertIn("nothing needs tearing down", (ROOT / "actions/setup-tailscale/action.yml").read_text())


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
                self.assertIn("if test -f central-ci/scripts/ci/macos_keychain_guard.py; then", cleanup["run"])
                self.assertIn("macos_keychain_guard.py restore || cleanup_status=$?", cleanup["run"])
                self.assertIn('"${RUNNER_TEMP}/tmp"', cleanup["run"])
                self.assertIn('"${GITHUB_WORKSPACE}/source"', cleanup["run"])

    @staticmethod
    def fake_security(state, calls):
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
        return call

    def test_keychain_guard_restores_the_context_and_deletes_every_keychain_the_job_added(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            login = root / "home/Library/Keychains/login.keychain-db"
            elsewhere = root / "home/Library/Keychains/job.keychain-db"
            in_temp = root / "runner-temp/release.keychain-db"
            default_only = root / "other/default-only.keychain-db"
            for path in (login, elsewhere, in_temp, default_only):
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("x")
            state = {"search": [str(login)], "default": str(login)}
            calls = []
            call = self.fake_security(state, calls)
            record = keychain.state_path(root / "home", "runner 1/x")
            self.assertEqual(record.parent, root / "home" / keychain.STATE_DIR)
            self.assertEqual(record.name, "keychain-state-runner_1_x.json")
            keychain.snapshot(record, call=call)
            self.assertEqual(record.stat().st_mode & 0o777, 0o600)
            state["search"] = [str(in_temp), str(login), str(elsewhere)]
            state["default"] = str(default_only)
            added = keychain.restore(record, call=call)
            self.assertEqual(sorted(added), sorted([str(in_temp), str(elsewhere), str(default_only)]))
            self.assertEqual(state, {"search": [str(login)], "default": str(login)})
            for path in (in_temp, elsewhere, default_only):
                self.assertFalse(path.exists(), path)
            self.assertTrue(login.exists(), "a keychain that was there before the job is never deleted")
            self.assertNotIn(("delete-keychain", str(login)), calls)
            self.assertFalse(record.exists())
            self.assertEqual(keychain.restore(record, call=call), [], "restore without a record is a no-op")

    def test_the_next_snapshot_recovers_a_job_killed_before_its_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            login = root / "login.keychain-db"
            leaked = root / "leaked.keychain-db"
            for path in (login, leaked):
                path.write_text("x")
            state = {"search": [str(login)], "default": str(login)}
            call = self.fake_security(state, [])
            record = keychain.state_path(root / "home", "runner1")
            keychain.snapshot(record, call=call)
            state["search"] = [str(leaked), str(login)]  # the job is killed here: no restore
            state["default"] = str(leaked)
            result = keychain.snapshot(record, call=call)  # the next job on the same runner
            self.assertEqual(result["recovered"], [str(leaked)])
            self.assertFalse(leaked.exists())
            self.assertEqual(result["record"], {"search": [str(login)], "default": str(login)})
            self.assertEqual(json.loads(record.read_text()), result["record"])

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
