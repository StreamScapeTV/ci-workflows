"""Workflow-scoped workspace preparation, path registration, and cleanup."""
from __future__ import annotations

import json
import os
import shutil
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from .foundation_types import (
    FoundationError,
    atomic_write_json,
    bounded_path,
    canonical_json,
    ensure_no_symlink_escape,
    full_sha,
    load_contract,
    repository_name,
    require,
    safe_id,
    safe_relative_path,
    sha256_hex,
    stable_identifier,
)

WORKSPACE_CONTRACT = "contracts/workspace-paths.json"
CACHE_CONTRACT = "contracts/cache-policy.json"


@dataclass(frozen=True)
class WorkspaceContext:
    workspace: Path
    runner_temp: Path
    repository: str
    run_id: str
    run_attempt: int
    job: str
    runner_os: str


@dataclass(frozen=True)
class RegisteredPath:
    name: str
    relative: str
    kind: str


@dataclass(frozen=True)
class WorkspaceState:
    state_id: str
    root: Path
    profile: str
    cache_mode: str
    cache_key: str | None
    environment: Mapping[str, str]
    registered_paths: tuple[RegisteredPath, ...]

    def output_values(self) -> dict[str, str]:
        return {
            "state_id": self.state_id,
            "profile": self.profile,
            "cache_mode": self.cache_mode,
            "cache_key": self.cache_key or "",
            "registered_path_count": str(len(self.registered_paths)),
        }


@dataclass(frozen=True)
class CleanupReport:
    state_id: str
    removed_paths: int
    removed_sensitive_paths: int
    partial_setup: bool
    platform: str

    def output_values(self) -> dict[str, str]:
        return {
            "state_id": self.state_id,
            "removed_paths": str(self.removed_paths),
            "removed_sensitive_paths": str(self.removed_sensitive_paths),
            "partial_setup": "true" if self.partial_setup else "false",
            "platform": self.platform,
            "cleanup_verified": "true",
        }


def _contract_paths(contract: Mapping[str, Any], profile: str) -> list[RegisteredPath]:
    profiles = contract.get("profiles")
    require(isinstance(profiles, dict) and profile in profiles, "unsupported_workspace_profile")
    result: dict[str, RegisteredPath] = {}

    def add_entries(entries: Any) -> None:
        require(isinstance(entries, list), "workspace_contract_invalid")
        for raw in entries:
            require(isinstance(raw, dict), "workspace_contract_invalid")
            name = safe_id(raw.get("name"), "invalid_registered_path_name")
            relative = safe_relative_path(raw.get("relative"))
            kind = safe_id(raw.get("kind"), "invalid_registered_path_kind")
            require(name not in result, "duplicate_registered_path")
            result[name] = RegisteredPath(name=name, relative=relative, kind=kind)

    add_entries(contract.get("base_paths"))
    seen_profiles: set[str] = set()

    def add_profile(identifier: str) -> None:
        require(identifier not in seen_profiles, "workspace_profile_cycle")
        seen_profiles.add(identifier)
        raw_profile = profiles.get(identifier)
        require(isinstance(raw_profile, dict), "workspace_contract_invalid")
        parents = raw_profile.get("extends", [])
        require(isinstance(parents, list), "workspace_contract_invalid")
        for parent in parents:
            require(isinstance(parent, str) and parent in profiles, "workspace_contract_invalid")
            add_profile(parent)
        add_entries(raw_profile.get("paths", []))

    add_profile(profile)
    return list(result.values())


def _profile_environment(
    contract: Mapping[str, Any],
    profile: str,
    paths: Mapping[str, Path],
) -> dict[str, str]:
    profiles = contract["profiles"]
    result: dict[str, str] = {}
    seen: set[str] = set()

    def add_profile(identifier: str) -> None:
        if identifier in seen:
            return
        seen.add(identifier)
        raw = profiles[identifier]
        for parent in raw.get("extends", []):
            add_profile(parent)
        environment = raw.get("environment", {})
        require(isinstance(environment, dict), "workspace_contract_invalid")
        for variable, target in environment.items():
            require(
                isinstance(variable, str)
                and variable
                and isinstance(target, str)
                and target,
                "workspace_contract_invalid",
            )
            name, separator, suffix = target.partition("/")
            require(name in paths, "workspace_contract_invalid")
            path = paths[name]
            if separator:
                suffix = safe_relative_path(suffix)
                path = bounded_path(path, suffix)
            result[variable] = str(path)

    add_profile(profile)
    return result


def _cache_settings(
    contract_root: Path,
    *,
    mode: str,
    repository: str,
    source_sha: str | None,
    lock_digest: str | None,
    runner_os: str,
    profile: str,
    trust_mode: str | None,
) -> tuple[str, str | None]:
    contract = load_contract(contract_root, CACHE_CONTRACT)
    modes = contract.get("modes")
    require(isinstance(modes, dict) and mode in modes, "unsupported_cache_mode")
    if mode == contract.get("default_mode") == "disabled":
        return mode, None
    require(source_sha is not None and lock_digest is not None, "cache_key_material_required")
    source_sha = full_sha(source_sha, "cache_source_sha_required")
    lock_digest = sha256_hex(lock_digest, "cache_lock_digest_required")
    allowed = contract.get("allowed_trust_modes", {}).get(mode)
    require(isinstance(allowed, list) and trust_mode in allowed, "cache_trust_scope_forbidden")
    material = {
        "repository": repository_name(repository),
        "source_sha": source_sha,
        "lock_digest": lock_digest,
        "platform": runner_os,
        "profile": profile,
    }
    key = stable_identifier("cache", material, length=32)
    require(len(key) <= int(contract.get("max_key_length", 0)), "cache_key_too_long")
    return mode, key


def _state_id(context: WorkspaceContext) -> str:
    return stable_identifier(
        "workspace",
        {
            "repository": repository_name(context.repository),
            "run_id": str(context.run_id),
            "run_attempt": context.run_attempt,
            "job": context.job,
        },
    )


def _state_files(root: Path, contract: Mapping[str, Any]) -> tuple[Path, Path]:
    marker_name = safe_relative_path(contract.get("marker_file"))
    registry_name = safe_relative_path(contract.get("registry_file"))
    return root / marker_name, root / registry_name


def _write_state(
    marker: Path,
    registry: Path,
    *,
    state_id: str,
    context: WorkspaceContext,
    root: Path,
    profile: str,
    status: str,
    paths: Sequence[RegisteredPath],
) -> None:
    marker_payload = {
        "schema_version": 1,
        "state_id": state_id,
        "root": str(root),
        "runner_temp": str(context.runner_temp.resolve()),
        "workspace": str(context.workspace.resolve()),
        "repository": context.repository,
        "run_id": str(context.run_id),
        "run_attempt": context.run_attempt,
        "job": context.job,
        "runner_os": context.runner_os,
        "profile": profile,
        "status": status,
    }
    atomic_write_json(marker, marker_payload)
    if status == "prepared":
        atomic_write_json(
            registry,
            {
                "schema_version": 1,
                "state_id": state_id,
                "root": str(root),
                "paths": [
                    {"name": item.name, "relative": item.relative, "kind": item.kind}
                    for item in paths
                ],
            },
        )


def prepare_workspace(
    context: WorkspaceContext,
    *,
    profile: str,
    cache_mode: str = "disabled",
    source_sha: str | None = None,
    lock_digest: str | None = None,
    trust_mode: str | None = None,
    contract_root: Path,
) -> WorkspaceState:
    """Create one isolated workflow-scoped state root and strict environment."""

    contract = load_contract(contract_root, WORKSPACE_CONTRACT)
    repository_name(context.repository)
    require(context.runner_os in contract.get("supported_os", []), "unsupported_runner_os")
    require(context.run_attempt >= 1, "invalid_run_attempt")
    require(bool(str(context.run_id).strip()) and bool(context.job.strip()), "invalid_workflow_identity")
    workspace = context.workspace.resolve()
    runner_temp = context.runner_temp.resolve()
    require(workspace.is_dir() and runner_temp.is_dir(), "workspace_context_unavailable")
    require(not context.workspace.is_symlink() and not context.runner_temp.is_symlink(), "symlink_escape_detected")

    paths = _contract_paths(contract, profile)
    state_id = _state_id(context)
    state_parent = runner_temp / safe_id(contract.get("state_root_directory"), "workspace_contract_invalid")
    state_parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    root = state_parent / state_id
    require(not root.exists(), "workspace_state_already_exists")
    root.mkdir(mode=0o700)
    marker, registry = _state_files(root, contract)
    prepared_context = WorkspaceContext(
        workspace=workspace,
        runner_temp=runner_temp,
        repository=context.repository,
        run_id=str(context.run_id),
        run_attempt=context.run_attempt,
        job=context.job,
        runner_os=context.runner_os,
    )
    _write_state(
        marker,
        registry,
        state_id=state_id,
        context=prepared_context,
        root=root,
        profile=profile,
        status="preparing",
        paths=(),
    )

    try:
        resolved_paths: dict[str, Path] = {}
        for item in paths:
            target = bounded_path(root, item.relative)
            ensure_no_symlink_escape(root, target)
            target.mkdir(mode=0o700, parents=True, exist_ok=False)
            resolved_paths[item.name] = target
        _write_state(
            marker,
            registry,
            state_id=state_id,
            context=prepared_context,
            root=root,
            profile=profile,
            status="prepared",
            paths=paths,
        )
    except BaseException:
        # The marker deliberately remains so cleanup can safely terminalize a
        # partial or interrupted setup without accepting an arbitrary path.
        raise

    cache_mode, cache_key = _cache_settings(
        contract_root,
        mode=cache_mode,
        repository=context.repository,
        source_sha=source_sha,
        lock_digest=lock_digest,
        runner_os=context.runner_os,
        profile=profile,
        trust_mode=trust_mode,
    )
    locale = contract.get("locale_by_os", {}).get(context.runner_os)
    require(isinstance(locale, str) and locale, "workspace_contract_invalid")
    environment = {
        "CI_WORKFLOW_STATE_ID": state_id,
        "CI_WORKFLOW_ROOT": str(root),
        "CI_WORKFLOW_REGISTRY": str(registry),
        "CI_WORKFLOW_PROFILE": profile,
        "CI_CACHE_MODE": cache_mode,
        "CI_CACHE_KEY": cache_key or "",
        "LC_ALL": locale,
        "LANG": locale,
        "TZ": "UTC",
        "HOME": str(resolved_paths["home"]),
        "TMPDIR": str(resolved_paths["tmp"]),
        "XDG_CACHE_HOME": str(resolved_paths["xdg_cache"]),
        "XDG_CONFIG_HOME": str(resolved_paths["xdg_config"]),
        "XDG_DATA_HOME": str(resolved_paths["xdg_data"]),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_TERMINAL_PROMPT": "0",
        "CI_CREDENTIAL_ROOT": str(resolved_paths["credentials"]),
        "CI_EVIDENCE_ROOT": str(resolved_paths["evidence"]),
        "CI_ARTIFACT_ROOT": str(resolved_paths["artifacts"]),
        "CI_GENERATED_ROOT": str(resolved_paths["generated"]),
        "CI_TOOL_ROOT": str(resolved_paths["tools"]),
        "CI_DEPENDENCY_ROOT": str(resolved_paths["dependencies"]),
    }
    environment.update(_profile_environment(contract, profile, resolved_paths))
    return WorkspaceState(
        state_id=state_id,
        root=root,
        profile=profile,
        cache_mode=cache_mode,
        cache_key=cache_key,
        environment=environment,
        registered_paths=tuple(paths),
    )


def _load_state(root: Path, contract_root: Path) -> tuple[Mapping[str, Any], Mapping[str, Any] | None, Mapping[str, Any]]:
    contract = load_contract(contract_root, WORKSPACE_CONTRACT)
    require(root.is_absolute(), "workspace_root_must_be_absolute")
    require(not root.is_symlink(), "symlink_escape_detected")
    marker_path, registry_path = _state_files(root, contract)
    try:
        marker = json.loads(marker_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise FoundationError("workspace_marker_unavailable") from error
    require(isinstance(marker, dict) and marker.get("schema_version") == 1, "workspace_marker_invalid")
    state_id = safe_id(marker.get("state_id"), "workspace_marker_invalid")
    runner_temp = Path(str(marker.get("runner_temp", ""))).resolve()
    expected_parent = runner_temp / safe_id(contract.get("state_root_directory"), "workspace_contract_invalid")
    require(root.resolve() == expected_parent / state_id, "unsafe_cleanup_target")
    require(str(root.resolve()) == marker.get("root"), "workspace_marker_invalid")
    registry: Mapping[str, Any] | None = None
    if registry_path.exists():
        try:
            loaded = json.loads(registry_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise FoundationError("workspace_registry_invalid") from error
        require(isinstance(loaded, dict) and loaded.get("schema_version") == 1, "workspace_registry_invalid")
        require(loaded.get("state_id") == state_id and loaded.get("root") == str(root.resolve()), "workspace_registry_invalid")
        registry = loaded
    return marker, registry, contract


def register_state_path(
    state_root: Path,
    *,
    name: str,
    relative: str,
    kind: str,
    contract_root: Path,
    create: bool = True,
) -> Path:
    """Register one bounded dynamic path beneath a reviewed state root."""

    marker, registry, contract = _load_state(state_root, contract_root)
    require(registry is not None and marker.get("status") == "prepared", "workspace_not_prepared")
    name = safe_id(name, "invalid_registered_path_name")
    relative = safe_relative_path(relative)
    kind = safe_id(kind, "invalid_registered_path_kind")
    dynamic_roots = set(str(value) for value in contract.get("dynamic_roots", []))
    require(relative.split("/", 1)[0] in dynamic_roots, "unapproved_dynamic_path")
    paths = registry.get("paths")
    require(isinstance(paths, list), "workspace_registry_invalid")
    require(all(isinstance(item, dict) for item in paths), "workspace_registry_invalid")
    require(name not in {str(item.get("name")) for item in paths}, "duplicate_registered_path")
    target = bounded_path(state_root, relative)
    ensure_no_symlink_escape(state_root, target)
    if create:
        target.mkdir(mode=0o700, parents=True, exist_ok=False)
    paths.append({"name": name, "relative": relative, "kind": kind})
    _, registry_path = _state_files(state_root, contract)
    atomic_write_json(registry_path, dict(registry))
    return target


def _chmod_writable(path: Path) -> None:
    try:
        path.chmod(path.stat().st_mode | stat.S_IWUSR | stat.S_IXUSR)
    except OSError:
        return


def _remove_tree(path: Path, runner_os: str) -> None:
    if not path.exists():
        return
    require(not path.is_symlink(), "symlink_escape_detected")
    # Both Linux and macOS can leave read-only files behind. macOS additionally
    # commonly needs writable directory traversal after build tools finish.
    if runner_os == "macOS":
        for candidate in sorted(path.rglob("*"), reverse=True):
            _chmod_writable(candidate)
    _chmod_writable(path)
    shutil.rmtree(path, onerror=lambda _fn, candidate, _exc: _chmod_writable(Path(candidate)))


def remove_registered_path(
    state_root: Path,
    *,
    name: str,
    contract_root: Path,
) -> bool:
    marker, registry, contract = _load_state(state_root, contract_root)
    require(registry is not None and marker.get("status") == "prepared", "workspace_not_prepared")
    name = safe_id(name, "invalid_registered_path_name")
    paths = registry.get("paths")
    require(isinstance(paths, list), "workspace_registry_invalid")
    entry = next((item for item in paths if isinstance(item, dict) and item.get("name") == name), None)
    require(isinstance(entry, dict), "registered_path_not_found")
    target = bounded_path(state_root, str(entry.get("relative", "")))
    ensure_no_symlink_escape(state_root, target)
    existed = target.exists()
    _remove_tree(target, str(marker.get("runner_os")))
    require(not target.exists(), "cleanup_residue_detected")
    return existed


def cleanup_workspace(
    state_root: Path,
    *,
    expected_state_id: str | None,
    contract_root: Path,
) -> CleanupReport:
    """Remove only marker-bound registered state and verify zero residue."""

    marker, registry, contract = _load_state(state_root, contract_root)
    state_id = safe_id(marker.get("state_id"), "workspace_marker_invalid")
    if expected_state_id is not None:
        require(state_id == safe_id(expected_state_id), "workspace_state_id_mismatch")
    runner_os = str(marker.get("runner_os"))
    require(runner_os in contract.get("supported_os", []), "unsupported_runner_os")
    sensitive_kinds = set(str(value) for value in contract.get("sensitive_kinds", []))
    partial_setup = registry is None
    entries: list[Mapping[str, Any]] = []
    if registry is not None:
        raw_entries = registry.get("paths")
        require(isinstance(raw_entries, list), "workspace_registry_invalid")
        for raw in raw_entries:
            require(isinstance(raw, dict), "workspace_registry_invalid")
            entries.append(raw)

    # Validate every deletion target before deleting anything. A malicious
    # registry therefore cannot cause partial deletion outside the state root.
    validated: list[tuple[Mapping[str, Any], Path]] = []
    for entry in entries:
        safe_id(entry.get("name"), "workspace_registry_invalid")
        safe_id(entry.get("kind"), "workspace_registry_invalid")
        target = bounded_path(state_root, str(entry.get("relative", "")), "unsafe_cleanup_target")
        ensure_no_symlink_escape(state_root, target)
        validated.append((entry, target))

    removed = 0
    removed_sensitive = 0
    for entry, target in sorted(validated, key=lambda item: len(item[1].parts), reverse=True):
        if target.exists():
            removed += 1
            if str(entry.get("kind")) in sensitive_kinds:
                removed_sensitive += 1
        _remove_tree(target, runner_os)
        require(not target.exists(), "cleanup_residue_detected")

    _remove_tree(state_root, runner_os)
    require(not state_root.exists(), "cleanup_residue_detected")
    return CleanupReport(
        state_id=state_id,
        removed_paths=removed,
        removed_sensitive_paths=removed_sensitive,
        partial_setup=partial_setup,
        platform=runner_os,
    )
