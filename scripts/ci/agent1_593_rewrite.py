#!/usr/bin/env python3
"""Temporary deterministic rewrite helper for ci-workflows #593.

This file is issue scaffolding only and is deleted before final validation.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def write(path: str, text: str) -> None:
    target = ROOT / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


def replace_once(path: str, old: str, new: str) -> None:
    text = read(path)
    if text.count(old) != 1:
        raise RuntimeError(f"{path}: expected one occurrence of replacement marker, found {text.count(old)}")
    write(path, text.replace(old, new))


def regex_once(path: str, pattern: str, replacement: str) -> None:
    text = read(path)
    updated, count = re.subn(pattern, replacement, text, count=1, flags=re.S)
    if count != 1:
        raise RuntimeError(f"{path}: expected one regex replacement, found {count}")
    write(path, updated)


def remove(path: str) -> None:
    target = ROOT / path
    if target.exists():
        target.unlink()


# ---------------------------------------------------------------------------
# Retire the global action/tool lock plumbing from the canonical harness.
# ---------------------------------------------------------------------------
replace_once(
    "src/ci_workflows/validation_model.py",
    '        "PyYAML is required; install the exact version in requirements/validation.lock"\n',
    '        "PyYAML is required; install requirements/validation.txt"\n',
)
replace_once(
    "src/ci_workflows/validation_model.py",
    '''@dataclasses.dataclass(frozen=True)\nclass ActionLock:\n    actions: Mapping[str, Mapping[str, str]]\n    approved_internal_prefixes: tuple[str, ...]\n    python_packages: Mapping[str, Mapping[str, str]]\n\n\n''',
    "",
)
regex_once(
    "src/ci_workflows/validation_model.py",
    r"def load_action_lock\(root: Path\) -> ActionLock:.*?\n\ndef load_public_contract",
    "def load_public_contract",
)

helpers = read("src/ci_workflows/validation_helpers.py")
helpers = helpers.replace("    ActionLock,\n", "")
helpers, count = re.subn(
    r"def _validate_action_reference\(.*?\n\ndef _validate_checkout_step",
    '''def _validate_action_reference(\n    uses: str,\n    relative_path: str,\n    line_number: int,\n    config: HarnessConfig,\n    findings: list[Finding],\n) -> None:\n    \"\"\"Validate only the functional shape of one ``uses`` reference.\n\n    Ordinary Central development intentionally has no global action allowlist, SHA\n    registry, runtime registry, or release-comment checkpoint. Local release paths\n    may still enforce an immutable identity when that identity is part of their own\n    functional release contract.\n    \"\"\"\n\n    if uses.startswith("./"):\n        return\n\n    remote_workflow = _REMOTE_WORKFLOW_RE.match(uses)\n    if remote_workflow:\n        if remote_workflow.group("owner") != "StreamScapeTV":\n            _finding(\n                findings,\n                config,\n                "unapproved-reusable-workflow",\n                relative_path,\n                f"reusable workflow owner is not approved: {uses}",\n                line_number,\n            )\n        ref = remote_workflow.group("ref")\n        if ref != "main" and not _SHA_RE.fullmatch(ref) and not _SEMVER_RE.fullmatch(ref):\n            _finding(\n                findings,\n                config,\n                "mutable-workflow-reference",\n                relative_path,\n                f"unsupported reusable workflow reference {ref!r}",\n                line_number,\n            )\n        return\n\n    if _ACTION_RE.match(uses) is None:\n        _finding(\n            findings,\n            config,\n            "invalid-action-reference",\n            relative_path,\n            f"cannot classify uses reference {uses!r}",\n            line_number,\n        )\n\n\ndef _validate_checkout_step''',
    helpers,
    count=1,
    flags=re.S,
)
if count != 1:
    raise RuntimeError("validation_helpers.py: action reference function replacement failed")
write("src/ci_workflows/validation_helpers.py", helpers)

contracts = read("src/ci_workflows/validation_contracts.py")
contracts = contracts.replace("    ActionLock,\n", "")
contracts, count = re.subn(
    r"def _validate_tool_lock\(.*?\n\ndef _validate_fixture_coverage",
    "def _validate_fixture_coverage",
    contracts,
    count=1,
    flags=re.S,
)
if count != 1:
    raise RuntimeError("validation_contracts.py: tool lock function replacement failed")
write("src/ci_workflows/validation_contracts.py", contracts)

harness = read("src/ci_workflows/validation_harness.py")
harness = harness.replace("    _validate_tool_lock,\n", "")
harness = harness.replace("    ActionLock,\n", "")
harness = harness.replace("    load_action_lock,\n", "")
harness, count = re.subn(
    r"\n_HELM_SIMPLE_SHA = .*?\n\ndef validate_repository",
    "\n\ndef validate_repository",
    harness,
    count=1,
    flags=re.S,
)
if count != 1:
    raise RuntimeError("validation_harness.py: Helm lock carve-out replacement failed")
harness = harness.replace("    lock = load_action_lock(root)\n", "")
harness = harness.replace(
    "    # The checked-in lock remains validated exactly as stored. Helm issue #18\n"
    "    # intentionally removes only its two simple reusable helpers from that\n"
    "    # runtime bootstrap authority; workflow validation still requires their\n"
    "    # exact reviewed SHA and human-readable checkpoint comment.\n"
    "    _validate_tool_lock(root, lock, config, findings)\n"
    "    workflow_lock = _workflow_action_policy(lock)\n",
    "",
)
harness = harness.replace("            workflow_lock,\n", "")
harness = harness.replace(
    "        _validate_action(document, config, workflow_lock, findings, duplicated_runs)\n",
    "        _validate_action(document, config, findings, duplicated_runs)\n",
)
harness = harness.replace('    "ActionLock",\n', "")
write("src/ci_workflows/validation_harness.py", harness)

policy = read("src/ci_workflows/validation_policy.py")
policy = policy.replace("    ActionLock,\n", "")
policy = policy.replace("    lock: ActionLock,\n", "")
policy = policy.replace(
    "            uses,\n            comment,\n            path,\n            line_number,\n            config,\n            lock,\n            findings,\n",
    "            uses,\n            path,\n            line_number,\n            config,\n            findings,\n",
)
# The action comment is no longer policy authority.
policy = policy.replace("    for uses, comment, line_number in _uses_entries(document):\n", "    for uses, _comment, line_number in _uses_entries(document):\n")
# Remove the blanket Actions-artifact registry/retention policy. Private-source
# output protection remains in the private-CI implementation and its tests.
policy, count = re.subn(
    r"            uses = step\.get\(\"uses\"\)\n            if isinstance\(uses, str\) and uses\.startswith\(.*?\n            run = step\.get\(\"run\"\)",
    '            run = step.get("run")',
    policy,
    count=1,
    flags=re.S,
)
if count != 1:
    raise RuntimeError("validation_policy.py: artifact gate replacement failed")
write("src/ci_workflows/validation_policy.py", policy)

# ---------------------------------------------------------------------------
# Replace the bespoke digest-locked parser bootstrap with a conventional
# run-local requirement declaration.
# ---------------------------------------------------------------------------
remove("contracts/action-tool-lock.json")
remove("contracts/artifact-policy.json")
remove("contracts/security-policy.json")
remove("requirements/validation.lock")
remove("scripts/ci/bootstrap_validation_runtime.py")
remove("src/ci_workflows/validation_runtime.py")
remove("tests/test_validation_runtime.py")
remove("tests/test_validation_runtime_archive.py")
remove("tests/test_validation_runtime_macos.py")
write(
    "requirements/validation.txt",
    "# Runtime dependency for the Central YAML validation harness.\nPyYAML>=6,<7\n",
)

SELF_CHECK = r'''name: Central workflow self-check

on:
  pull_request:
    branches: [main]
  push:
    branches: ["**"]
  workflow_dispatch:

permissions:
  contents: read

concurrency:
  group: central-self-check-${{ github.event.pull_request.number || github.ref }}
  cancel-in-progress: true

jobs:
  validate:
    name: Validate repository and reusable workflow contracts
    if: >-
      ${{ github.event_name != 'pull_request' ||
          (github.event.pull_request.user.login == 'mimranfaruqi' &&
           github.event.pull_request.head.repo.full_name == github.repository) }}
    runs-on: [ubuntu-latest]
    timeout-minutes: 10
    env:
      SOURCE_SHA: ${{ github.event.pull_request.head.sha || github.sha }}
      PYTHONDONTWRITEBYTECODE: "1"
      PYTHONPATH: src
    steps:
      - name: Admit trusted workflow source
        shell: bash
        env:
          PR_AUTHOR: ${{ github.event.pull_request.user.login }}
          PR_HEAD_REPOSITORY: ${{ github.event.pull_request.head.repo.full_name }}
          PR_HEAD_SHA: ${{ github.event.pull_request.head.sha }}
        run: |
          set -Eeuo pipefail
          [[ "${SOURCE_SHA}" =~ ^[0-9a-f]{40}$ ]]
          case "${GITHUB_EVENT_NAME}" in
            pull_request)
              test "${PR_AUTHOR}" = "mimranfaruqi"
              test "${PR_HEAD_REPOSITORY}" = "${GITHUB_REPOSITORY}"
              test "${SOURCE_SHA}" = "${PR_HEAD_SHA}"
              ;;
            push|workflow_dispatch)
              test "${SOURCE_SHA}" = "${GITHUB_SHA}"
              ;;
            *) exit 1 ;;
          esac

      - name: Verify pre-provisioned general-Linux CPython 3.12
        shell: bash
        run: |
          set -Eeuo pipefail
          candidate="$(type -P python3.12 || true)"
          if [[ -z "${candidate}" ]]; then candidate="$(type -P python3 || true)"; fi
          test -n "${candidate}"
          test "${candidate}" != "${candidate#/}"
          test -x "${candidate}"
          resolved="$("${candidate}" -c 'import os,sys; print(os.path.realpath(sys.executable))')"
          test "${resolved}" != "${resolved#/}"
          test -x "${resolved}"
          identity="$("${resolved}" -c 'import platform,sys; print(f"{sys.implementation.name}|{sys.version_info.major}.{sys.version_info.minor}|{platform.system()}")')"
          IFS='|' read -r implementation version system <<< "${identity}"
          test "${implementation}" = cpython
          test "${version}" = 3.12
          test "${system}" = Linux
          {
            printf 'VERIFIED_PYTHON=%s\n' "${resolved}"
            printf 'PYTHON_EXECUTABLE=%s\n' "${resolved}"
          } >> "${GITHUB_ENV}"

      - name: Check out exact source
        uses: actions/checkout@v7
        with:
          ref: ${{ env.SOURCE_SHA }}
          fetch-depth: 1
          clean: true
          persist-credentials: false
          set-safe-directory: false

      - name: Verify exact source
        shell: bash
        run: test "$(git rev-parse HEAD)" = "${SOURCE_SHA}"

      - name: Prepare repository-declared validation dependencies
        shell: bash
        run: |
          set -Eeuo pipefail
          validation_root="${HOME}/.ciw-self-check-${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}"
          test ! -e "${validation_root}"
          test ! -L "${validation_root}"
          mkdir -m 0700 "${validation_root}"
          mkdir -m 0700 "${validation_root}/python" "${validation_root}/tmp"
          "${VERIFIED_PYTHON}" -m pip install \
            --disable-pip-version-check \
            --no-cache-dir \
            --target "${validation_root}/python" \
            -r requirements/validation.txt
          PYTHONPATH="${validation_root}/python" "${VERIFIED_PYTHON}" -c 'import yaml; print(f"PyYAML {yaml.__version__}")'
          {
            printf 'VALIDATION_ROOT=%s\n' "${validation_root}"
            printf 'PYTHONPATH=%s:%s/src\n' "${validation_root}/python" "${GITHUB_WORKSPACE}"
          } >> "${GITHUB_ENV}"

      - name: Run canonical validation harness
        shell: bash
        run: |
          set -Eeuo pipefail
          "${VERIFIED_PYTHON}" scripts/ci/validation_harness.py \
            --root . --summary "${VALIDATION_ROOT}/summary.json"

      - name: Verify bootstrap and generated inventory contracts
        shell: bash
        run: |
          set -Eeuo pipefail
          "${VERIFIED_PYTHON}" scripts/ci/bootstrap_check.py
          "${VERIFIED_PYTHON}" scripts/ci/inventory_contract.py validate
          "${VERIFIED_PYTHON}" scripts/ci/inventory_contract.py render --check

      - name: Verify public API agreement
        shell: bash
        run: |
          set -Eeuo pipefail
          "${VERIFIED_PYTHON}" scripts/ci/public_api_contract.py validate
          "${VERIFIED_PYTHON}" scripts/ci/public_api_contract.py render --check

      - name: Run automatically discovered unit tests
        shell: bash
        run: |
          set -Eeuo pipefail
          TMPDIR="${VALIDATION_ROOT}/tmp" \
            "${VERIFIED_PYTHON}" -m unittest discover -s tests -p 'test_*.py' -v
          test "$(git rev-parse HEAD)" = "${SOURCE_SHA}"

      - name: Verify clean tracked tree
        shell: bash
        run: |
          set -Eeuo pipefail
          git diff --exit-code
          git diff --cached --exit-code
          test -z "$(git status --porcelain --untracked-files=all)"

      - name: Remove validation state
        if: always()
        shell: bash
        run: |
          set +e
          cleanup_failed=0
          if [[ -n "${VALIDATION_ROOT:-}" ]]; then
            rm -rf "${VALIDATION_ROOT}" || cleanup_failed=1
            [[ ! -e "${VALIDATION_ROOT}" ]] || cleanup_failed=1
          fi
          find . -type d -name __pycache__ -prune -exec rm -rf {} + || cleanup_failed=1
          find . -type f \( -name '*.pyc' -o -name '*.pyo' \) -delete || cleanup_failed=1
          test -z "$(find . -type d -name __pycache__ -print -quit)" || cleanup_failed=1
          test -z "$(find . -type f \( -name '*.pyc' -o -name '*.pyo' \) -print -quit)" || cleanup_failed=1
          if [[ -d .git ]]; then
            test -z "$(git status --porcelain --untracked-files=all)" || cleanup_failed=1
          fi
          exit "${cleanup_failed}"
'''
write(".github/workflows/self-check.yml", SELF_CHECK)

BOOTSTRAP = r'''#!/usr/bin/env python3
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
'''
write("scripts/ci/bootstrap_check.py", BOOTSTRAP)

# Replace the three remaining functional users of the custom parser bootstrap.
def replace_runtime_bootstrap(path: str) -> None:
    text = read(path)
    text = re.sub(
        r'(?P<indent>\s*)python3 (?P<prefix>"?[^\n]*?)scripts/ci/bootstrap_validation_runtime\.py"? \\\n(?P=indent)\s*--lock [^\n]+ \\\n(?P=indent)\s*--target (?P<target>[^\n]+)',
        lambda m: (
            f"{m.group('indent')}python3 -m pip install --disable-pip-version-check --no-cache-dir "
            f"--target {m.group('target')} -r {m.group('prefix')}requirements/validation.txt"
        ),
        text,
    )
    # Handle plain workflow form without a path prefix.
    text = text.replace(
        '          python3 scripts/ci/bootstrap_validation_runtime.py \\\n            --lock contracts/action-tool-lock.json \\\n            --target "${validation_root}/python"',
        '          python3 -m pip install --disable-pip-version-check --no-cache-dir \\\n            --target "${validation_root}/python" \\\n            -r requirements/validation.txt',
    )
    text = text.replace(
        '          PYTHONDONTWRITEBYTECODE=1 python3 .ciw/scripts/ci/bootstrap_validation_runtime.py \\\n            --lock .ciw/contracts/action-tool-lock.json \\\n            --target "${validation_root}/python"',
        '          PYTHONDONTWRITEBYTECODE=1 python3 -m pip install \\\n            --disable-pip-version-check --no-cache-dir \\\n            --target "${validation_root}/python" \\\n            -r .ciw/requirements/validation.txt',
    )
    write(path, text)

replace_runtime_bootstrap("actions/measure-helm/action.yml")
replace_runtime_bootstrap(".github/workflows/gitops-validation-smoke.yml")
replace_runtime_bootstrap(".github/workflows/device-lock-contract-smoke.yml")
replace_once(
    ".github/workflows/android-validation-smoke.yml",
    "      - contracts/action-tool-lock.json\n",
    "",
)

# ---------------------------------------------------------------------------
# Align authority/documentation with the owner-directed privacy boundary.
# ---------------------------------------------------------------------------
SECURITY_DOC = '''# Privacy and functional CI boundaries\n\n## Mandatory privacy boundary\n\nCentral CI must prevent outsiders from reading private repository source, credentials, private configuration, dependency identities intentionally kept opaque by the private-CI architecture, or private command output. Private-source detailed logs stay out of public GitHub logs/summaries and Actions artifacts and use the bounded private R2 path owned by #495. Credentials are passed only through explicit named secret/environment boundaries and are cleaned with their run-owned state. `secrets: inherit` remains forbidden.\n\nPrivileged jobs must not execute untrusted fork or metadata-event source with private credentials or private checkout authority. Checkout credentials are not persisted after checkout. These rules are retained because violating them can expose private source or credentials.\n\n## Ordinary action and dependency references\n\nThere is no repository-wide action SHA allowlist, release-comment registry, runtime-generation registry, or first-party checkpoint carrier. Ordinary development may use normal GitHub action/reusable-workflow references such as a maintained release tag or `@main` where that channel is the intended compatibility surface. A specific workflow may require an immutable identity only when the identity is part of that workflow's functional release or source contract.\n\nThe validation harness dependency is declared conventionally in `requirements/validation.txt` and installed into run-local state. It is not a supply-chain policy registry and carries no artifact digest ceremony.\n\n## Artifacts\n\nPublic and otherwise non-private workflows may retain Actions artifacts when the feature actually needs them. There is no global zero-artifact registry. Private-source Central runs remain different: their detailed private output must never become a public Actions artifact and continues to use private R2 storage/read-back.\n\n## Release correctness\n\nExact Git tags, immutable product versions, and remote read-back remain required where they are the functional release identity or where read-back proves that publication actually succeeded. Those checks are release correctness, not a global action-pinning program.\n\n## Cleanup\n\nCleanup remains mandatory for credentials, private checkout/authentication state, private logs, and other run-owned state whose residue could expose private data or interfere with later executions. Other temporary state is cleaned when required for deterministic functional execution.\n'''
write("docs/architecture/security-and-artifacts.md", SECURITY_DOC)

HARNESS_DOC = '''# Canonical validation harness\n\n`python3 scripts/ci/validation_harness.py --root .` is the repository-wide static gate for GitHub Actions workflows, composite actions, named Python functions, public API contracts, caller fixtures, event fixtures, and mocked service scenarios.\n\nThe harness uses PyYAML through a narrowed YAML 1.2-style boolean resolver so the GitHub Actions `on` key remains literal. Validation dependencies are declared in `requirements/validation.txt` and installed into a run-local target by self-CI. There is no action/tool SHA registry, digest-locked parser bootstrap, release-comment checkpoint registry, or approved-local-action prefix registry.\n\n## Policy surface\n\nThe gate keeps functional and privacy-sensitive checks: valid workflow/action syntax, bounded runner selection, public API agreement, explicit permissions and secrets, no `secrets: inherit`, protection against privileged execution of untrusted source, credential-safe checkout, required cleanup for credentials/private state, call-graph correctness, source identity where a workflow functionally requires it, release-tag/read-back correctness for publication, and deterministic repository contracts.\n\nOrdinary action references are not required to appear in a global allowlist or to use a repository-registered SHA/comment. First-party reusable workflows may follow the repository's active `@main` channel during development. A specific release workflow may still require immutable source where that is part of the release's functional identity.\n\nThere is no blanket Central ban on Actions artifacts for public/non-private workflows. A workflow may retain an artifact when the feature requires it. Private-source Central runs remain strict: private source, configuration and command output must not be exposed through public logs, summaries, or Actions artifacts.\n\n## Named command and readability contracts\n\n`contracts/ciw-commands.json` remains the checked-in command registry. `contracts/readability-policy.json` keeps maintainability bounds for workflow/action structure. These are implementation/navigation contracts, not supply-chain registries.\n\n## Automatic tests\n\nThe self-check runs `"${VERIFIED_PYTHON}" -m unittest discover -s tests -p 'test_*.py' -v`. New focused suites under `tests/test_*.py` or nested `tests/**/test_*.py` are included without workflow edits. Tests remain hermetic and do not require private credentials, Agent State mutation, Kubernetes authority, signing, or devices unless a dedicated workflow owns that capability.\n\n## Extending the harness\n\nA new reusable workflow adds its public API record, documentation, implementation component, representative fixtures, cleanup behavior where functionally required, and focused tests. It does not add a row to a global action/tool/checkpoint registry. Reuse existing domain providers and prefer one coherent implementation owner over parallel policy layers.\n'''
write("docs/validation/harness.md", HARNESS_DOC)

agents = read("AGENTS.md")
agents = agents.replace(
    "applies the repository's digest-locked validation dependency bootstrap, and uses the verified absolute interpreter for every later Python command.",
    "installs repository-declared validation dependencies into run-local state, and uses the verified absolute interpreter for every later Python command.",
)
agents = agents.replace(
    "- Routine workflows retain zero GitHub Actions artifacts. Private-source detailed logs are never Actions artifacts; they use the fixed private R2 path described above. Any other artifact exception must be named, bounded, justified, redacted, registered in contract, and tested.\n",
    "- Public and otherwise non-private workflows may use Actions artifacts when the feature functionally requires them. Private-source detailed logs are never Actions artifacts; they use the fixed private R2 path described above, and private source/configuration/command output must not be exposed publicly.\n",
)
agents = agents.replace(
    "- Workflow and action parsing, action and tool pins, permissions, trust classes, source admission, runner profiles, call graphs, readability, public API compatibility, documentation, inventory, fixtures, discovered tests, cleanup, and artifact policy must remain green.\n",
    "- Workflow and action parsing, functional dependency declarations, permissions, trust classes, source admission, runner profiles, call graphs, readability, public API compatibility, documentation, inventory, fixtures, discovered tests, and required cleanup must remain green. Ordinary development has no global action SHA/checkpoint registry.\n",
)
write("AGENTS.md", agents)

# ---------------------------------------------------------------------------
# Reconcile the Python inventory for the modified/deleted implementation files.
# ---------------------------------------------------------------------------
TRIVIAL_METHODS = {"__init__", "__repr__", "__str__", "__hash__", "__eq__", "__post_init__"}


def sentence(text: str | None, fallback: str) -> str:
    value = " ".join((text or "").split()) or fallback
    cut = value.find(". ")
    if cut >= 0:
        value = value[: cut + 1]
    if len(value) > 220:
        value = value[:217].rstrip() + "..."
    if value[-1] not in ".!?":
        value += "."
    return value


def signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    prefix = "async " if isinstance(node, ast.AsyncFunctionDef) else ""
    suffix = f" -> {ast.unparse(node.returns)}" if node.returns is not None else ""
    return f"{prefix}{node.name}({ast.unparse(node.args)}){suffix}"


def declarations(tree: ast.Module, module_summary: str) -> tuple[list[dict[str, object]], list[dict[str, str]]]:
    classes: list[dict[str, object]] = []
    functions: list[dict[str, str]] = []
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            methods = []
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) and child.name not in TRIVIAL_METHODS:
                    methods.append({
                        "name": child.name,
                        "signature": signature(child),
                        "purpose": sentence(ast.get_docstring(child, clean=True), f"Implements {node.name}.{child.name} behavior for {module_summary.rstrip('.')}"),
                    })
            classes.append({
                "name": node.name,
                "purpose": sentence(ast.get_docstring(node, clean=True), f"Defines {node.name} for {module_summary.rstrip('.')}"),
                "methods": methods,
            })
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            functions.append({
                "name": node.name,
                "signature": signature(node),
                "purpose": sentence(ast.get_docstring(node, clean=True), f"Implements {node.name} for {module_summary.rstrip('.')}"),
            })
    return classes, functions


def internal_dependencies(tree: ast.Module, module_names: set[str]) -> list[str]:
    result: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            candidates: list[str] = []
            if node.level and node.module:
                candidates.append(node.module.split(".")[0])
            elif node.module and node.module.startswith("ci_workflows."):
                candidates.append(node.module.removeprefix("ci_workflows.").split(".")[0])
            elif node.level and not node.module:
                candidates.extend(alias.name.split(".")[0] for alias in node.names)
            result.update(item for item in candidates if item in module_names)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("ci_workflows."):
                    item = alias.name.removeprefix("ci_workflows.").split(".")[0]
                    if item in module_names:
                        result.add(item)
    return sorted(result)


inventory_path = ROOT / "PYTHON_INVENTORY.yml"
inventory = yaml.safe_load(inventory_path.read_text(encoding="utf-8"))
source_paths = sorted((ROOT / "src/ci_workflows").glob("*.py"))
source_rel = {path.relative_to(ROOT).as_posix() for path in source_paths}
module_names = {path.stem for path in source_paths if path.name != "__init__.py"}
modified_python = {
    "src/ci_workflows/validation_model.py",
    "src/ci_workflows/validation_helpers.py",
    "src/ci_workflows/validation_contracts.py",
    "src/ci_workflows/validation_harness.py",
    "src/ci_workflows/validation_policy.py",
}
for domain, raw_domain in inventory["domains"].items():
    files = [record for record in raw_domain["files"] if record["path"] in source_rel]
    raw_domain["files"] = files
    for record in files:
        if record["path"] not in modified_python:
            continue
        path = ROOT / record["path"]
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        responsibility = sentence(ast.get_docstring(tree, clean=True), f"Implementation module {path.name}")
        classes, functions = declarations(tree, responsibility)
        record["responsibility"] = responsibility
        record["classes"] = classes
        record["functions"] = functions
        record["internal_dependencies"] = internal_dependencies(tree, module_names)

inventory["summary"]["python_files_discovered"] = len(source_paths)
inventory["summary"]["note"] = "Protected-main inventory reconciled for #593 after retiring the global action/tool policy and custom validator runtime; private-source confidentiality remains independently enforced."
# Rebuild the >2-file report from retained domain metadata while preserving the
# human assessment/reason for unaffected domains.
prior_reports = {item["domain"]: item for item in inventory.get("multi_file_domains", [])}
reports = []
for domain, raw_domain in inventory["domains"].items():
    files = sorted(record["path"] for record in raw_domain["files"])
    if len(files) <= 2:
        continue
    prior = prior_reports.get(domain, {})
    report = {
        "domain": domain,
        "current_file_count": len(files),
        "files": files,
        "assessment": prior.get("assessment", "consolidation-required"),
        "reason": prior.get("reason", "Current implementation remains split across more than two modules and requires later bounded consolidation."),
        "intended_target": prior.get("intended_target", raw_domain["target_module"]),
    }
    if domain == "validation":
        report["reason"] = "The custom digest-locked runtime is retired by #593; remaining validation policy/model/helper modules are existing consolidation debt rather than a dependency-bootstrap boundary."
        report["intended_target"] = "validation_harness.py"
    reports.append(report)
inventory["multi_file_domains"] = reports
inventory_path.write_text(yaml.safe_dump(inventory, sort_keys=False, allow_unicode=True, width=180), encoding="utf-8")

print(f"#593 rewrite complete; python_files={len(source_paths)}")
