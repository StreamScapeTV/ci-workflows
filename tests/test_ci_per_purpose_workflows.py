from __future__ import annotations

import base64
import datetime
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]


def load(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts/ci" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


validate = load("ci_validate_inputs")
lane = load("ci_lane")
cache = load("ci_cache")
scrub = load("ci_scrub_log")
auth = load("ci_registry_auth")
CONTRACT = json.loads((ROOT / "contracts/repository-ci-v1.json").read_text())
ENTRY = {
    "android": ROOT / ".github/workflows/ci-android-build.yml",
    "apple": ROOT / ".github/workflows/ci-apple-build.yml",
}
SHA_PIN = re.compile(r"^[^@\s]+@[0-9a-f]{40}( #.*)?$")


def git_repo(files: dict) -> Path:
    directory = Path(tempfile.mkdtemp())
    subprocess.run(["git", "init", "-q", str(directory)], check=True)
    for name, content in files.items():
        path = directory / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    subprocess.run(["git", "add", "."], cwd=directory, check=True)
    return directory


class InputValidationTests(unittest.TestCase):
    def check(self, profile, claim, **kwargs):
        arguments = dict(contract=CONTRACT, operation=profile, allowed_operations={"lint", "build", "test"},
                         host_os="linux", claim_inputs_raw=json.dumps(claim),
                         workflow_key="validation.repository", accepted_workflow_keys={"validation.repository"})
        arguments.update(kwargs)
        return validate.validate(**arguments)

    def test_valid_build_maps_to_fixed_entrypoint_and_normalized_inputs(self):
        result = self.check("build", {"host_os": "linux", "semantic_inputs": '{"build_configuration":"debug"}'})
        self.assertEqual(result["entrypoint"], ".ci/build.sh")
        self.assertEqual(json.loads(result["semantic_inputs_json"]),
                         {"schemaVersion": 1, "inputs": {"build_configuration": "debug"}})

    def test_workflow_key_operation_os_and_unknown_fields_fail_closed(self):
        with self.assertRaises(SystemExit):
            self.check("build", {}, workflow_key="release.repository")
        with self.assertRaises(SystemExit):
            self.check("full", {})
        with self.assertRaises(SystemExit):
            self.check("build", {"host_os": "macos"})
        with self.assertRaises(SystemExit):
            self.check("build", {"semantic_inputs": '{"command":"rm -rf /"}'})
        with self.assertRaises(SystemExit):
            self.check("test", {"semantic_inputs": '{"test_selectors":["--evil"]}'})
        with self.assertRaises(SystemExit):
            self.check("build", {"semantic_inputs": '{"build_configuration":"debug","build_configuration":"release"}'})


class LaneTests(unittest.TestCase):
    def test_lanes_follow_legacy_supersession_semantics(self):
        key, cancel = lane.lane("o/r", "feature", "false", "ci-android-build.yml", "test")
        other, _ = lane.lane("o/r", "feature", "false", "ci-android-build.yml", "full")
        self.assertTrue(cancel)
        self.assertEqual(key, other)  # ordinary validation shares one lane per repository/ref
        build, _ = lane.lane("o/r", "feature", "false", "ci-android-build.yml", "build")
        self.assertNotEqual(key, build)  # cheap lint/build has its own lane
        tag_key, tag_cancel = lane.lane("o/r", "1.0_1", "true", "ci-android-build.yml", "build")
        self.assertFalse(tag_cancel)
        legacy_prefix_free = lane.lane("o/r", "feature", "false", "x.yml", "test")[0]
        self.assertEqual(len(legacy_prefix_free), 64)
        with self.assertRaises(SystemExit):
            lane.lane("bad", "x", "false", "a.yml", "build")


class CacheIdentityTests(unittest.TestCase):
    today = datetime.date(2026, 11, 20)

    def gradle_repo(self, version="9.7.1", extra=""):
        return git_repo({
            "gradle/wrapper/gradle-wrapper.properties": f"distributionUrl=https\\://services.gradle.org/distributions/gradle-{version}-bin.zip\n",
            "gradle/libs.versions.toml": "[versions]\nkotlin='2.0'\n" + extra,
            "app/build.gradle.kts": "plugins {}\n",
            "app/src/Main.kt": "fun main() {}\n",
        })

    def identity(self, root, ref="develop", is_tag="false"):
        return cache.identity("gradle", root, "o/r", ref, is_tag, "Linux", "X64", Path("/home/gradle"), self.today)

    def test_key_has_prefix_fallbacks_and_ignores_application_source(self):
        root = self.gradle_repo()
        before = self.identity(root)
        (root / "app/src/Main.kt").write_text("fun main() { println() }\n")
        self.assertEqual(before["key"], self.identity(root)["key"])
        self.assertIn("g9.7.1", before["key"])
        self.assertTrue(before["key"].endswith("2026w47"))
        keys = before["restore_keys"].split("\n")
        self.assertEqual(keys[0], before["exact_prefix"])
        self.assertTrue(before["key"].startswith(keys[0]) and keys[0].startswith(keys[1]))
        (root / "gradle/libs.versions.toml").write_text("[versions]\nkotlin='2.1'\n")
        changed = self.identity(root)
        self.assertNotEqual(before["key"], changed["key"])
        self.assertEqual(before["restore_keys"].split("\n")[1], changed["restore_keys"].split("\n")[1])

    def test_gradle_version_change_starts_a_new_generation_and_paths_are_fixed(self):
        old, new = self.identity(self.gradle_repo("9.7.1")), self.identity(self.gradle_repo("9.8.0"))
        self.assertNotEqual(old["restore_keys"].split("\n")[1], new["restore_keys"].split("\n")[1])
        self.assertEqual(old["paths"].split("\n"), [
            "/home/gradle/caches/modules-2", "/home/gradle/caches/build-cache-1", "/home/gradle/wrapper/dists"])

    def test_only_integration_branches_may_save(self):
        root = self.gradle_repo()
        self.assertEqual(self.identity(root, "develop")["save_allowed"], "true")
        self.assertEqual(self.identity(root, "main")["save_allowed"], "true")
        self.assertEqual(self.identity(root, "issue-1-feature")["save_allowed"], "false")
        self.assertEqual(self.identity(root, "develop", "true")["save_allowed"], "false")  # a tag named like a branch
        self.assertEqual(self.identity(root, "1.0_1", "true")["save_allowed"], "false")

    def test_swiftpm_identity_matches_resolved_and_skips_projects_without_packages(self):
        self.assertEqual(cache.identity("swiftpm", git_repo({"a.swift": "x"}), "o/r", "develop", "false",
                                        "macOS", "ARM64", Path("/Users/r"), self.today), {"enabled": "false"})
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


class SecretHandlingTests(unittest.TestCase):
    def test_scrub_redacts_plain_json_url_and_base64_forms_but_not_short_values(self):
        secret = 'p@ss"word/1234'
        data = "\n".join([secret, json.dumps(secret)[1:-1], base64.b64encode(secret.encode()).decode(), "abc"]).encode()
        forms = set()
        for form in scrub.variants(secret):
            forms.add(form)
        cleaned, count = scrub.scrub(data, list(forms | scrub.variants("abc")))
        self.assertNotIn(b"word/1234", cleaned)
        self.assertNotIn(base64.b64encode(secret.encode()), cleaned)
        self.assertIn(b"abc", cleaned)
        self.assertGreaterEqual(count, 3)

    def test_registry_auth_writes_private_files_and_only_requested_kinds(self):
        with tempfile.TemporaryDirectory() as directory:
            environ = {"GITHUB_SOURCE_TOKEN": "gh-token-value", "REGISTRY_USERNAME": "bot", "REGISTRY_READ_TOKEN": "reg-token-value"}
            result = auth.build(Path(directory) / "a", github_git=False, registry_netrc=True, gradle_maven=True, environ=environ)
            home = Path(result["auth_home"])
            netrc = (home / ".netrc").read_text()
            self.assertIn("git.faruqi.dev", netrc)
            self.assertNotIn("github.com", netrc)
            self.assertEqual(oct((home / ".netrc").stat().st_mode & 0o777), "0o600")
            self.assertIn("centralRegistryReadToken", (home / ".gradle/gradle.properties").read_text())
            apple = auth.build(Path(directory) / "b", github_git=True, registry_netrc=True, gradle_maven=False, environ=environ)
            self.assertFalse((Path(apple["auth_home"]) / ".gradle").exists())
            with self.assertRaises(SystemExit):
                auth.build(Path(directory) / "c", github_git=False, registry_netrc=True, gradle_maven=False, environ={})


class WorkflowShapeTests(unittest.TestCase):
    def documents(self):
        return {name: yaml.safe_load(path.read_text()) for name, path in ENTRY.items()}

    def uses(self, document):
        for job in document["jobs"].values():
            for step in job["steps"]:
                if "uses" in step:
                    yield step["uses"]

    def test_entry_workflows_take_the_run_id_and_least_privilege(self):
        for name, document in self.documents().items():
            on = document.get("on") or document[True]
            self.assertEqual(set(on), {"workflow_dispatch"})
            inputs = on["workflow_dispatch"]["inputs"]
            self.assertEqual(set(inputs), {"ci_run_id", "host_class"})
            self.assertTrue(inputs["ci_run_id"]["required"])
            self.assertEqual(document["permissions"], {"contents": "read"})
            for job in document["jobs"].values():
                self.assertNotIn("secrets", job)
                self.assertNotIn("permissions", job)

    def test_third_party_actions_are_pinned_by_full_sha_and_local_actions_exist(self):
        paths = [*ENTRY.values(), *(ROOT / "actions").glob("ci-*/action.yml"), ROOT / "actions/setup-tailscale/action.yml"]
        for path in paths:
            document = yaml.safe_load(path.read_text())
            steps = document["runs"]["steps"] if "runs" in document else [s for j in document["jobs"].values() for s in j["steps"]]
            for step in steps:
                value = step.get("uses")
                if value is None:
                    continue
                if value.startswith("./central-ci/"):
                    self.assertTrue((ROOT / value[len("./central-ci/"):] / "action.yml").is_file(), value)
                else:
                    self.assertRegex(value, SHA_PIN, f"{path.name}: {value}")

    def test_no_secrets_inherit_and_no_forbidden_secrets_per_purpose(self):
        forbidden = {
            "android": ["DOCKERHUB", "APPLE_", "APP_STORE", "STREAMSCAPE_", "XTREAM_", "REGISTRY_WRITE", "FORGEJO_REGISTRY_TOKEN",
                        "BACKUP_S3", "PROTECTED_DEPLOYED", "CLUSTER_INSPECTOR"],
            "apple": ["DOCKERHUB", "APPLE_", "APP_STORE", "REGISTRY_WRITE", "FORGEJO_REGISTRY_TOKEN", "BACKUP_S3",
                      "PROTECTED_DEPLOYED", "CLUSTER_INSPECTOR"],
        }
        for name, path in ENTRY.items():
            text = path.read_text()
            self.assertNotIn("secrets: inherit", text)
            used = set(re.findall(r"secrets\.([A-Z0-9_]+)", text))
            for token in forbidden[name]:
                self.assertFalse([item for item in used if token in item], (name, token))
        # fixtures only inside the Apple test profile
        apple = ENTRY["apple"].read_text()
        for line in apple.splitlines():
            if "secrets.STREAMSCAPE_" in line or "secrets.XTREAM_" in line:
                self.assertTrue("test_profile == 'test'" in line or line.strip().startswith(("STREAMSCAPE_", "XTREAM_")), line)

    def test_each_job_settles_agent_state_and_only_integration_refs_save_cache(self):
        for name, document in self.documents().items():
            text = ENTRY[name].read_text()
            self.assertIn("phase: finish", text)
            self.assertIn("phase: cancel-if-active", text)
            self.assertIn("steps.cache.outputs.save_allowed == 'true'", text)
            self.assertIn("steps.execute.outcome == 'success'", text)
            self.assertEqual(document["jobs"]["settle"]["needs"], ["claim", "build"])
            self.assertIn("cancel-in-progress", document["jobs"]["build"]["concurrency"])
            self.assertTrue(document["jobs"]["build"]["concurrency"]["group"].startswith(f"ci-{name}-"))

    def test_product_process_runs_without_central_secrets_in_its_step_env(self):
        for name, document in self.documents().items():
            step = next(s for s in document["jobs"]["build"]["steps"] if s.get("id") == "execute")
            for value in step["env"].values():
                self.assertNotIn("secrets.", str(value))
                self.assertNotIn("steps.source.outputs.token", str(value))


if __name__ == "__main__":
    unittest.main()
