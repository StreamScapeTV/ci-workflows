#!/usr/bin/env python3
"""Validate the repository bootstrap and its bounded public-API exception."""

from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

REQUIRED_PATHS = (
    "AGENTS.md",
    "CONTRIBUTING.md",
    "README.md",
    ".github/ISSUE_TEMPLATE/implementation.yml",
    ".github/pull_request_template.md",
    ".github/workflows/self-check.yml",
    "actions/README.md",
    "src/ci_workflows/__init__.py",
    "scripts/release/README.md",
    "contracts/artifact-policy.json",
    "contracts/security-policy.json",
    "contracts/bootstrap-public-workflows.json",
    "docs/architecture/ADR-0001-reuse-layers.md",
    "docs/architecture/security-and-artifacts.md",
    "docs/consumers/access.md",
    "docs/workflows/README.md",
    "tests/fixtures/README.md",
)

FORBIDDEN_SELF_CHECK_PATTERNS = (
    "secrets: inherit",
    "upload-artifact",
    "pull_request_target:",
    "packages: write",
    "id-token: write",
    "homelab-portable-linux-x64",
)


def read_text(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def read_json(relative: str) -> object:
    return json.loads(read_text(relative))


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


def validate_self_check() -> None:
    source = read_text(".github/workflows/self-check.yml")
    required = (
        "runs-on: portable",
        "timeout-minutes: 10",
        "persist-credentials: false",
        "SOURCE_SHA:",
        'test "$(git rev-parse HEAD)" = "${SOURCE_SHA}"',
        "python3 scripts/ci/bootstrap_check.py",
        "python3 -m unittest discover -s tests -p 'test_*.py' -v",
        "git diff --exit-code",
        "Confirm zero Actions artifacts",
    )
    for token in required:
        if token not in source:
            raise SystemExit(f"self-check is missing required contract: {token}")
    for token in FORBIDDEN_SELF_CHECK_PATTERNS:
        if token in source:
            raise SystemExit(f"self-check contains forbidden contract: {token}")
    if re.search(r"runs-on:\s*(?:\[)?self-hosted", source):
        raise SystemExit("self-check must not use generic self-hosted")


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
            "docs/architecture/ADR-0001-reuse-layers.md",
        )
    )
    for required in (
        "Agent State",
        "Flux",
        "@main",
        "Git tag",
        "zero",
        "src/ci_workflows",
    ):
        if required not in combined:
            raise SystemExit(f"bootstrap documentation is missing: {required}")


def main() -> None:
    validate_required_paths()
    validate_public_workflow_exceptions()
    validate_self_check()
    validate_policies()
    validate_authority_docs()
    print("bootstrap policy validation passed")


if __name__ == "__main__":
    main()
