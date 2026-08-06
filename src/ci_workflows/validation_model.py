"""Canonical static validation harness for StreamScapeTV GitHub Actions.

The harness deliberately separates semantic YAML parsing from the few source-shape
checks that must retain comments or exact expressions.  It validates workflows,
composite actions, public API compatibility, call graphs, trust boundaries, and
repository fixture coverage without executing product or consumer source.
"""
from __future__ import annotations

import dataclasses
import json
import re
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence

try:
    import yaml
except ImportError as error:  # pragma: no cover - exercised by the CI bootstrap
    raise RuntimeError(
        "PyYAML is required; install the exact version in requirements/validation.lock"
    ) from error


_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_SEMVER_RE = re.compile(r"^v[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?$")
_JOB_ID_RE = re.compile(r"^[a-z][a-z0-9_]{1,63}$")
_LOCAL_WORKFLOW_RE = re.compile(r"^\./\.github/workflows/(?:reusable|internal)-[^/]+\.ya?ml$")
_REMOTE_WORKFLOW_RE = re.compile(
    r"^(?P<owner>[A-Za-z0-9_.-]+)/(?P<repo>[A-Za-z0-9_.-]+)/"
    r"(?P<path>\.github/workflows/(?:reusable|internal)-[^@]+\.ya?ml)@(?P<ref>.+)$"
)
_ACTION_RE = re.compile(
    r"^(?P<owner>[A-Za-z0-9_.-]+)/(?P<repo>[A-Za-z0-9_.-]+)"
    r"(?P<path>/[^@]+)?@(?P<ref>[^\s]+)$"
)
_USES_LINE_RE = re.compile(
    r"^\s*(?:-\s*)?uses:\s*(?P<uses>[^\s#]+)(?:\s+#\s*(?P<comment>.+?))?\s*$"
)
_SECRET_EXPR_RE = re.compile(r"\$\{\{\s*secrets\.([A-Za-z0-9_]+)\s*\}\}")
_GITHUB_EXPR_RE = re.compile(r"\$\{\{.*?\}\}")
_FORBIDDEN_INPUT_NAMES = {
    "runner",
    "runs_on",
    "runner_labels",
    "container_engine",
    "docker_command",
    "buildah_command",
    "registry_host",
    "registry_command",
    "secret_name",
    "kubeconfig_path",
    "cluster",
    "namespace",
    "arbitrary_command",
    "shell",
    "callback_url",
}
_HIGH_RISK_EVENTS = {"pull_request_target", "issue_comment", "workflow_run"}
_UNTRUSTED_EVENTS = {"pull_request", "pull_request_target", "issue_comment", "workflow_run"}
_PUBLICATION_WORDS = ("buildah push", "skopeo copy", "helm push", "docker push", "oras push")
_READBACK_WORDS = ("skopeo inspect", "helm pull", "helm show", "oras manifest fetch", "read-back", "read back")
_CREDENTIAL_WORDS = (
    "secrets.",
    "registry_token",
    "kubeconfig",
    "sops",
    "auth_file",
    "authorization:",
    "password-stdin",
)
_TEMP_STATE_WORDS = (
    "runner_temp",
    "mktemp",
    "buildah",
    "podman",
    "docker",
    "helm registry login",
    "simctl",
    "adb ",
)


class ActionsLoader(yaml.SafeLoader):
    """YAML 1.2-ish loader that preserves GitHub's literal ``on`` key."""


# PyYAML's default YAML 1.1 bool resolver turns on/off/yes/no into booleans.
# Copy the resolver table before narrowing booleans to true/false only.
ActionsLoader.yaml_implicit_resolvers = {
    key: list(value) for key, value in yaml.SafeLoader.yaml_implicit_resolvers.items()
}
for first_character, resolvers in list(ActionsLoader.yaml_implicit_resolvers.items()):
    ActionsLoader.yaml_implicit_resolvers[first_character] = [
        (tag, regexp)
        for tag, regexp in resolvers
        if tag != "tag:yaml.org,2002:bool"
    ]
ActionsLoader.add_implicit_resolver(
    "tag:yaml.org,2002:bool",
    re.compile(r"^(?:true|false)$", re.IGNORECASE),
    list("tTfF"),
)


@dataclasses.dataclass(frozen=True, order=True)
class Finding:
    """One deterministic policy failure."""

    rule: str
    path: str
    message: str
    line: int = 0

    def render(self) -> str:
        location = f"{self.path}:{self.line}" if self.line else self.path
        return f"[{self.rule}] {location}: {self.message}"


class HarnessFailure(RuntimeError):
    """Raised when repository validation produces findings."""

    def __init__(self, findings: Sequence[Finding]):
        self.findings = tuple(sorted(findings))
        super().__init__("\n".join(finding.render() for finding in self.findings))


@dataclasses.dataclass(frozen=True)
class ParsedDocument:
    path: Path
    relative_path: str
    raw: str
    data: Mapping[str, Any]


@dataclasses.dataclass(frozen=True)
class RepositoryInventory:
    workflows: tuple[Path, ...]
    actions: tuple[Path, ...]
    python_sources: tuple[Path, ...]
    adapters: tuple[Path, ...]
    contracts: tuple[Path, ...]
    tests: tuple[Path, ...]
    docs: tuple[Path, ...]


@dataclasses.dataclass(frozen=True)
class PublicContract:
    registry: Mapping[str, Any]
    records_by_file: Mapping[str, Mapping[str, Any]]
    permission_profiles: Mapping[str, Mapping[str, Any]]
    max_depth: int
    forbidden_caller_fields: frozenset[str]


@dataclasses.dataclass(frozen=True)
class HarnessConfig:
    max_inline_run_lines: int
    max_matrix_jobs: int
    max_timeout_minutes: int
    allowed_runner_profiles: frozenset[str]
    required_fixture_callers: frozenset[str]
    required_event_fixtures: frozenset[str]
    required_service_scenarios: Mapping[str, frozenset[str]]
    exceptions: Mapping[str, frozenset[str]]

    def excepts(self, relative_path: str, rule: str) -> bool:
        return rule in self.exceptions.get(relative_path, frozenset())


@dataclasses.dataclass(frozen=True)
class ActionLock:
    actions: Mapping[str, Mapping[str, str]]
    approved_internal_prefixes: tuple[str, ...]
    python_packages: Mapping[str, Mapping[str, str]]


@dataclasses.dataclass(frozen=True)
class ValidationResult:
    inventory: RepositoryInventory
    findings: tuple[Finding, ...]
    test_count: int
    workflow_count: int
    action_count: int
    public_api_count: int

    def require_success(self) -> "ValidationResult":
        if self.findings:
            raise HarnessFailure(self.findings)
        return self


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"cannot read JSON contract {path}: {error}") from error


def _relative(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def load_actions_yaml(path: Path, root: Path | None = None) -> ParsedDocument:
    """Parse a workflow/action deterministically while preserving the ``on`` key."""

    raw = path.read_text(encoding="utf-8")
    try:
        data = yaml.load(raw, Loader=ActionsLoader)
    except yaml.YAMLError as error:
        raise ValueError(f"invalid YAML in {path}: {error}") from error
    if not isinstance(data, Mapping):
        raise ValueError(f"top level of {path} must be a mapping")
    relative_path = _relative(root, path) if root is not None else path.as_posix()
    return ParsedDocument(path=path, relative_path=relative_path, raw=raw, data=data)


def discover_repository(root: Path) -> RepositoryInventory:
    """Discover every supported source type in stable lexical order."""

    root = root.resolve()

    def files(patterns: Sequence[str]) -> tuple[Path, ...]:
        found: set[Path] = set()
        for pattern in patterns:
            found.update(path for path in root.glob(pattern) if path.is_file())
        return tuple(sorted(found, key=lambda path: _relative(root, path)))

    workflows = files((".github/workflows/*.yml", ".github/workflows/*.yaml"))
    actions = files(
        (
            ".github/actions/**/action.yml",
            ".github/actions/**/action.yaml",
            "actions/**/action.yml",
            "actions/**/action.yaml",
        )
    )
    return RepositoryInventory(
        workflows=workflows,
        actions=actions,
        python_sources=files(("src/ci_workflows/**/*.py",)),
        adapters=files(("scripts/ci/**/*.py", "scripts/ci/**/*.sh")),
        contracts=files(("contracts/**/*.json",)),
        tests=files(("tests/test_*.py", "tests/**/test_*.py")),
        docs=files(("docs/**/*.md", "README.md")),
    )


def _collect_runner_profiles_from_contracts(root: Path) -> set[str]:
    profiles: set[str] = set()
    for path in sorted((root / "contracts").glob("**/*runner*.json")):
        try:
            payload = _read_json(path)
        except ValueError:
            continue

        def visit(value: Any, key: str | None = None) -> None:
            if isinstance(value, Mapping):
                for child_key, child_value in value.items():
                    visit(child_value, str(child_key))
            elif isinstance(value, list):
                for child in value:
                    visit(child, key)
            elif isinstance(value, str) and key in {
                "id",
                "profile",
                "semantic_profile",
                "semantic_runner_profile",
                "runner",
                "runs_on",
                "label",
                "labels",
            }:
                if value and not value.startswith("contract:"):
                    profiles.add(value)

        visit(payload)
    return profiles


def load_harness_config(root: Path) -> HarnessConfig:
    payload = _read_json(root / "contracts/validation-harness.json")
    exceptions: dict[str, frozenset[str]] = {}
    for entry in payload.get("exceptions", []):
        exceptions[str(entry["path"])] = frozenset(str(rule) for rule in entry["rules"])
    service_scenarios = {
        str(service): frozenset(str(name) for name in names)
        for service, names in payload.get("required_service_scenarios", {}).items()
    }
    runners = set(str(value) for value in payload.get("allowed_runner_profiles", []))
    runners.update(_collect_runner_profiles_from_contracts(root))
    return HarnessConfig(
        max_inline_run_lines=int(payload["max_inline_run_lines"]),
        max_matrix_jobs=int(payload["max_matrix_jobs"]),
        max_timeout_minutes=int(payload["max_timeout_minutes"]),
        allowed_runner_profiles=frozenset(runners),
        required_fixture_callers=frozenset(payload["required_fixture_callers"]),
        required_event_fixtures=frozenset(payload["required_event_fixtures"]),
        required_service_scenarios=service_scenarios,
        exceptions=exceptions,
    )


def load_action_lock(root: Path) -> ActionLock:
    payload = _read_json(root / "contracts/action-tool-lock.json")
    actions = {
        str(entry["uses"]): {
            "sha": str(entry["sha"]),
            "release": str(entry["release"]),
            "runtime": str(entry["runtime"]),
        }
        for entry in payload.get("third_party_actions", [])
    }
    packages = {
        str(entry["name"]): {key: str(value) for key, value in entry.items() if key != "name"}
        for entry in payload.get("python", {}).get("packages", [])
    }
    return ActionLock(
        actions=actions,
        approved_internal_prefixes=tuple(payload.get("approved_internal_actions", [])),
        python_packages=packages,
    )


def load_public_contract(root: Path) -> PublicContract:
    registry = _read_json(root / "contracts/public-workflows.json")
    records_by_file: dict[str, Mapping[str, Any]] = {}
    for fragment_name in registry.get("fragment_contracts", []):
        fragment = _read_json(root / str(fragment_name))
        for record in fragment.get("workflows", []):
            records_by_file[str(record["file"])] = record
    permissions_payload = _read_json(root / "contracts/permission-profiles.json")
    permission_profiles = {
        str(profile["id"]): profile for profile in permissions_payload.get("profiles", [])
    }
    types_payload = _read_json(root / "contracts/public-workflow-types.json")
    defaults = types_payload.get("defaults", {})
    return PublicContract(
        registry=registry,
        records_by_file=records_by_file,
        permission_profiles=permission_profiles,
        max_depth=int(defaults.get("max_reusable_workflow_depth", 2)),
        forbidden_caller_fields=frozenset(
            str(value) for value in defaults.get("forbidden_caller_fields", _FORBIDDEN_INPUT_NAMES)
        ),
    )
