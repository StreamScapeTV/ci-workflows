"""Contract loading, source authority, and plan construction for GitOps validation."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path, PurePosixPath
from typing import Any, Mapping
from urllib.parse import urlparse

from .gitops_types import (
    GitOpsPlan,
    GitOpsPolicyScript,
    GitOpsProfile,
    GitOpsRequest,
    GitOpsTarget,
    GitOpsTargetKind,
    GitOpsToolPin,
    GitOpsValidationError,
    VendoredDependency,
)

CONTRACT_PATH = Path("contracts/gitops-validation.json")
_FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SAFE_ID = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
_REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_EXACT_VERSION = re.compile(r"^v?[0-9]+\.[0-9]+\.[0-9]+$")
_PROFILE_NAMES = {profile.value for profile in GitOpsProfile}
_ALLOWED_TRUST = {"untrusted-fork", "trusted-pr", "trusted-exact"}
_REQUIRED_FORBIDDEN_INPUTS = {
    "runner",
    "runs_on",
    "runner_labels",
    "tool_url",
    "tool_digest",
    "tool_version",
    "command",
    "arguments",
    "shell",
    "callback",
    "registry",
    "cluster",
    "kubeconfig",
    "namespace",
    "service_account",
    "sops_key",
    "decryption_key",
    "deployment",
    "flux_reconcile",
    "artifact_upload",
}
_EXPECTED_TOOLS = {
    "helm": {
        "version": "3.18.6",
        "url": "https://get.helm.sh/helm-v3.18.6-linux-amd64.tar.gz",
        "sha256": "3f43c0aa57243852dd542493a0f54f1396c0bc8ec7296bbb2c01e802010819ce",
        "archive_member": "linux-amd64/helm",
        "max_bytes": 30_000_000,
        "max_unpacked_bytes": 61_000_000,
        "version_args": ["version", "--short"],
        "version_pattern": r"v3\.18\.6(?:\+g[0-9a-f]+)?",
        "allowed_hosts": ["get.helm.sh"],
    },
    "kustomize": {
        "version": "5.8.1",
        "url": "https://github.com/kubernetes-sigs/kustomize/releases/download/kustomize/v5.8.1/kustomize_v5.8.1_linux_amd64.tar.gz",
        "sha256": "029a7f0f4e1932c52a0476cf02a0fd855c0bb85694b82c338fc648dcb53a819d",
        "archive_member": "kustomize",
        "max_bytes": 20_000_000,
        "max_unpacked_bytes": 13_000_000,
        "version_args": ["version"],
        "version_pattern": r"v?5\.8\.1",
        "allowed_hosts": [
            "github.com",
            "release-assets.githubusercontent.com",
            "objects.githubusercontent.com",
        ],
    },
    "pyyaml": {
        "version": "6.0.3",
        "url": "https://files.pythonhosted.org/packages/8b/9d/b3589d3877982d4f2329302ef98a8026e7f4443c765c46cfecc8858c6b4b/pyyaml-6.0.3-cp312-cp312-manylinux2014_x86_64.manylinux_2_17_x86_64.manylinux_2_28_x86_64.whl",
        "sha256": "ba1cc08a7ccde2d2ec775841541641e4548226580ab850948cbfda66a1befcdc",
        "archive_member": "yaml/__init__.py",
        "max_bytes": 3_000_000,
        "max_unpacked_bytes": 3_000_000,
        "version_args": ["--version"],
        "version_pattern": r"6\.0\.3",
        "allowed_hosts": ["files.pythonhosted.org"],
    },
}


def fail(code: str, detail: str = "") -> None:
    raise GitOpsValidationError(code, detail)


def require(condition: bool, code: str, detail: str = "") -> None:
    if not condition:
        fail(code, detail)


def _mapping(value: object, code: str = "contract_invalid") -> dict[str, Any]:
    require(isinstance(value, dict), code)
    return dict(value)


def _exact_mapping(
    value: object,
    keys: set[str],
    code: str = "contract_invalid",
) -> dict[str, Any]:
    result = _mapping(value, code)
    require(set(result) == keys, code, "field set")
    return result


def _strings(value: object, *, empty: bool = True) -> tuple[str, ...]:
    require(isinstance(value, list), "contract_invalid")
    require(
        all(isinstance(item, str) and bool(item) for item in value),
        "contract_invalid",
    )
    require(empty or bool(value), "contract_invalid")
    require(len(value) == len(set(value)), "contract_invalid")
    return tuple(value)


def safe_relative(value: object, *, allow_dot: bool = True) -> str:
    """Return one normalized relative path without traversal or backslashes."""

    require(isinstance(value, str) and value and "\x00" not in value, "invalid_path")
    require("\\" not in value, "invalid_path")
    path = PurePosixPath(value)
    if allow_dot and path == PurePosixPath("."):
        return "."
    require(
        not path.is_absolute()
        and path != PurePosixPath(".")
        and all(part not in {"", ".", ".."} for part in path.parts),
        "invalid_path",
        value,
    )
    return path.as_posix()


def bounded_path(
    root: Path,
    relative: str,
    *,
    must_exist: bool = False,
    kind: str | None = None,
) -> Path:
    """Resolve a source path while rejecting every symlink component."""

    base = root.resolve()
    require(base.is_dir() and not root.is_symlink(), "invalid_path")
    normalized = safe_relative(relative)
    current = base
    for part in PurePosixPath(normalized).parts:
        current = current / part
        require(not current.is_symlink(), "path_symlink_rejected", normalized)
        if not current.exists():
            break
    resolved = current.resolve(strict=False)
    require(resolved == base or base in resolved.parents, "path_escape_rejected", normalized)
    if must_exist:
        require(resolved.exists(), "path_missing", normalized)
    if kind == "file":
        require(resolved.is_file() and not resolved.is_symlink(), "path_missing", normalized)
    if kind == "directory":
        require(resolved.is_dir() and not resolved.is_symlink(), "path_missing", normalized)
    return resolved


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_trust_from_environment(environment: Mapping[str, str]) -> str:
    """Derive immutable source trust from the GitHub event, never caller input."""

    event = environment.get("GITHUB_EVENT_NAME", "")
    if event != "pull_request":
        return "trusted-exact"
    try:
        payload = json.loads(
            Path(environment.get("GITHUB_EVENT_PATH", "")).read_text(encoding="utf-8")
        )
        head_repository = payload["pull_request"]["head"]["repo"]["full_name"]
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as error:
        raise GitOpsValidationError("invalid_input") from error
    require(isinstance(head_repository, str) and head_repository, "invalid_input")
    return (
        "trusted-pr"
        if head_repository == environment.get("GITHUB_REPOSITORY")
        else "untrusted-fork"
    )


def _tool(name: str, raw: object) -> GitOpsToolPin:
    row = _exact_mapping(
        raw,
        {
            "version",
            "url",
            "sha256",
            "archive_member",
            "max_bytes",
            "max_unpacked_bytes",
            "version_args",
            "version_pattern",
            "allowed_hosts",
        },
    )
    expected = _EXPECTED_TOOLS[name]
    for field in (
        "version",
        "url",
        "sha256",
        "archive_member",
        "max_bytes",
        "max_unpacked_bytes",
        "version_args",
        "version_pattern",
        "allowed_hosts",
    ):
        require(row[field] == expected[field], "tool_identity_drift", f"{name}:{field}")
    require(_EXACT_VERSION.fullmatch(str(row["version"])) is not None, "contract_invalid")
    require(_SHA256.fullmatch(str(row["sha256"])) is not None, "contract_invalid")
    parsed = urlparse(str(row["url"]))
    require(parsed.scheme == "https" and parsed.hostname, "contract_invalid")
    allowed_hosts = _strings(row["allowed_hosts"], empty=False)
    require(parsed.hostname in allowed_hosts, "contract_invalid")
    require(
        isinstance(row["max_bytes"], int) and 1_000_000 <= row["max_bytes"] <= 100_000_000,
        "contract_invalid",
    )
    require(
        isinstance(row["max_unpacked_bytes"], int)
        and 1_000_000 <= row["max_unpacked_bytes"] <= 100_000_000,
        "contract_invalid",
    )
    args = _strings(row["version_args"], empty=False)
    require(
        isinstance(row["version_pattern"], str)
        and 1 <= len(row["version_pattern"]) <= 256,
        "contract_invalid",
    )
    try:
        re.compile(row["version_pattern"])
    except re.error as error:
        raise GitOpsValidationError("contract_invalid") from error
    return GitOpsToolPin(
        name=name,
        version=str(row["version"]),
        url=str(row["url"]),
        sha256=str(row["sha256"]),
        archive_member=safe_relative(str(row["archive_member"]), allow_dot=False),
        max_bytes=int(row["max_bytes"]),
        max_unpacked_bytes=int(row["max_unpacked_bytes"]),
        version_args=args,
        version_pattern=str(row["version_pattern"]),
        allowed_hosts=allowed_hosts,
    )


def _target(identifier: str, raw: object) -> GitOpsTarget:
    require(_SAFE_ID.fullmatch(identifier) is not None, "contract_invalid")
    row = _exact_mapping(
        raw,
        {
            "kind",
            "root",
            "include",
            "schema_path",
            "values_files",
            "required_values",
            "sops_files",
            "expected_render_path",
            "kubernetes_version",
            "vendored_dependencies",
        },
    )
    try:
        kind = GitOpsTargetKind(row["kind"])
    except (TypeError, ValueError) as error:
        raise GitOpsValidationError("contract_invalid") from error
    root = safe_relative(row["root"])
    include = _strings(row["include"], empty=False)
    for pattern in include:
        require(
            pattern not in {"*", "**"}
            and ".." not in PurePosixPath(pattern).parts
            and "\\" not in pattern,
            "contract_invalid",
        )
    optional_paths: dict[str, str | None] = {}
    for field in ("schema_path", "expected_render_path"):
        value = row[field]
        optional_paths[field] = (
            None if value in {None, ""} else safe_relative(value, allow_dot=False)
        )
    values_files = tuple(safe_relative(value, allow_dot=False) for value in _strings(row["values_files"]))
    sops_files = tuple(safe_relative(value, allow_dot=False) for value in _strings(row["sops_files"]))
    required_values = _strings(row["required_values"])
    for value in required_values:
        require(re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]*(\.[A-Za-z][A-Za-z0-9_-]*)*", value) is not None, "contract_invalid")
    dependencies: list[VendoredDependency] = []
    require(isinstance(row["vendored_dependencies"], list), "contract_invalid")
    for raw_dependency in row["vendored_dependencies"]:
        dependency = _exact_mapping(
            raw_dependency,
            {"name", "version", "path", "tree_sha256"},
        )
        require(_SAFE_ID.fullmatch(str(dependency["name"])) is not None, "contract_invalid")
        require(_EXACT_VERSION.fullmatch(str(dependency["version"])) is not None, "contract_invalid")
        require(_SHA256.fullmatch(str(dependency["tree_sha256"])) is not None, "contract_invalid")
        dependencies.append(
            VendoredDependency(
                name=str(dependency["name"]),
                version=str(dependency["version"]),
                path=safe_relative(dependency["path"], allow_dot=False),
                tree_sha256=str(dependency["tree_sha256"]),
            )
        )
    if kind is GitOpsTargetKind.YAML:
        require(not values_files and not required_values and not dependencies, "contract_invalid")
    if kind is GitOpsTargetKind.HELM:
        require(values_files and required_values, "contract_invalid")
    if kind is GitOpsTargetKind.KUSTOMIZE:
        require(not values_files and not required_values and not dependencies, "contract_invalid")
    kubernetes_version = row["kubernetes_version"]
    require(
        kubernetes_version is None
        or (
            isinstance(kubernetes_version, str)
            and re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", kubernetes_version)
        ),
        "contract_invalid",
    )
    return GitOpsTarget(
        target_id=identifier,
        kind=kind,
        root=root,
        include=include,
        schema_path=optional_paths["schema_path"],
        values_files=values_files,
        required_values=required_values,
        sops_files=sops_files,
        expected_render_path=optional_paths["expected_render_path"],
        kubernetes_version=kubernetes_version,
        vendored_dependencies=tuple(dependencies),
    )


def _policy(identifier: str, raw: object) -> GitOpsPolicyScript:
    require(_SAFE_ID.fullmatch(identifier) is not None, "contract_invalid")
    row = _exact_mapping(
        raw,
        {
            "path",
            "argv",
            "allowed_profiles",
            "timeout_seconds",
            "max_output_bytes",
            "sha256",
        },
    )
    path = safe_relative(row["path"], allow_dot=False)
    argv = _strings(row["argv"], empty=False)
    require(argv[0] == "python3" and argv[1:] == (path,), "contract_invalid")
    profiles = _strings(row["allowed_profiles"], empty=False)
    require(set(profiles) <= _PROFILE_NAMES, "contract_invalid")
    require(
        isinstance(row["timeout_seconds"], int) and 1 <= row["timeout_seconds"] <= 300,
        "contract_invalid",
    )
    require(
        isinstance(row["max_output_bytes"], int) and 1 <= row["max_output_bytes"] <= 65536,
        "contract_invalid",
    )
    require(_SHA256.fullmatch(str(row["sha256"])) is not None, "contract_invalid")
    return GitOpsPolicyScript(
        policy_id=identifier,
        path=path,
        argv=argv,
        allowed_profiles=profiles,
        timeout_seconds=int(row["timeout_seconds"]),
        max_output_bytes=int(row["max_output_bytes"]),
        sha256=str(row["sha256"]),
    )


def validate_contract(payload: Mapping[str, Any]) -> None:
    require(payload.get("schema_version") == 1, "contract_invalid")
    require(payload.get("contract_version") == "1.0.0", "contract_invalid")
    require(payload.get("organization") == "StreamScapeTV", "contract_invalid")
    api = _exact_mapping(
        payload.get("api"),
        {"name", "version", "workflow", "stable_check"},
    )
    require(api == {
        "name": "validation.gitops",
        "version": "1.0.0",
        "workflow": ".github/workflows/reusable-gitops-validation.yml",
        "stable_check": "CI / GitOps validation",
    }, "contract_invalid")
    require(payload.get("runner_profile") == "portable", "contract_invalid")
    require(payload.get("workspace_profile") == "minimal", "contract_invalid")
    require(payload.get("artifact_policy") == "zero-default", "contract_invalid")
    profiles = _mapping(payload.get("profiles"))
    require(set(profiles) == _PROFILE_NAMES, "contract_invalid")
    for name, raw in profiles.items():
        row = _exact_mapping(raw, {"timeout_minutes", "allowed_source_trust"})
        require(isinstance(row["timeout_minutes"], int) and 1 <= row["timeout_minutes"] <= 120, "contract_invalid")
        trust = set(_strings(row["allowed_source_trust"], empty=False))
        require(trust <= _ALLOWED_TRUST, "contract_invalid")
        if name not in {"source-audit", "yaml"}:
            require("untrusted-fork" not in trust, "contract_invalid")
    tools = _mapping(payload.get("tools"))
    require(set(tools) == set(_EXPECTED_TOOLS), "contract_invalid")
    for name, raw in tools.items():
        _tool(name, raw)
    targets = _mapping(payload.get("targets"))
    require(targets, "contract_invalid")
    for name, raw in targets.items():
        _target(name, raw)
    policies = _mapping(payload.get("policy_scripts"))
    for name, raw in policies.items():
        _policy(name, raw)
    consumers = _mapping(payload.get("consumer_contracts"))
    require(consumers, "contract_invalid")
    for identifier, raw in consumers.items():
        require(_SAFE_ID.fullmatch(identifier) is not None, "contract_invalid")
        row = _exact_mapping(raw, {"repository", "profiles"})
        require(_REPOSITORY.fullmatch(str(row["repository"])) is not None, "contract_invalid")
        profile_rows = _mapping(row["profiles"])
        require(profile_rows, "contract_invalid")
        for profile_name, raw_profile in profile_rows.items():
            require(profile_name in profiles, "contract_invalid")
            profile = _exact_mapping(raw_profile, {"targets", "policy_script"})
            target_ids = _strings(profile["targets"])
            require(set(target_ids) <= set(targets), "contract_invalid")
            if profile_name not in {"source-audit", "changed-tree"}:
                require(bool(target_ids), "contract_invalid")
            policy = profile["policy_script"]
            require(policy is None or policy in policies, "contract_invalid")
            if policy is not None:
                require(profile_name in policies[policy]["allowed_profiles"], "contract_invalid")
    failures = set(_strings(payload.get("failure_codes"), empty=False))
    require(
        {
            "invalid_input",
            "contract_invalid",
            "source_mismatch",
            "source_dirty",
            "tool_download_failed",
            "tool_digest_mismatch",
            "tool_archive_rejected",
            "tool_identity_mismatch",
            "yaml_invalid",
            "yaml_style_failed",
            "schema_invalid",
            "helm_lock_invalid",
            "helm_failed",
            "required_value_missing",
            "kustomize_invalid",
            "kustomize_failed",
            "sops_plaintext_rejected",
            "render_drift",
            "duplicate_object_ownership",
            "policy_failed",
            "source_mutated",
            "cleanup_failed",
            "primary_and_cleanup_failed",
        }
        <= failures,
        "contract_invalid",
    )
    forbidden = set(_strings(payload.get("forbidden_inputs"), empty=False))
    require(_REQUIRED_FORBIDDEN_INPUTS <= forbidden, "contract_invalid")
    cleanup = _mapping(payload.get("cleanup"))
    require(cleanup and all(value is True for value in cleanup.values()), "contract_invalid")


def load_gitops_contract(root: Path) -> dict[str, Any]:
    try:
        payload = json.loads((root / CONTRACT_PATH).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise GitOpsValidationError("contract_invalid") from error
    require(isinstance(payload, dict), "contract_invalid")
    validate_contract(payload)
    return payload


def request_from_environment(
    environment: Mapping[str, str],
    contract: Mapping[str, Any],
) -> GitOpsRequest:
    forbidden = set(contract["forbidden_inputs"])
    supplied = sorted(
        name
        for name, value in environment.items()
        if name.startswith("INPUT_")
        and value
        and name.removeprefix("INPUT_").lower() in forbidden
    )
    require(not supplied, "invalid_input", ",".join(supplied))
    repository = environment.get("GITHUB_REPOSITORY", "")
    admitted_sha = environment.get("INPUT_ADMITTED_SHA", "")
    consumer = environment.get("INPUT_CONSUMER_CONTRACT", "")
    profile_raw = environment.get("INPUT_VALIDATION_PROFILE", "")
    require(_REPOSITORY.fullmatch(repository) is not None, "invalid_input")
    require(_FULL_SHA.fullmatch(admitted_sha) is not None, "invalid_input")
    require(_SAFE_ID.fullmatch(consumer) is not None, "invalid_input")
    try:
        profile = GitOpsProfile(profile_raw)
    except ValueError as error:
        raise GitOpsValidationError("invalid_input") from error
    change_base = environment.get("INPUT_CHANGE_BASE_SHA", "") or None
    if change_base is not None:
        require(_FULL_SHA.fullmatch(change_base) is not None, "invalid_input")
    policy = environment.get("INPUT_POLICY_SCRIPT_PROFILE", "") or None
    if policy is not None:
        require(_SAFE_ID.fullmatch(policy) is not None, "invalid_input")
    artifact = environment.get("INPUT_ARTIFACT_EXCEPTION_ID", "") or None
    require(artifact is None, "artifact_policy_failed")
    return GitOpsRequest(
        repository=repository,
        admitted_sha=admitted_sha,
        consumer_contract=consumer,
        validation_profile=profile,
        source_trust=source_trust_from_environment(environment),
        change_base_sha=change_base,
        policy_script_profile=policy,
        artifact_exception_id=artifact,
    )


def build_plan(
    contract: Mapping[str, Any],
    request: GitOpsRequest,
    source_root: Path | None,
) -> GitOpsPlan:
    require(_FULL_SHA.fullmatch(request.admitted_sha) is not None, "invalid_input")
    require(request.source_trust in _ALLOWED_TRUST, "invalid_input")
    consumers = contract["consumer_contracts"]
    require(request.consumer_contract in consumers, "consumer_contract_rejected")
    consumer = consumers[request.consumer_contract]
    require(consumer["repository"] == request.repository, "consumer_contract_rejected")
    profile_name = request.validation_profile.value
    require(profile_name in consumer["profiles"], "profile_consumer_mismatch")
    profile_contract = contract["profiles"][profile_name]
    require(request.source_trust in profile_contract["allowed_source_trust"], "source_trust_rejected")
    if request.validation_profile is GitOpsProfile.CHANGED_TREE:
        require(request.change_base_sha is not None, "change_base_required")
    else:
        require(request.change_base_sha is None, "invalid_input")
    selection = consumer["profiles"][profile_name]
    expected_policy = selection["policy_script"]
    require(request.policy_script_profile == expected_policy, "policy_profile_rejected")
    targets = tuple(_target(name, contract["targets"][name]) for name in selection["targets"])
    policy = (
        _policy(expected_policy, contract["policy_scripts"][expected_policy])
        if expected_policy is not None
        else None
    )
    required_tools = {"pyyaml"}
    if any(target.kind is GitOpsTargetKind.HELM for target in targets):
        required_tools.add("helm")
    if any(target.kind is GitOpsTargetKind.KUSTOMIZE for target in targets):
        required_tools.add("kustomize")
    tools = tuple(_tool(name, contract["tools"][name]) for name in sorted(required_tools))
    if source_root is not None:
        require(source_root.is_dir() and not source_root.is_symlink(), "invalid_path")
        for target in targets:
            bounded_path(source_root, target.root, must_exist=True, kind="directory")
            if target.schema_path:
                bounded_path(source_root, target.schema_path, must_exist=True, kind="file")
            if target.expected_render_path:
                bounded_path(source_root, target.expected_render_path, must_exist=True, kind="file")
            for relative in (*target.values_files, *target.sops_files):
                bounded_path(source_root, relative, must_exist=True, kind="file")
            for dependency in target.vendored_dependencies:
                bounded_path(source_root, dependency.path, must_exist=True, kind="directory")
        if policy:
            path = bounded_path(source_root, policy.path, must_exist=True, kind="file")
            require(file_sha256(path) == policy.sha256, "policy_profile_rejected")
    return GitOpsPlan(
        request=request,
        runner_profile="portable",
        workspace_profile="minimal",
        timeout_minutes=int(profile_contract["timeout_minutes"]),
        tools=tools,
        targets=targets,
        policy_script=policy,
    )
