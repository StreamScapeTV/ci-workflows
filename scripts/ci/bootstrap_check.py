#!/usr/bin/env python3
"""Validate the functional repository bootstrap for Central self-CI."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[2]
GENERAL_LINUX_SELECTOR = ["linux", "amd64", "general", "small"]
SELF_CHECK_RUNNER = "ubuntu-latest"
SELF_CHECK_RUNS_ON_SOURCE = "[ubuntu-latest]"

REQUIRED_PATHS = (
    "AGENTS.md",
    "CONTRIBUTING.md",
    "README.md",
    "RUNNERS.md",
    ".github/workflows/self-check.yml",
    "src/ci_workflows/__init__.py",
    "scripts/ci/validation_harness.py",
    "contracts/bootstrap-public-workflows.json",
    "contracts/runner-profiles.json",
    "contracts/runner-execution-backends.json",
    "docs/validation/harness.md",
    "requirements/validation.txt",
)

FORBIDDEN_SELF_CHECK_PATTERNS = (
    "secrets: inherit",
    "pull_request_target:",
    "packages: write",
    "id-token: write",
    "runs-on: self-hosted",
    "runs-on: mobile",
    "runs-on: buildah",
    "runs-on: flux-control",
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
    retired = (
        "contracts/action-tool-lock.json",
        "contracts/artifact-policy.json",
        "contracts/security-policy.json",
        "requirements/validation.lock",
        "scripts/ci/bootstrap_validation_runtime.py",
        "src/ci_workflows/validation_runtime.py",
    )
    residue = [path for path in retired if (ROOT / path).exists()]
    if residue:
        raise SystemExit(f"retired policy/bootstrap files remain: {', '.join(residue)}")


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
    return sorted(str(entry["path"]) for entry in allowed if isinstance(entry, dict))


def validate_public_workflow_exceptions() -> None:
    actual = public_workflow_paths()
    allowed = allowed_bootstrap_workflows()
    if actual != allowed:
        raise SystemExit(f"public reusable workflow set drifted: actual={actual!r} allowed={allowed!r}")


def _general_small_profile(contract: Mapping[str, Any]) -> Mapping[str, Any]:
    profiles = contract.get("profiles")
    if not isinstance(profiles, list):
        raise SystemExit("runner profile contract is invalid")
    matches = [p for p in profiles if isinstance(p, dict) and p.get("id") == "general-small"]
    if len(matches) != 1:
        raise SystemExit("runner contract requires one general-small profile")
    return matches[0]


def validate_runner_contract() -> None:
    harness = _mapping(read_json("contracts/validation-harness.json"), "validation harness contract is invalid")
    if harness.get("allowed_runner_profiles") != ["portable"]:
        raise SystemExit("general validation semantic profile must remain portable")
    runner_contract = _mapping(read_json("contracts/runner-profiles.json"), "runner profile contract is invalid")
    profile = _general_small_profile(runner_contract)
    if profile.get("default_internal_selector") != GENERAL_LINUX_SELECTOR:
        raise SystemExit("portable compatibility must resolve to sized general-small")
    backend = _mapping(read_json("contracts/runner-execution-backends.json"), "runner backend contract is invalid")
    hosted = _mapping(backend.get("github-hosted"), "github-hosted backend contract is invalid")
    if hosted.get("runs_on") != [SELF_CHECK_RUNNER]:
        raise SystemExit("standard hosted runner selector drifted")


def validate_self_check() -> None:
    source = read_text(".github/workflows/self-check.yml")
    required = (
        f"runs-on: {SELF_CHECK_RUNS_ON_SOURCE}",
        "Admit trusted workflow source",
        "Verify pre-provisioned general-Linux CPython 3.12",
        "persist-credentials: false",
        "actions/checkout@v7",
        'test "$(git rev-parse HEAD)" = "${SOURCE_SHA}"',
        "requirements/validation.txt",
        '"${VERIFIED_PYTHON}" -m pip install',
        '"${VERIFIED_PYTHON}" scripts/ci/validation_harness.py',
        '"${VERIFIED_PYTHON}" -m unittest discover',
        "if: always()",
    )
    for token in required:
        if token not in source:
            raise SystemExit(f"self-check is missing required functional contract: {token}")
    lowered = source.lower()
    for token in FORBIDDEN_SELF_CHECK_PATTERNS:
        if token.lower() in lowered:
            raise SystemExit(f"self-check contains forbidden privacy/authority contract: {token}")
    runs_on = re.findall(r"^\s+runs-on:\s*(.+?)\s*$", source, re.MULTILINE)
    if runs_on != [SELF_CHECK_RUNS_ON_SOURCE]:
        raise SystemExit(f"self-check must use exactly {SELF_CHECK_RUNS_ON_SOURCE}, found {runs_on!r}")
    if "action-tool-lock" in source or "Confirm zero Actions artifacts" in source:
        raise SystemExit("self-check must not restore retired global security policy")


def validate_requirements() -> None:
    lines = [line.strip() for line in read_text("requirements/validation.txt").splitlines() if line.strip() and not line.lstrip().startswith("#")]
    if not any(line.lower().startswith("pyyaml") for line in lines):
        raise SystemExit("validation requirements must declare PyYAML")
    if any("--hash" in line or "sha256" in line.lower() for line in lines):
        raise SystemExit("ordinary validation dependencies must not recreate digest-lock policy")


def validate_authority_docs() -> None:
    combined = "\n".join(read_text(path) for path in ("AGENTS.md", "docs/validation/harness.md", "docs/architecture/security-and-artifacts.md"))
    for required in ("private source", "credentials", "private", "requirements/validation.txt", "@main"):
        if required.lower() not in combined.lower():
            raise SystemExit(f"bootstrap documentation is missing: {required}")


def main() -> None:
    validate_required_paths()
    validate_public_workflow_exceptions()
    validate_runner_contract()
    validate_self_check()
    validate_requirements()
    validate_authority_docs()
    print("bootstrap functional validation passed")


if __name__ == "__main__":
    main()
