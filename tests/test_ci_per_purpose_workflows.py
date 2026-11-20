from __future__ import annotations

import base64
import datetime
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts/ci"))


def load(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts/ci" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


validate = load("ci_validate_inputs")
cache = load("ci_cache")
scrub = load("ci_scrub_log")
auth = load("ci_registry_auth")
stage = load("ci_stage")
run_stage = load("ci_run_stage")
CONTRACT = json.loads((ROOT / "contracts/repository-ci-v1.json").read_text())
ECOSYSTEMS = ("gradle", "xcode", "python")
CACHED = ("gradle", "xcode")  # python has no cache by owner decision
SHA_PIN = re.compile(r"^[^@\s]+@[0-9a-f]{40}( #.*)?$")


def git_repo(files: dict, executable=()) -> Path:
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


class RequestValidationTests(unittest.TestCase):
    def check(self, ecosystem="gradle", **overrides):
        arguments = dict(contract=CONTRACT, ecosystem=ecosystem, request_id="req-0123456789",
                         repository="StreamScapeTV/example-app", ref="develop", is_tag="false",
                         operation="build", options_raw="{}")
        arguments.update(overrides)
        return validate.validate(**arguments)

    def test_the_top_operation_expands_to_every_operation_below_it(self):
        self.assertEqual(self.check(operation="build")["stages"], ["lint", "test", "build"])
        self.assertEqual(self.check(operation="test")["stages"], ["lint", "test"])
        self.assertEqual(self.check(operation="lint")["stages"], ["lint"])

    def test_requests_outside_the_reviewed_bound_fail_closed(self):
        bad = [
            dict(repository="evil/repo"), dict(repository="StreamScapeTV/" + "x y"), dict(request_id="x"),
            dict(ref=""), dict(ref="a\nb"), dict(is_tag="maybe"), dict(operation="release"), dict(operation="check"),
            dict(options_raw='{"command":"rm -rf /"}'), dict(options_raw='["x"]'),
            dict(options_raw='{"test_selectors":["--evil"]}', operation="test"),
            dict(options_raw='{"build_configuration":"debug","build_configuration":"release"}'),
            dict(options_raw='{"test_selectors":["a.B"]}', operation="lint"),
            dict(options_raw="x" * 5000),
        ]
        for overrides in bad:
            with self.subTest(overrides=overrides), self.assertRaises(SystemExit):
                self.check(**overrides)

    def test_options_project_onto_the_fields_each_stage_accepts(self):
        result = self.check("xcode", operation="build", options_raw=json.dumps(
            {"scheme": "App", "product_target": "macos", "build_configuration": "debug", "test_selectors": ["A.B"]}))
        build = validate.stage_inputs(CONTRACT, "build", result["options"])
        self.assertEqual(build, {"schemaVersion": 1, "inputs": {"product_target": "macos", "build_configuration": "debug"}})
        self.assertNotIn("scheme", json.dumps(validate.stage_inputs(CONTRACT, "test", result["options"])))
        with self.assertRaises(SystemExit):
            self.check("gradle", options_raw='{"scheme":"App"}')


class StageResolutionTests(unittest.TestCase):
    def test_gradle_convention_and_transition_script(self):
        plain = git_repo({"gradlew": "#!/bin/sh\n", "gradle/wrapper/gradle-wrapper.properties": "x"}, executable=("gradlew",))
        self.assertEqual(stage.resolve("gradle", "build", plain, {})["argv"], ["./gradlew", "--no-daemon", "assemble"])
        self.assertEqual(stage.resolve("gradle", "test", plain, {})["argv"][-1], "test")
        self.assertEqual(stage.resolve("gradle", "lint", plain, {})["argv"][-3:], ["check", "-x", "test"])
        scripted = git_repo({"gradlew": "x", ".ci/build.sh": "#!/bin/sh\n"}, executable=(".ci/build.sh",))
        self.assertEqual(stage.resolve("gradle", "build", scripted, {}), {"mode": "script", "argv": ["./.ci/build.sh"]})
        with self.assertRaises(SystemExit):  # non executable gradlew and no script
            stage.resolve("gradle", "build", git_repo({"gradlew": "x"}), {})
        with self.assertRaises(SystemExit):  # a tracked but non executable script is refused, not skipped
            stage.resolve("gradle", "build", git_repo({".ci/build.sh": "x"}), {})

    def test_python_conventions(self):
        lib = git_repo({"pyproject.toml": "[tool.ruff]\n[project.optional-dependencies]\ndev=['pytest']\n", "tests/test_a.py": "x"})
        for name in ("lint", "build", "test"):
            self.assertEqual(stage.resolve("python", name, lib, {})["mode"], "convention")
        self.assertIn("ruff check", stage.resolve("python", "lint", lib, {})["argv"][-1])
        self.assertIn("python -m pytest -q", stage.resolve("python", "test", lib, {})["argv"][-1])
        self.assertIn("-e '.[dev]'", stage.resolve("python", "test", lib, {})["argv"][-1])
        plain = git_repo({"requirements.txt": "x\n", "tests/test_a.py": "x"})
        self.assertIn("unittest discover", stage.resolve("python", "test", plain, {})["argv"][-1])
        self.assertIn("-r requirements.txt", stage.resolve("python", "test", plain, {})["argv"][-1])
        self.assertEqual(stage.resolve("python", "lint", plain, {})["mode"], "skip")
        self.assertEqual(stage.resolve("python", "build", plain, {})["mode"], "skip")
        self.assertEqual(stage.resolve("python", "test", git_repo({"requirements.txt": "x"}), {})["mode"], "skip")
        with self.assertRaises(SystemExit):
            stage.resolve("python", "test", git_repo({"README.md": "x"}), {})

    def test_xcode_conventions(self):
        package = git_repo({"Package.swift": "// swift-tools-version:5.9"})
        self.assertEqual(stage.resolve("xcode", "test", package, {})["argv"], ["swift", "test", "-c", "debug"])
        project = git_repo({"App.xcodeproj/project.pbxproj": "x"})
        argv = stage.resolve("xcode", "build", project, {"scheme": "App", "product_target": "ios", "build_configuration": "release"}, "/dd")["argv"]
        self.assertEqual(argv[:3], ["xcodebuild", "-project", "App.xcodeproj"])
        self.assertIn("Release", argv)
        self.assertIn("generic/platform=iOS Simulator", argv)
        self.assertIn("CODE_SIGNING_ALLOWED=NO", argv)
        for options in ({}, {"scheme": "App"}):
            with self.assertRaises(SystemExit):
                stage.resolve("xcode", "build", project, options)
        # stages without a convention are skipped cleanly, never red
        skipped = stage.resolve("xcode", "test", project, {"scheme": "App", "product_target": "ios"})
        self.assertEqual((skipped["mode"], skipped["argv"]), ("skip", []))
        self.assertIn("not standardised", skipped["reason"])
        self.assertEqual(stage.resolve("xcode", "lint", project, {})["mode"], "skip")
        self.assertEqual(stage.resolve("xcode", "lint", git_repo({".swiftlint.yml": "x"}), {})["mode"], "convention")
        both = git_repo({"A.xcodeproj/project.pbxproj": "x", "B.xcodeproj/project.pbxproj": "x"})
        with self.assertRaises(SystemExit):
            stage.resolve("xcode", "build", both, {"scheme": "A", "product_target": "macos"})


class CacheIdentityTests(unittest.TestCase):
    today = datetime.date(2026, 11, 20)

    def gradle_repo(self, version="9.7.1", extra=""):
        return git_repo({
            "gradle/wrapper/gradle-wrapper.properties": f"distributionUrl=https\\://services.gradle.org/distributions/gradle-{version}-bin.zip\n",
            "gradle/libs.versions.toml": "[versions]\nkotlin='2.0'\n" + extra,
            "app/build.gradle.kts": "plugins {}\n",
            "app/src/Main.kt": "fun main() {}\n",
        })

    def identity(self, root, ref="develop", is_tag="false", integration="develop"):
        return cache.identity("gradle", root, "o/r", ref, is_tag, "Linux", "X64", Path("/home/gradle"), self.today, integration)

    def test_key_is_narrow_has_prefix_fallbacks_and_ignores_application_source(self):
        root = self.gradle_repo()
        before = self.identity(root)
        (root / "app/src/Main.kt").write_text("fun main() { println() }\n")
        self.assertEqual(before["key"], self.identity(root)["key"])
        self.assertIn("g9.7.1", before["key"])
        self.assertTrue(before["key"].endswith("20261120"))
        keys = before["restore_keys"].split("\n")
        self.assertEqual(keys[0], before["exact_prefix"])
        self.assertTrue(before["key"].startswith(keys[0]) and keys[0].startswith(keys[1]))
        (root / "gradle/libs.versions.toml").write_text("[versions]\nkotlin='2.1'\n")
        changed = self.identity(root)
        self.assertNotEqual(before["key"], changed["key"])
        self.assertEqual(keys[1], changed["restore_keys"].split("\n")[1])
        self.assertEqual(self.identity(root)["paths"].split("\n"), [
            "/home/gradle/caches/modules-2", "/home/gradle/caches/build-cache-1", "/home/gradle/wrapper/dists"])

    def test_only_the_integration_branch_saves(self):
        root = self.gradle_repo()
        self.assertEqual(self.identity(root, "develop")["save_allowed"], "true")
        self.assertEqual(self.identity(root, "main", integration="main")["save_allowed"], "true")
        self.assertEqual(self.identity(root, "main", integration="develop")["save_allowed"], "false")
        self.assertEqual(self.identity(root, "issue-1-x")["save_allowed"], "false")
        self.assertEqual(self.identity(root, "develop", "true")["save_allowed"], "false")  # a tag named develop
        self.assertEqual(self.identity(root, "1.0_1", "true")["save_allowed"], "false")
        self.assertEqual(self.identity(root, "develop", integration="none")["save_allowed"], "false")  # undetermined: restore only
        with self.assertRaises(SystemExit):
            self.identity(root, integration="release")

    def test_swiftpm_identity(self):
        none = cache.identity("swiftpm", git_repo({"a.swift": "x"}), "o/r", "develop", "false", "macOS", "ARM64", Path("/Users/r"), self.today)
        self.assertEqual(none, {"enabled": "false"})
        root = git_repo({"App.xcworkspace/xcshareddata/swiftpm/Package.resolved": '{"pins":[1]}'})
        first = cache.identity("swiftpm", root, "o/r", "develop", "false", "macOS", "ARM64", Path("/Users/r"), self.today)
        (root / "App.xcworkspace/xcshareddata/swiftpm/Package.resolved").write_text('{"pins":[2]}')
        second = cache.identity("swiftpm", root, "o/r", "develop", "false", "macOS", "ARM64", Path("/Users/r"), self.today)
        self.assertNotEqual(first["key"], second["key"])
        self.assertEqual(first["paths"].split("\n"), [
            "/Users/r/Library/Caches/org.swift.swiftpm/artifacts", "/Users/r/Library/Caches/org.swift.swiftpm/repositories"])

    def test_guard_rejects_symlinks_credentials_and_empty_caches(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.assertFalse(cache.guard([root / "missing"], [])[0])
            (root / "ok").mkdir()
            (root / "ok/file").write_bytes(b"dependency bytes")
            self.assertTrue(cache.guard([root / "ok"], [b"supersecret"])[0])
            (root / "ok/leak").write_bytes(b"token=supersecret")
            self.assertEqual(cache.guard([root / "ok"], [b"supersecret"]), (False, "cache contains a configured credential value"))
            (root / "ok/leak").unlink()
            (root / "ok/link").symlink_to(root / "ok/file")
            self.assertEqual(cache.guard([root / "ok"], []), (False, "cache path contains a symlink"))


class SecretAndLogTests(unittest.TestCase):
    def test_scrub_redacts_forms_but_not_short_values(self):
        secret = 'p@ss"word/1234'
        data = "\n".join([secret, json.dumps(secret)[1:-1], base64.b64encode(secret.encode()).decode(), "abc"]).encode()
        forms = list(scrub.variants(secret) | scrub.variants("abc"))
        cleaned, count = scrub.scrub(data, forms)
        self.assertNotIn(b"word/1234", cleaned)
        self.assertNotIn(base64.b64encode(secret.encode()), cleaned)
        self.assertIn(b"abc", cleaned)
        self.assertGreaterEqual(count, 3)

    def test_registry_auth_writes_private_files_and_only_requested_kinds(self):
        with tempfile.TemporaryDirectory() as directory:
            environ = {"GITHUB_SOURCE_TOKEN": "gh-token-value", "REGISTRY_USERNAME": "bot", "REGISTRY_READ_TOKEN": "reg-token-value"}
            result = auth.build(Path(directory) / "a", github_git=False, registry_netrc=True, gradle_maven=True, environ=environ)
            home = Path(result["auth_home"])
            self.assertIn("git.faruqi.dev", (home / ".netrc").read_text())
            self.assertNotIn("github.com", (home / ".netrc").read_text())
            self.assertEqual(stat.S_IMODE((home / ".netrc").stat().st_mode), 0o600)
            self.assertIn("centralRegistryReadToken", (home / ".gradle/gradle.properties").read_text())
            apple = auth.build(Path(directory) / "b", github_git=True, registry_netrc=True, gradle_maven=False, environ=environ)
            self.assertFalse((Path(apple["auth_home"]) / ".gradle").exists())
            with self.assertRaises(SystemExit):
                auth.build(Path(directory) / "c", github_git=False, registry_netrc=True, gradle_maven=False, environ={})

    def test_stage_runner_writes_a_scrubbed_growing_log_and_keeps_the_exit_code(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            source = git_repo({"gradlew": "#!/bin/sh\n"}, executable=("gradlew",))
            (source / ".ci").mkdir()
            script = source / ".ci/build.sh"
            script.write_text('#!/bin/sh\necho "token=$LEAK_ME"\necho "child sees: ${CI_SECRET_TOKEN:-none}"\n'
                              'echo "op=$CI_OPERATION inputs=$CI_INPUTS_JSON"\nexit 3\n')
            script.chmod(0o755)
            subprocess.run(["git", "add", "-f", ".ci/build.sh"], cwd=source, check=True)
            environ = dict(os.environ, LEAK_ME="supersecretvalue", CI_SECRET_TOKEN="supersecretvalue",
                           CI_OPTIONS_JSON='{"build_configuration":"debug"}')
            log = base / "stage.log"
            completed = subprocess.run(
                [sys.executable, str(ROOT / "scripts/ci/ci_run_stage.py"), "--ecosystem", "gradle", "--stage", "build",
                 "--source", str(source), "--contract", str(ROOT / "contracts/repository-ci-v1.json"),
                 "--host-os", "linux", "--work-dir", str(base / "work"), "--log-path", str(log)],
                env=environ, capture_output=True, text=True)
            self.assertEqual(completed.returncode, 3, completed.stderr)
            text = log.read_text()
            self.assertNotIn("supersecretvalue", text + completed.stdout)
            self.assertIn("child sees: none", text)
            self.assertIn('inputs={"inputs":{"build_configuration":"debug"},"schemaVersion":1}', text)
            self.assertIn("exit 3", text)
            self.assertEqual(text, completed.stdout)
            self.assertEqual(stat.S_IMODE(log.stat().st_mode), 0o600)


class SkipStageTests(unittest.TestCase):
    def test_a_stage_without_a_convention_succeeds_and_says_why(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            source = git_repo({"App.xcodeproj/project.pbxproj": "x"})
            log = base / "stage.log"
            completed = subprocess.run(
                [sys.executable, str(ROOT / "scripts/ci/ci_run_stage.py"), "--ecosystem", "xcode", "--stage", "lint",
                 "--source", str(source), "--contract", str(ROOT / "contracts/repository-ci-v1.json"),
                 "--host-os", "macos", "--work-dir", str(base / "work"), "--log-path", str(log)],
                env=dict(os.environ, CI_OPTIONS_JSON="{}"), capture_output=True, text=True)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertIn("skipped", log.read_text())


class WorkflowShapeTests(unittest.TestCase):
    def entry(self, name):
        return yaml.safe_load((ROOT / f".github/workflows/ci-{name}.yml").read_text())

    def all_paths(self):
        return [*[ROOT / f".github/workflows/ci-{n}{s}.yml" for n in ECOSYSTEMS for s in ("", "-stage")],
                *(ROOT / "actions").glob("ci-*/action.yml"), ROOT / "actions/setup-tailscale/action.yml"]

    def test_entry_workflows_take_runner_inputs_and_name_the_request_in_run_name(self):
        for name in ECOSYSTEMS:
            document = self.entry(name)
            on = document.get("on") or document[True]
            self.assertEqual(set(on), {"workflow_dispatch"})
            inputs = on["workflow_dispatch"]["inputs"]
            self.assertEqual(set(inputs), {"request_id", "repository", "ref", "is_tag", "operation", "target", "options"})
            self.assertIn("inputs.request_id", document["run-name"])
            self.assertEqual(document["permissions"], {"contents": "read"})
            # safety net only: one pipeline per repository + ref, never cancels another request
            self.assertEqual(document["concurrency"], {"group": "ci-${{ inputs.repository }}-${{ inputs.ref }}", "cancel-in-progress": False})
            self.assertEqual(list(document["jobs"]), ["plan", "lint", "test", "build"])

    def test_no_automatic_triggers(self):
        for name in ECOSYSTEMS:
            entry = yaml.safe_load((ROOT / f".github/workflows/ci-{name}.yml").read_text())
            stage = yaml.safe_load((ROOT / f".github/workflows/ci-{name}-stage.yml").read_text())
            self.assertEqual(set(entry.get("on") or entry[True]), {"workflow_dispatch"})
            self.assertEqual(set(stage.get("on") or stage[True]), {"workflow_call"})

    def test_workflows_never_call_agent_state_or_drive_or_a_global_cap(self):
        for path in self.all_paths():
            text = path.read_text()
            for forbidden in ("agent-state", "AGENT_STATE", "MIRROR", "CF_ACCESS", "ci_run_id", "claim_ci_run",
                              "google-drive", "GOOGLE_DRIVE", "CI_PROGRESS", "CI_LOG_UPLOADER", "secrets: inherit", "max-parallel"):
                self.assertNotIn(forbidden, text, f"{path.name}: {forbidden}")

    def test_third_party_actions_are_pinned_by_full_sha_and_local_actions_exist(self):
        for path in self.all_paths():
            document = yaml.safe_load(path.read_text())
            steps = document["runs"]["steps"] if "runs" in document else [s for j in document["jobs"].values() for s in j.get("steps", [])]
            for step in steps:
                value = step.get("uses")
                if value is None:
                    continue
                if value.startswith("./central-ci/"):
                    # actions/s3-log-stream arrives with the S3 transport change (PR 1213); until it is merged it is the one
                    # reference allowed to be absent
                    if value != "./central-ci/actions/s3-log-stream":
                        self.assertTrue((ROOT / value[len("./central-ci/"):] / "action.yml").is_file(), value)
                else:
                    self.assertRegex(value, SHA_PIN, f"{path.name}: {value}")

    def test_secrets_are_scoped_per_purpose_and_fixtures_only_reach_the_xcode_test_stage(self):
        forbidden = ["DOCKERHUB", "APPLE_", "APP_STORE", "REGISTRY_WRITE", "FORGEJO_REGISTRY_TOKEN", "BACKUP_S3",
                     "PROTECTED_DEPLOYED", "CLUSTER_INSPECTOR"]
        for name in ECOSYSTEMS:
            for suffix in ("", "-stage"):
                text = (ROOT / f".github/workflows/ci-{name}{suffix}.yml").read_text()
                used = set(re.findall(r"secrets\.([A-Z0-9_]+)", text))
                for token in forbidden:
                    self.assertFalse([item for item in used if token in item], (name, token))
        gradle = (ROOT / ".github/workflows/ci-gradle.yml").read_text() + (ROOT / ".github/workflows/ci-gradle-stage.yml").read_text()
        self.assertNotIn("STREAMSCAPE_", gradle)
        self.assertNotIn("XTREAM_", gradle)
        xcode = self.entry("xcode")
        for job in ("lint", "test", "build"):
            passes = "STREAMSCAPE_EMAIL" in xcode["jobs"][job]["secrets"]
            self.assertEqual(passes, job == "test", job)
            for secrets in (xcode["jobs"][job]["secrets"],):
                self.assertNotIn("inherit", str(secrets))

    def test_stage_jobs_are_separate_ordered_and_reusable_files_exist(self):
        for name in ECOSYSTEMS:
            document = self.entry(name)
            self.assertEqual(document["jobs"]["test"]["needs"], ["plan", "lint"])
            self.assertEqual(document["jobs"]["build"]["needs"], ["plan", "test"])
            # every later stage runs only when the previous stage succeeded (a failed lint stops the chain)
            self.assertTrue(document["jobs"]["test"]["if"].endswith("&& needs.lint.result == 'success' }}"))
            self.assertTrue(document["jobs"]["build"]["if"].endswith("&& needs.test.result == 'success' }}"))
            self.assertNotIn("!=", document["jobs"]["build"]["if"].split("contains")[-1])
            for job in ("lint", "build", "test"):
                self.assertEqual(document["jobs"][job]["uses"], f"./.github/workflows/ci-{name}-stage.yml")
                self.assertEqual(document["jobs"][job]["with"]["stage"], job)

    def test_each_stage_streams_logs_to_s3_around_the_run_and_stops_with_always(self):
        for name in ECOSYSTEMS:
            steps = yaml.safe_load((ROOT / f".github/workflows/ci-{name}-stage.yml").read_text())["jobs"]["stage"]["steps"]
            streams = [(i, s) for i, s in enumerate(steps) if s.get("uses", "") == "./central-ci/actions/s3-log-stream"]
            self.assertEqual([s["with"]["phase"] for _, s in streams], ["start", "stop"])
            run = next(i for i, s in enumerate(steps) if s.get("id") == "run")
            self.assertLess(streams[0][0], run)
            self.assertGreater(streams[1][0], run)
            self.assertEqual(streams[1][1]["if"], "${{ always() }}")
            self.assertTrue(streams[1][1]["continue-on-error"], "an S3 outage must not red the stage")
            self.assertNotIn("env", streams[0][1], "the background uploader process gets no credentials in its environment")
            self.assertEqual(set(streams[1][1]["with"]), {"phase", "repository", "request_id", "stage"}, "stop needs no credential")
            self.assertIn("inputs.request_id", streams[0][1]["with"]["request_id"])
            self.assertIn("S3_GITHUB_TRANSPORT_ACCESS_KEY", streams[0][1]["with"]["s3_access_key"])

    def test_xcode_fixture_values_are_in_the_scrub_list_of_the_run_step(self):
        steps = yaml.safe_load((ROOT / ".github/workflows/ci-xcode-stage.yml").read_text())["jobs"]["stage"]["steps"]
        env = next(step for step in steps if step.get("id") == "run")["env"]
        for name in ("STREAMSCAPE_API_BASE", "STREAMSCAPE_EMAIL", "STREAMSCAPE_PASSWORD", "STREAMSCAPE_DEMO_EMAIL",
                     "STREAMSCAPE_DEMO_PASSWORD", "XTREAM_URL", "XTREAM_USERNAME", "XTREAM_PASSWORD"):
            self.assertEqual(env[f"CI_SECRET_FIXTURE_{name}"], f"${{{{ secrets.{name} }}}}")

    def test_entry_input_contract_with_the_runner(self):
        for name in ECOSYSTEMS:
            document = self.entry(name)
            on = (document.get("on") or document[True])["workflow_dispatch"]["inputs"]
            self.assertTrue(on["operation"]["required"])
            self.assertFalse(on["target"].get("required", False))

    def test_ci_setup_mints_an_organization_token_only_for_github_git(self):
        action = yaml.safe_load((ROOT / "actions/ci-setup/action.yml").read_text())["runs"]["steps"]
        repo = next(s for s in action if s.get("id") == "token_repo")
        org = next(s for s in action if s.get("id") == "token_org")
        self.assertIn("!= 'true'", repo["if"])
        self.assertEqual(repo["with"]["repositories"], "${{ steps.name.outputs.name }}")
        self.assertIn("== 'true'", org["if"])
        self.assertNotIn("repositories", org["with"])

    def test_cache_action_fails_closed_and_logs_into_the_stage_log(self):
        action = (ROOT / "actions/ci-cache/action.yml").read_text()
        self.assertIn('grep -q "HTTP 404"', action)
        self.assertIn("integration=none", action)
        self.assertIn('>> "${LOG_PATH}"', action)
        for name in CACHED:
            text = (ROOT / f".github/workflows/ci-{name}-stage.yml").read_text()
            self.assertLess(text.index("Prepare the stage log file"), text.index("Restore "))
        self.assertNotIn("ci-cache", (ROOT / ".github/workflows/ci-python-stage.yml").read_text())

    def test_stage_runs_without_central_secrets_in_the_product_environment_and_only_trusted_cache_saves(self):
        for name in ECOSYSTEMS:
            text = (ROOT / f".github/workflows/ci-{name}-stage.yml").read_text()
            if name in CACHED:
                self.assertIn("phase: restore", text)
                self.assertIn("phase: save", text)
            run = text.split("- name: Run stage")[1].split("- name:")[0]
            self.assertNotIn("inputs.", run.split("run: |")[1], "no workflow input is interpolated into shell")
        action = (ROOT / "actions/ci-cache/action.yml").read_text()
        self.assertIn("lookup-only: true", action)
        self.assertIn("save_allowed == 'true'", action)


if __name__ == "__main__":
    unittest.main()
