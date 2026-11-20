"""Release building blocks: plan, build and publish with fake docker/helm tools."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts/ci"))
spec = importlib.util.spec_from_file_location("ci_release", ROOT / "scripts/ci/ci_release.py")
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)

DIGEST = "sha256:" + "a" * 64
DECLARATION = {"schemaVersion": 1, "artifacts": [
    {"image": "app-one", "chart": "app-one", "chart_path": "charts/app-one", "dockerfile": "Dockerfile", "context": "."},
    {"image": "app-two", "chart": "app-two", "chart_path": "charts/app-two", "dockerfile": "docker/two/Dockerfile", "context": "."},
]}

FAKE_DOCKER = f'''#!/usr/bin/env python3
import os, sys
state = os.environ["FAKE_STATE"]
args = sys.argv[1:]
open(state + "/calls", "a").write("docker " + " ".join(args) + "\\n")
if args[:2] == ["manifest", "inspect"]:
    if os.environ.get("FAKE_EXISTS") == "1":
        print("{{}}"); sys.exit(0)
    if os.environ.get("FAKE_BROKEN") == "1":
        print("denied: unauthorized", file=sys.stderr); sys.exit(1)
    print("manifest unknown", file=sys.stderr); sys.exit(1)
if args[0] == "login":
    sys.stdin.read(); print("Login Succeeded")
elif args[0] == "push":
    print("{DIGEST[:7]}: digest: {DIGEST} size: 1")
elif args[:2] == ["image", "inspect"]:
    print('["x@{DIGEST}"]' if "RepoDigests" in " ".join(args) else "sha256:image")
else:
    print("ok")
'''

FAKE_HELM = '''#!/usr/bin/env python3
import os, sys, shutil
state = os.environ["FAKE_STATE"]
args = sys.argv[1:]
open(state + "/calls", "a").write("helm " + " ".join(args) + "\\n")
def opt(name):
    return args[args.index(name) + 1]
if args[0] == "package":
    chart = os.path.basename(args[1].rstrip("/"))
    text = open(args[1] + "/Chart.yaml").read()
    assert 'version: "' + opt("--version") + '"' in text and 'appVersion: "' + opt("--app-version") + '"' in text, text
    out = opt("--destination") + "/" + chart + "-" + opt("--version") + ".tgz"
    open(out, "w").write("package " + chart)
    shutil.copy(out, state + "/" + chart + ".tgz")
elif args[0] == "template":
    print("kind: ConfigMap")
elif args[:2] == ["show", "chart"]:
    if os.environ.get("FAKE_EXISTS") == "1":
        print("name: x"); sys.exit(0)
    print("Error: not found", file=sys.stderr); sys.exit(1)
elif args[:2] == ["registry", "login"]:
    sys.stdin.read()
elif args[0] == "pull":
    chart = args[1].rsplit("/", 1)[1]
    shutil.copy(state + "/" + chart + ".tgz", opt("--destination") + "/" + chart + "-" + opt("--version") + ".tgz")
else:
    print("ok")
'''


def source_repo(version="1.2.3", declaration=DECLARATION, extra=None) -> Path:
    directory = Path(tempfile.mkdtemp())
    files = {"VERSION": version + "\n", "Dockerfile": "FROM scratch\n", "docker/two/Dockerfile": "FROM scratch\n",
             ".ci/release.json": json.dumps(declaration),
             "charts/app-one/Chart.yaml": "apiVersion: v2\nname: app-one\nversion: 0.0.0\nappVersion: \"0.0.0\"\n",
             "charts/app-two/Chart.yaml": "apiVersion: v2\nname: app-two\nversion: 0.0.0\n"}
    files.update(extra or {})
    subprocess.run(["git", "init", "-q", str(directory)], check=True)
    for name, content in files.items():
        path = directory / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    subprocess.run(["git", "add", "."], cwd=directory, check=True)
    return directory


def tools() -> tuple:
    root = Path(tempfile.mkdtemp())
    (root / "bin").mkdir()
    (root / "state").mkdir()
    for name, text in (("docker", FAKE_DOCKER), ("helm", FAKE_HELM)):
        path = root / "bin" / name
        path.write_text(text)
        path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return root / "bin", root / "state"


class PlanTests(unittest.TestCase):
    def test_version_authority(self):
        source = source_repo()
        self.assertEqual(release.read_version(source), "1.2.3")
        release.check_authority("1.2.3", "1.2.3", "true")
        release.check_authority("1.2.3", "main", "false")
        for ref, is_tag in (("1.2.4", "true"), ("develop", "false"), ("main", "true")):
            with self.assertRaises(SystemExit):
                release.check_authority("1.2.3", ref, is_tag)

    def test_version_must_be_one_tracked_semver_line(self):
        for bad in ("1.2\n", "1.2.3+build\n", "v1.2.3\n", "1.2.3\n1.2.4\n", ""):
            with self.assertRaises(SystemExit, msg=bad):
                release.read_version(source_repo(version=bad.rstrip("\n")) if bad.strip() else source_repo(version=""))
        untracked = Path(tempfile.mkdtemp())
        subprocess.run(["git", "init", "-q", str(untracked)], check=True)
        (untracked / "VERSION").write_text("1.2.3\n")
        with self.assertRaises(SystemExit):
            release.read_version(untracked)

    def test_declaration_is_validated(self):
        self.assertEqual([a["image"] for a in release.load_artifacts(source_repo())], ["app-one", "app-two"])
        bad_cases = [
            {"schemaVersion": 2, "artifacts": DECLARATION["artifacts"]},
            {"schemaVersion": 1, "artifacts": []},
            {"schemaVersion": 1, "artifacts": [{**DECLARATION["artifacts"][0], "image": "Bad Name"}]},
            {"schemaVersion": 1, "artifacts": [{**DECLARATION["artifacts"][0], "extra": "x"}]},
            {"schemaVersion": 1, "artifacts": [{**DECLARATION["artifacts"][0], "chart_path": "../x"}]},
            {"schemaVersion": 1, "artifacts": [{**DECLARATION["artifacts"][0], "chart": "other"}]},
            {"schemaVersion": 1, "artifacts": [DECLARATION["artifacts"][0]] * 2},
        ]
        for declaration in bad_cases:
            with self.assertRaises(SystemExit, msg=declaration):
                release.load_artifacts(source_repo(declaration=declaration))
        with self.assertRaises(SystemExit):  # chart dependencies are refused
            release.load_artifacts(source_repo(extra={"charts/app-one/Chart.lock": "x"}))

    def test_stamp_chart_sets_version_and_app_version(self):
        path = Path(tempfile.mkdtemp()) / "Chart.yaml"
        path.write_text("name: x\nversion: 0.0.0\n")
        release.stamp_chart(path, "2.0.0")
        self.assertEqual(path.read_text(), 'name: x\nversion: "2.0.0"\nappVersion: "2.0.0"\n')
        path.write_text("version: 1\nversion: 2\n")
        with self.assertRaises(SystemExit):
            release.stamp_chart(path, "2.0.0")

    def test_names_equal_the_previous_release_path(self):
        local, remote = release.image_refs({"image": "app-one"}, "1.2.3")
        self.assertEqual(remote, "git.faruqi.dev/mimranfaruqi/app-one:1.2.3")
        self.assertEqual(release.chart_ref({"chart": "app-one"}), "oci://git.faruqi.dev/mimranfaruqi/helm-charts/app-one")


class PipelineTests(unittest.TestCase):
    def run_release(self, command, source, work, bin_dir, state, extra_env=None, ref="1.2.3", is_tag="true"):
        env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "FAKE_STATE": str(state),
               "CI_SECRET_REGISTRY_TOKEN": "tok-secret-value", "CI_REGISTRY_USERNAME": "robot",
               "CI_REGISTRY_TOKEN": "tok-secret-value", **(extra_env or {})}
        argv = [sys.executable, str(ROOT / "scripts/ci/ci_release.py"), command, "--work-dir", str(work),
                "--log-path", str(work / "stage.log")]
        if command != "publish":
            argv += ["--source", str(source), "--ref", ref, "--is-tag", is_tag]
        return subprocess.run(argv, env=env, capture_output=True, text=True)

    def setup(self):
        bin_dir, state = tools()
        work = Path(tempfile.mkdtemp())
        (work / "stage.log").touch()
        return bin_dir, state, work

    def test_build_then_publish_in_declared_order_with_readback(self):
        bin_dir, state, work = self.setup()
        source = source_repo()
        built = self.run_release("build", source, work, bin_dir, state)
        self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
        plan = json.loads((work / "release-plan.json").read_text())
        self.assertEqual([p["chart"] for p in plan], ["app-one", "app-two"])
        self.assertEqual(plan[0]["chart_package_sha256"], hashlib.sha256(Path(plan[0]["package"]).read_bytes()).hexdigest())
        calls_after_build = (state / "calls").read_text()
        self.assertNotIn("login", calls_after_build, "the build never logs in to the registry")
        published = self.run_release("publish", source, work, bin_dir, state)
        self.assertEqual(published.returncode, 0, published.stdout + published.stderr)
        calls = (state / "calls").read_text().splitlines()
        pushes = [c for c in calls if c.startswith(("docker push", "helm push"))]
        self.assertEqual(pushes, [
            "docker push git.faruqi.dev/mimranfaruqi/app-one:1.2.3",
            f"helm push {plan[0]['package']} oci://git.faruqi.dev/mimranfaruqi/helm-charts",
            "docker push git.faruqi.dev/mimranfaruqi/app-two:1.2.3",
            f"helm push {plan[1]['package']} oci://git.faruqi.dev/mimranfaruqi/helm-charts"])
        # every immutability check precedes every push
        last_check = max(i for i, c in enumerate(calls) if c.startswith(("docker manifest inspect", "helm show chart")))
        first_push = min(i for i, c in enumerate(calls) if c.startswith(("docker push", "helm push")))
        self.assertLess(last_check, first_push)
        evidence = json.loads((work / "release-evidence.json").read_text())
        self.assertEqual(evidence[0]["image_digest"], DIGEST)
        self.assertEqual(evidence[0]["chart"], "oci://git.faruqi.dev/mimranfaruqi/helm-charts/app-one:1.2.3")
        log = (work / "stage.log").read_text()
        self.assertNotIn("tok-secret-value", log)
        self.assertIn("published and read back", log)

    def test_existing_version_is_never_overwritten(self):
        bin_dir, state, work = self.setup()
        source = source_repo()
        self.assertEqual(self.run_release("build", source, work, bin_dir, state).returncode, 0)
        result = self.run_release("publish", source, work, bin_dir, state, {"FAKE_EXISTS": "1"})
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("docker push", (state / "calls").read_text())

    def test_registry_error_other_than_not_found_stops_before_publish(self):
        bin_dir, state, work = self.setup()
        source = source_repo()
        self.assertEqual(self.run_release("build", source, work, bin_dir, state).returncode, 0)
        result = self.run_release("publish", source, work, bin_dir, state, {"FAKE_BROKEN": "1"})
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("docker push", (state / "calls").read_text())

    def test_wrong_tag_fails_before_any_build(self):
        bin_dir, state, work = self.setup()
        result = self.run_release("build", source_repo(), work, bin_dir, state, ref="9.9.9")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((state / "calls").exists())

    def test_publish_needs_credentials_and_no_secret_reaches_the_build(self):
        bin_dir, state, work = self.setup()
        source = source_repo()
        self.assertEqual(self.run_release("build", source, work, bin_dir, state).returncode, 0)
        result = self.run_release("publish", source, work, bin_dir, state, {"CI_REGISTRY_TOKEN": ""})
        self.assertNotEqual(result.returncode, 0)
        env_check = release.Runner(work / "stage.log", {"CI_SECRET_X": "1", "CI_REGISTRY_TOKEN": "t", "PATH": "/bin", "KEEP": "1"})
        self.assertEqual(env_check.env, {"PATH": "/bin", "KEEP": "1"})
        env_check.close()


if __name__ == "__main__":
    unittest.main()
