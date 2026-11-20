#!/usr/bin/env python3
"""Build and publish the container images and Helm charts of one release (operation ``release``).

Building blocks used by every ecosystem workflow that publishes (``actions/ci-oci-image``,
``actions/ci-helm-chart`` and ``ci-oci-release-stage.yml``):

* ``plan``     validate the tracked declaration ``.ci/release.json``, the root ``VERSION`` and the
               release authority (tag equal to VERSION, or the main branch) and print the plan.
* ``build``    build each ``linux/amd64`` image and lint, render and package each chart with
               ``version`` and ``appVersion`` stamped from VERSION. No registry credential is used.
* ``publish``  log in, require every image/chart version to be unused (immutable), push, read the
               result back and write ``release-evidence.json``.

Names, tags and registry paths equal the previous release path, so deployments need no change:
``git.faruqi.dev/mimranfaruqi/<image>:<VERSION>`` and
``oci://git.faruqi.dev/mimranfaruqi/helm-charts/<chart>:<VERSION>``.

The registry write credential is read from ``CI_REGISTRY_USERNAME`` / ``CI_REGISTRY_TOKEN`` only in
``publish`` and is passed to ``docker login`` / ``helm registry login`` on stdin. Product code (the
image build) never receives it. Every line is scrubbed of ``CI_SECRET_*`` values before it reaches
the log, which the S3 log stream uploads while the stage runs.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ci_run_stage  # noqa: E402

REGISTRY = "git.faruqi.dev"
NAMESPACE = "mimranfaruqi"
CHART_NAMESPACE = "mimranfaruqi/helm-charts"
DECLARATION = ".ci/release.json"
MAX_ARTIFACTS = 4
SLUG = re.compile(r"[a-z0-9](?:[a-z0-9._-]{0,126}[a-z0-9])?")
SEMVER = re.compile(
    r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-((?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*)(?:\.(?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*))*))?")
NOT_FOUND = re.compile(r"manifest unknown|name unknown|not found|no such manifest|status code: 404|statuscode: 404|404", re.I)


def fail(message: str):
    raise SystemExit(message)


def tracked(source: Path, relative: str) -> bool:
    result = subprocess.run(["git", "-C", str(source), "ls-files", "--error-unmatch", "--", relative],
                            capture_output=True)
    return result.returncode == 0


def read_version(source: Path) -> str:
    path = source / "VERSION"
    if path.is_symlink() or not path.is_file() or not tracked(source, "VERSION"):
        fail("repository-root VERSION must be a tracked regular file")
    raw = path.read_bytes()
    if not raw or len(raw) > 256:
        fail("VERSION must contain one bounded SemVer value")
    try:
        text = raw.decode("ascii")
    except UnicodeDecodeError:
        fail("VERSION must be plain ASCII SemVer")
    lines = text.splitlines()
    if len(lines) != 1 or text not in {lines[0], lines[0] + "\n", lines[0] + "\r\n"}:
        fail("VERSION must contain exactly one line")
    if SEMVER.fullmatch(lines[0]) is None:
        fail("VERSION must be release SemVer without build metadata (it is also the image tag)")
    return lines[0]


def check_authority(version: str, ref: str, is_tag: str) -> None:
    if is_tag == "true":
        if ref != version:
            fail(f"release tag {ref} must equal VERSION {version}")
    elif ref != "main":
        fail("a branch release is only accepted from main")


def bounded(source: Path, relative: str, *, directory: bool) -> Path:
    source = source.resolve()
    if not relative or "\\" in relative or relative.startswith("/") or any(p in ("", "..") for p in Path(relative).parts):
        fail("a declared path is invalid")
    resolved = (source / relative).resolve()
    if resolved != source and source not in resolved.parents:
        fail("a declared path escapes the source")
    if (source / relative).is_symlink() or not (resolved.is_dir() if directory else resolved.is_file()):
        fail("a declared path has the wrong type or is a symlink")
    return resolved


def load_artifacts(source: Path) -> list:
    declaration = source / DECLARATION
    if declaration.is_symlink() or not declaration.is_file() or not tracked(source, DECLARATION):
        fail(f"release needs the tracked declaration {DECLARATION} (see docs/ci-conventions/node.md)")
    try:
        document = json.loads(declaration.read_text(encoding="utf-8"))
    except ValueError:
        fail(f"{DECLARATION} is not valid JSON")
    items = document.get("artifacts") if isinstance(document, dict) else None
    if document.get("schemaVersion") != 1 or not isinstance(items, list) or not 1 <= len(items) <= MAX_ARTIFACTS:
        fail(f"{DECLARATION} must have schemaVersion 1 and 1 to {MAX_ARTIFACTS} artifacts")
    artifacts, seen = [], set()
    for item in items:
        if not isinstance(item, dict) or set(item) != {"image", "chart", "chart_path", "dockerfile", "context"}:
            fail("each artifact needs exactly image, chart, chart_path, dockerfile and context")
        if not all(isinstance(v, str) for v in item.values()):
            fail("artifact fields must be strings")
        for name in ("image", "chart"):
            if SLUG.fullmatch(item[name]) is None:
                fail(f"artifact {name} is invalid")
        if (item["image"], item["chart"]) in seen:
            fail("duplicate artifact")
        seen.add((item["image"], item["chart"]))
        bounded(source, item["context"], directory=True)
        bounded(source, item["dockerfile"], directory=False)
        chart = bounded(source, item["chart_path"], directory=True)
        chart_yaml = chart / "Chart.yaml"
        if chart_yaml.is_symlink() or not chart_yaml.is_file():
            fail("Chart.yaml is invalid")
        match = re.search(r"^name:\s*(\S+)\s*$", chart_yaml.read_text(encoding="utf-8"), re.M)
        if match is None or match.group(1) != item["chart"]:
            fail("chart name does not match the declaration")
        if (chart / "Chart.lock").exists():
            fail("charts with Helm dependencies are not supported yet")
        artifacts.append(dict(item))
    return artifacts


def stamp_chart(chart_yaml: Path, version: str) -> None:
    lines = chart_yaml.read_text(encoding="utf-8").splitlines()
    version_rows = [i for i, line in enumerate(lines) if re.match(r"^version\s*:", line)]
    app_rows = [i for i, line in enumerate(lines) if re.match(r"^appVersion\s*:", line)]
    if len(version_rows) != 1 or len(app_rows) > 1:
        fail("Chart.yaml has ambiguous version/appVersion metadata")
    lines[version_rows[0]] = f'version: "{version}"'
    if app_rows:
        lines[app_rows[0]] = f'appVersion: "{version}"'
    else:
        lines.insert(version_rows[0] + 1, f'appVersion: "{version}"')
    chart_yaml.write_text("\n".join(lines) + "\n", encoding="utf-8")


def image_refs(artifact: dict, version: str) -> tuple:
    return (f"ciw-local/{artifact['image']}:{version}", f"{REGISTRY}/{NAMESPACE}/{artifact['image']}:{version}")


def chart_ref(artifact: dict) -> str:
    return f"oci://{REGISTRY}/{CHART_NAMESPACE}/{artifact['chart']}"


class Runner:
    """Runs one command, streaming its scrubbed output into the stage log."""

    def __init__(self, log_path: Path, base_env: dict):
        self.log = ci_run_stage.LogFile(log_path, ci_run_stage.secrets_from_environment())
        self.env = {k: v for k, v in base_env.items()
                    if not k.startswith(("CI_SECRET_", "CI_REGISTRY_", "INPUT_", "ACTIONS_RUNTIME", "ACTIONS_ID_TOKEN"))}

    def say(self, message: str) -> None:
        self.log.add(f"== {message} ==\n".encode())

    def run(self, argv: list, *, cwd=None, stdin: str | None = None, env: dict | None = None, check=True) -> tuple:
        self.log.add(("$ " + " ".join(argv) + "\n").encode())
        process = subprocess.Popen(argv, cwd=cwd, env=env or self.env, stdin=subprocess.PIPE if stdin is not None else None,
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT, bufsize=0)
        if stdin is not None:
            process.stdin.write(stdin.encode())
            process.stdin.close()
        lines = []
        for line in iter(process.stdout.readline, b""):
            self.log.add(line)
            lines.append(line.decode(errors="replace"))
        status = process.wait()
        if check and status != 0:
            self.say(f"command failed with exit {status}")
            raise SystemExit(status)
        return status, "".join(lines)

    def close(self) -> None:
        self.log.close()


def command_plan(args) -> int:
    source = Path(args.source).resolve()
    version = read_version(source)
    check_authority(version, args.ref, args.is_tag)
    artifacts = load_artifacts(source)
    print(json.dumps({"version": version, "artifacts": artifacts}, sort_keys=True))
    return 0


def command_build(args) -> int:
    source = Path(args.source).resolve()
    work = Path(args.work_dir).resolve()
    version = read_version(source)
    check_authority(version, args.ref, args.is_tag)
    artifacts = load_artifacts(source)
    packages = work / "packages"
    packages.mkdir(parents=True, exist_ok=True, mode=0o700)
    runner = Runner(Path(args.log_path), dict(os.environ))
    evidence = []
    try:
        runner.say(f"release build {version}: {len(artifacts)} artifact(s)")
        for artifact in artifacts:
            local, _remote = image_refs(artifact, version)
            runner.run(["docker", "build", "--platform", "linux/amd64", "--pull", "-t", local,
                        "-f", str(bounded(source, artifact["dockerfile"], directory=False)),
                        str(bounded(source, artifact["context"], directory=True))], cwd=source)
            runner.run(["docker", "image", "inspect", local, "--format", "{{.Id}}"], cwd=source)
            prepared = work / "charts" / artifact["chart"]
            if prepared.exists():
                fail("prepared chart directory already exists")
            prepared.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            runner.run(["cp", "-a", str(bounded(source, artifact["chart_path"], directory=True)), str(prepared)])
            stamp_chart(prepared / "Chart.yaml", version)
            runner.run(["helm", "lint", str(prepared)])
            _status, rendered = runner.run(["helm", "template", artifact["chart"], str(prepared)])
            if not rendered.strip():
                fail("Helm render is empty")
            runner.run(["helm", "package", str(prepared), "--version", version, "--app-version", version,
                        "--destination", str(packages)])
            package = packages / f"{artifact['chart']}-{version}.tgz"
            if not package.is_file():
                fail("Helm package was not produced")
            evidence.append({**artifact, "version": version, "package": str(package),
                             "chart_package_sha256": hashlib.sha256(package.read_bytes()).hexdigest()})
            runner.say(f"built {artifact['image']} and chart {artifact['chart']} at {version}")
        (work / "release-plan.json").write_text(json.dumps(evidence, sort_keys=True), encoding="utf-8")
    finally:
        runner.close()
    return 0


def inspect_unused(runner: Runner, argv: list, what: str) -> None:
    status, output = runner.run(argv, check=False)
    if status == 0:
        runner.say(f"immutable {what} already exists")
        raise SystemExit(1)
    if not NOT_FOUND.search(output):
        runner.say(f"registry inspection of {what} failed for a reason other than 'unused'")
        raise SystemExit(1)


def command_publish(args) -> int:
    work = Path(args.work_dir).resolve()
    plan = json.loads((work / "release-plan.json").read_text(encoding="utf-8"))
    username, token = os.environ.get("CI_REGISTRY_USERNAME", ""), os.environ.get("CI_REGISTRY_TOKEN", "")
    if not username or not token:
        fail("registry credentials are missing")
    runner = Runner(Path(args.log_path), dict(os.environ))
    runner.env["HELM_REGISTRY_CONFIG"] = str(work / "helm-registry.json")
    runner.env["HELM_REPOSITORY_CONFIG"] = str(work / "helm-repositories.yaml")
    runner.env["HELM_REPOSITORY_CACHE"] = str(work / "helm-cache")
    evidence = []
    try:
        runner.run(["docker", "login", REGISTRY, "-u", username, "--password-stdin"], stdin=token)
        runner.run(["helm", "registry", "login", REGISTRY, "-u", username, "--password-stdin"], stdin=token)
        for item in plan:
            local, remote = image_refs(item, item["version"])
            inspect_unused(runner, ["docker", "manifest", "inspect", remote], f"image {remote}")
            inspect_unused(runner, ["helm", "show", "chart", chart_ref(item), "--version", item["version"]],
                           f"chart {chart_ref(item)}:{item['version']}")
        for item in plan:
            local, remote = image_refs(item, item["version"])
            runner.run(["docker", "tag", local, remote])
            _status, pushed = runner.run(["docker", "push", remote])
            digest = re.search(r"digest:\s*(sha256:[0-9a-f]{64})", pushed)
            if digest is None:
                fail("docker push did not report a digest")
            runner.run(["helm", "push", item["package"], f"oci://{REGISTRY}/{CHART_NAMESPACE}"])
            runner.run(["docker", "pull", remote])
            _status, repo_digests = runner.run(["docker", "image", "inspect", remote, "--format", "{{json .RepoDigests}}"])
            if digest.group(1) not in repo_digests:
                fail("private image read-back does not match the pushed digest")
            readback = work / "readback" / item["chart"]
            readback.mkdir(parents=True, exist_ok=True, mode=0o700)
            runner.run(["helm", "pull", chart_ref(item), "--version", item["version"], "--destination", str(readback)])
            pulled = readback / f"{item['chart']}-{item['version']}.tgz"
            if not pulled.is_file() or hashlib.sha256(pulled.read_bytes()).hexdigest() != item["chart_package_sha256"]:
                fail("private chart read-back does not match the packaged chart")
            evidence.append({"image": remote, "image_digest": digest.group(1), "chart": f"{chart_ref(item)}:{item['version']}",
                             "chart_package_sha256": item["chart_package_sha256"], "version": item["version"]})
            runner.say(f"published and read back {remote} ({digest.group(1)}) and {chart_ref(item)}:{item['version']}")
        (work / "release-evidence.json").write_text(json.dumps(evidence, sort_keys=True, indent=1), encoding="utf-8")
    finally:
        runner.run(["docker", "logout", REGISTRY], check=False)
        runner.close()
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("plan", "build", "publish"):
        item = sub.add_parser(name)
        item.add_argument("--work-dir", required=name != "plan")
        item.add_argument("--log-path", required=name != "plan")
        if name != "publish":
            item.add_argument("--source", default="source")
            item.add_argument("--ref", required=True)
            item.add_argument("--is-tag", required=True, choices=("true", "false"))
    args = parser.parse_args(argv)
    return {"plan": command_plan, "build": command_build, "publish": command_publish}[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
