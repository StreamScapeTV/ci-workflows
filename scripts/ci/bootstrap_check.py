#!/usr/bin/env python3
"""Validate the repository bootstrap and its bounded public-API exception."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]

SETUP_PYTHON_SHA = "5fda3b95a4ea91299a34e894583c3862153e4b97"
SETUP_PYTHON_RELEASE = "v7.0.0"
PYTHON_VERSION = "3.12.10"
PY_YAML_VERSION = "6.0.3"
PY_YAML_SOURCE_SHA256 = (
    "d76623373421df22fb4cf8817020cbb7ef15c725b9d5e45f17e189bfc384190f"
)
PY_YAML_SOURCE_FILENAME = "pyyaml-6.0.3.tar.gz"
PY_YAML_SOURCE_URL = (
    "https://files.pythonhosted.org/packages/05/8e/"
    "961c0007c59b8dd7729d542c61a4d537767a59645b82a0b521206e1e25c2/"
    "pyyaml-6.0.3.tar.gz"
)
PY_YAML_SOURCE_RUNTIMES = ["cp312-macos-arm64", "cp312-macos-x86_64"]
PY_YAML_LINUX_WHEEL = {
    "runtime": "cp312-manylinux-x86_64",
    "filename": (
        "pyyaml-6.0.3-cp312-cp312-manylinux2014_x86_64."
        "manylinux_2_17_x86_64.manylinux_2_28_x86_64.whl"
    ),
    "url": (
        "https://files.pythonhosted.org/packages/8b/9d/"
        "b3589d3877982d4f2329302ef98a8026e7f4443c765c46cfecc8858c6b4b/"
        "pyyaml-6.0.3-cp312-cp312-manylinux2014_x86_64."
        "manylinux_2_17_x86_64.manylinux_2_28_x86_64.whl"
    ),
    "sha256": "ba1cc08a7ccde2d2ec775841541641e4548226580ab850948cbfda66a1befcdc",
}

REQUIRED_PATHS = (
    "AGENTS.md",
    "CONTRIBUTING.md",
    "README.md",
    "RUNNERS.md",
    ".github/ISSUE_TEMPLATE/implementation.yml",
    ".github/pull_request_template.md",
    ".github/workflows/self-check.yml",
    "actions/README.md",
    "src/ci_workflows/__init__.py",
    "src/ci_workflows/validation_runtime.py",
    "scripts/ci/bootstrap_validation_runtime.py",
    "scripts/release/README.md",
    "contracts/action-tool-lock.json",
    "contracts/artifact-policy.json",
    "contracts/security-policy.json",
    "contracts/bootstrap-public-workflows.json",
    "docs/architecture/ADR-0001-reuse-layers.md",
    "docs/architecture/security-and-artifacts.md",
    "docs/consumers/access.md",
    "docs/validation/harness.md",
    "docs/workflows/README.md",
    "requirements/validation.lock",
    "tests/fixtures/README.md",
)

FORBIDDEN_SELF_CHECK_PATTERNS = (
    "secrets: inherit",
    "upload-artifact",
    "pull_request_target:",
    "packages: write",
    "id-token: write",
    "homelab-portable-linux-x64",
    "runs-on: macos-latest",
    "runs-on: self-hosted",
    "runs-on: portable",
    "runs-on: mobile",
    "runs-on: buildah",
    "runs-on: buildah-tiny",
    "runs-on: buildah-small",
    "runs-on: buildah-medium",
    "runs-on: buildah-high",
    "runs-on: flux-control",
    "python -m pip",
    "python3 -m pip",
    "pip install",
    "setup.py",
    "brew install",
)


def read_text(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def read_json(relative: str) -> object:
    return json.loads(read_text(relative))


def _mapping(value: object, message: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise SystemExit(message)
    return value


def validate_required_paths() -> None:
    missing = [path for path in REQUIRED_PATHS if not (ROOT / path).is_file()]
    if missing:
        raise SystemExit(f"missing bootstrap files: {', '.join(missing)}")


def public_workflow_paths() -> list[str]:
    workflows = ROOT / ".github" / "workflows"
    return sorted(
        path.relative_to(ROOT).as_posix()
        for pattern in ("reusable-*.yml", "reusable-*.yaml")
        for path in workflows.glob(pattern)
    )


def allowed_bootstrap_workflows() -> list[str]:
    contract = read_json("contracts/bootstrap-public-workflows.json")
    if not isinstance(contract, dict) or contract.get("schema_version") != 1:
        raise SystemExit("bootstrap public-workflow contract is invalid")
    allowed = contract.get("allowed")
    if not isinstance(allowed, list):
        raise SystemExit("bootstrap public-workflow allowlist is invalid")
    result: list[str] = []
    for entry in allowed:
        if not isinstance(entry, dict):
            raise SystemExit("bootstrap public-workflow entry is invalid")
        path = entry.get("path")
        issue = entry.get("issue")
        follow_up = entry.get("required_follow_up")
        if not isinstance(path, str) or not isinstance(issue, int):
            raise SystemExit("bootstrap public-workflow identity is invalid")
        if not isinstance(follow_up, list) or not all(
            isinstance(value, int) for value in follow_up
        ):
            raise SystemExit("bootstrap public-workflow follow-up is invalid")
        result.append(path)
    return sorted(result)


def validate_public_workflow_exceptions() -> None:
    actual = public_workflow_paths()
    allowed = allowed_bootstrap_workflows()
    if actual != allowed:
        raise SystemExit(
            "public reusable workflow set differs from bounded bootstrap exceptions: "
            f"actual={actual!r} allowed={allowed!r}"
        )


def _validate_emergency_exception() -> None:
    harness = _mapping(
        read_json("contracts/validation-harness.json"),
        "validation harness contract is invalid",
    )
    if harness.get("allowed_runner_profiles") != ["portable", "agent-state"]:
        raise SystemExit(
            "general validation runner profiles must remain portable and agent-state"
        )
    exceptions = harness.get("exceptions")
    if not isinstance(exceptions, list):
        raise SystemExit("validation harness exceptions must be a list")
    matches = [
        entry
        for entry in exceptions
        if isinstance(entry, dict)
        and entry.get("path") == ".github/workflows/self-check.yml"
    ]
    if len(matches) != 1:
        raise SystemExit("self-check requires exactly one emergency runner exception")
    entry = matches[0]
    if entry.get("issue") != 60 or entry.get("rules") != [
        "unknown-runner-profile"
    ]:
        raise SystemExit("self-check emergency exception scope drifted")
    reason = str(entry.get("reason", ""))
    for required in ("macOS", "#268", "remove", "portable"):
        if required not in reason:
            raise SystemExit(
                f"self-check emergency exception reason is missing {required!r}"
            )


def validate_self_check() -> None:
    source = read_text(".github/workflows/self-check.yml")
    required = (
        "runs-on: macOS",
        "timeout-minutes: 10",
        "permissions:\n  actions: read\n  contents: read",
        "Admit trusted workflow source",
        "PR_HEAD_REPOSITORY:",
        '"${PR_HEAD_REPOSITORY}" != "${GITHUB_REPOSITORY}"',
        "push|workflow_dispatch)",
        "SOURCE_SHA: ${{ github.event.pull_request.head.sha || github.sha }}",
        f"actions/setup-python@{SETUP_PYTHON_SHA} # {SETUP_PYTHON_RELEASE}",
        f'python-version: "{PYTHON_VERSION}"',
        "persist-credentials: false",
        "SOURCE_SHA:",
        'test "$(git rev-parse HEAD)" = "${SOURCE_SHA}"',
        "python3 scripts/ci/bootstrap_validation_runtime.py",
        'test -f "${validation_root}/python/yaml/__init__.py"',
        "python3 scripts/ci/bootstrap_check.py",
        "python3 -m unittest discover -s tests -p 'test_*.py' -v",
        "git diff --exit-code",
        "if: always()",
        "Confirm zero Actions artifacts",
    )
    for token in required:
        if token not in source:
            raise SystemExit(f"self-check is missing required contract: {token}")
    for token in FORBIDDEN_SELF_CHECK_PATTERNS:
        if token in source:
            raise SystemExit(f"self-check contains forbidden contract: {token}")
    runs_on = re.findall(r"^\s+runs-on:\s*([^\s#]+)\s*$", source, re.MULTILINE)
    if runs_on != ["macOS"]:
        raise SystemExit(
            "self-check must use exactly runs-on: macOS, "
            f"found {runs_on!r}"
        )
    if re.search(r"runs-on:\s*.*\$\{\{", source):
        raise SystemExit("self-check runner selector must not be dynamic")
    if re.search(r"^\s+[A-Za-z-]+:\s+write\s*$", source, re.MULTILINE):
        raise SystemExit("self-check must preserve read-only workflow permissions")

    admission = source.index("- name: Admit trusted workflow source")
    setup = source.index("- name: Set up pinned CPython 3.12")
    checkout = source.index("- name: Check out exact source")
    if not admission < setup < checkout:
        raise SystemExit(
            "same-repository admission and pinned Python setup must precede checkout"
        )
    _validate_emergency_exception()

    macos_users: list[str] = []
    for path in sorted((ROOT / ".github/workflows").glob("*.y*ml")):
        text = path.read_text(encoding="utf-8")
        if re.search(r"^\s+runs-on:\s*macOS\s*$", text, re.MULTILINE):
            macos_users.append(path.relative_to(ROOT).as_posix())
    if macos_users != [".github/workflows/self-check.yml"]:
        raise SystemExit(
            "emergency macOS selector is restricted to self-check.yml: "
            f"{macos_users!r}"
        )


def validate_runtime_lock() -> None:
    lock = _mapping(
        read_json("contracts/action-tool-lock.json"),
        "action/tool lock root is invalid",
    )
    actions = lock.get("third_party_actions")
    if not isinstance(actions, list):
        raise SystemExit("third-party action lock is invalid")
    setup_entries = [
        entry
        for entry in actions
        if isinstance(entry, dict) and entry.get("uses") == "actions/setup-python"
    ]
    expected_setup = {
        "uses": "actions/setup-python",
        "sha": SETUP_PYTHON_SHA,
        "release": SETUP_PYTHON_RELEASE,
        "runtime": "node24",
        "source": "https://github.com/actions/setup-python",
    }
    if setup_entries != [expected_setup]:
        raise SystemExit("actions/setup-python lock entry drifted")

    python = _mapping(lock.get("python"), "python tool lock is invalid")
    if python.get("minimum") != "3.12":
        raise SystemExit("validation Python minimum must be 3.12")
    packages = python.get("packages")
    if not isinstance(packages, list):
        raise SystemExit("validation package lock is invalid")
    pyyaml = [
        entry
        for entry in packages
        if isinstance(entry, dict) and entry.get("name") == "PyYAML"
    ]
    if len(pyyaml) != 1:
        raise SystemExit("validation lock must contain one PyYAML entry")
    package = pyyaml[0]
    if package.get("version") != PY_YAML_VERSION:
        raise SystemExit("PyYAML package version drifted")
    if package.get("runtime") != "python":
        raise SystemExit("PyYAML package runtime drifted")
    if package.get("artifact") != PY_YAML_SOURCE_FILENAME:
        raise SystemExit("PyYAML package source filename drifted")
    if package.get("sha256") != PY_YAML_SOURCE_SHA256:
        raise SystemExit("PyYAML source digest drifted")
    source = _mapping(package.get("source"), "PyYAML source lock is invalid")
    expected_source = {
        "format": "sdist-tar-gz",
        "filename": PY_YAML_SOURCE_FILENAME,
        "url": PY_YAML_SOURCE_URL,
        "sha256": PY_YAML_SOURCE_SHA256,
        "runtimes": PY_YAML_SOURCE_RUNTIMES,
    }
    if source != expected_source:
        raise SystemExit("PyYAML macOS source artifact lock drifted")
    wheels = package.get("wheels")
    if wheels != [PY_YAML_LINUX_WHEEL]:
        raise SystemExit("retained Linux PyYAML wheel lock drifted")


def validate_policies() -> None:
    artifact = read_json("contracts/artifact-policy.json")
    security = read_json("contracts/security-policy.json")
    if artifact != {
        "schema_version": 1,
        "default": "zero-routine-artifacts",
        "exceptions": [],
    }:
        raise SystemExit("artifact policy bootstrap contract drifted")
    if not isinstance(security, dict):
        raise SystemExit("security policy is invalid")
    release = security.get("release_reference_policy")
    if not isinstance(release, dict) or release.get("bootstrap_channel") != "main":
        raise SystemExit("security policy must document @main bootstrap channel")
    if release.get("github_release_required") is not False:
        raise SystemExit("ci-workflows tag release must not require GitHub Release")
    if release.get("attached_artifacts_required") is not False:
        raise SystemExit("ci-workflows tag release must not require attached artifacts")


def validate_authority_docs() -> None:
    combined = "\n".join(
        read_text(path)
        for path in (
            "AGENTS.md",
            "README.md",
            "RUNNERS.md",
            "docs/architecture/ADR-0001-reuse-layers.md",
            "docs/validation/harness.md",
        )
    )
    for required in (
        "Agent State",
        "Flux",
        "@main",
        "Git tag",
        "zero",
        "src/ci_workflows",
        "ci-workflows #60",
        "Flux #268",
    ):
        if required not in combined:
            raise SystemExit(f"bootstrap documentation is missing: {required}")


def main() -> None:
    validate_required_paths()
    validate_public_workflow_exceptions()
    validate_self_check()
    validate_runtime_lock()
    validate_policies()
    validate_authority_docs()
    print("bootstrap policy validation passed")


if __name__ == "__main__":
    main()
