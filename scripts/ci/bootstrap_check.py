#!/usr/bin/env python3
"""Validate the pre-public-API repository bootstrap contract."""

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
    ".github/workflows/bootstrap-self-check.yml",
    "actions/README.md",
    "src/ci_workflows/__init__.py",
    "scripts/release/README.md",
    "contracts/artifact-policy.json",
    "contracts/security-policy.json",
    "docs/architecture/ADR-0001-reuse-layers.md",
    "docs/architecture/security-and-artifacts.md",
    "docs/consumers/access.md",
    "docs/workflows/README.md",
    "tests/fixtures/README.md",
)

FORBIDDEN_BOOTSTRAP_PATTERNS = (
    "secrets: inherit",
    "upload-artifact",
    "pull_request_target:",
    "packages: write",
    "id-token: write",
)


def read_text(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def validate_required_paths() -> None:
    missing = [path for path in REQUIRED_PATHS if not (ROOT / path).is_file()]
    if missing:
        raise SystemExit(f"missing bootstrap files: {', '.join(missing)}")


def validate_no_public_workflow_api() -> None:
    workflows = ROOT / ".github" / "workflows"
    unexpected = sorted(path.name for path in workflows.glob("reusable-*.yml"))
    unexpected += sorted(path.name for path in workflows.glob("reusable-*.yaml"))
    if unexpected:
        raise SystemExit(
            "public reusable workflows are forbidden before inventory/API approval: "
            + ", ".join(unexpected)
        )


def validate_self_check() -> None:
    source = read_text(".github/workflows/bootstrap-self-check.yml")
    required = (
        "runs-on: homelab-portable-linux-x64",
        "timeout-minutes: 10",
        "persist-credentials: false",
        "EXPECTED_SHA:",
        'test "$(git rev-parse HEAD)" = "$EXPECTED_SHA"',
        "python3 scripts/ci/bootstrap_check.py",
        "python3 -m unittest discover",
        "git diff --exit-code",
    )
    for token in required:
        if token not in source:
            raise SystemExit(f"bootstrap workflow is missing required contract: {token}")
    for token in FORBIDDEN_BOOTSTRAP_PATTERNS:
        if token in source:
            raise SystemExit(f"bootstrap workflow contains forbidden contract: {token}")
    if re.search(r"runs-on:\s*(?:\[)?self-hosted", source):
        raise SystemExit("bootstrap workflow must not use generic self-hosted")


def validate_policies() -> None:
    artifact = json.loads(read_text("contracts/artifact-policy.json"))
    security = json.loads(read_text("contracts/security-policy.json"))
    if artifact != {
        "schema_version": 1,
        "default": "zero-routine-artifacts",
        "exceptions": [],
    }:
        raise SystemExit("artifact policy bootstrap contract drifted")
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
    validate_no_public_workflow_api()
    validate_self_check()
    validate_policies()
    validate_authority_docs()
    print("bootstrap policy validation passed")


if __name__ == "__main__":
    main()
