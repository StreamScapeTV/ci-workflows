"""Workflow-scoped workspace preparation, path registration, and cleanup."""
from __future__ import annotations

import json
import shutil
import stat
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence

from .foundation_types import (
    FoundationError,
    atomic_write_json,
    bounded_path,
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
            "partial_setup": str(self.partial_setup).lower(),
            "platform": self.platform,
            "cleanup_verified": "true",
        }


def _path_entry(raw: Any) -> RegisteredPath:
    require(isinstance(raw, dict), "workspace_contract_invalid")
    return RegisteredPath(
        name=safe_id(raw.get("name"), "invalid_registered_path_name"),
        relative=safe_relative_path(raw.get("relative")),
        kind=safe_id(raw.get("kind"), "invalid_registered_path_kind"),
    )


def _profile_order(contract: Mapping[str, Any], profile: str) -> list[str]:
    profiles = contract.get("profiles")
    require(isinstance(profiles, dict) and profile in profiles, "unsupported_workspace_profile")
    result: list[str] = []
    active: set[str] = set()
    complete: set[str] = set()

    def visit(identifier: str) -> None:
        require(identifier not in active, "workspace_profile_cycle")
        if identifier in complete:
            return
        raw = profiles.get(identifier)
        require(isinstance(raw, dict), "workspace_contract_invalid")
        parents = raw.get("extends", [])
        require(
            isinstance(parents, list)
            and all(isinstance(value, str) and value in profiles for value in parents),
            "workspace_contract_invalid",
        )
        active.add(identifier)
        for parent in parents:
            visit(parent)
        active.remove(identifier)
        complete.add(identifier)
        result.append(identifier)

    visit(profile)
    return result


def _contract_paths(contract: Mapping[str, Any], profile: str) -> list[RegisteredPath]:
    raw_base = contract.get("base_paths")
    require(isinstance(raw_base, list), "workspace_contract_invalid")
    entries = [_path_entry(value) for value in raw_base]
    profiles = contract["profiles"]
    for identifier in _profile_order(contract, profile):
        raw_paths = profiles[identifier].get("paths", [])
        require(isinstance(raw_paths, list), "workspace_contract_invalid")
        entries.extend(_path_entry(value) for value in raw_paths)
    names = [entry.name for entry in entries]
    require(len(names) == len(set(names)), "duplicate_registered_path")
    return entries


def _profile_environment(
    contract: Mapping[str, Any],
    profile: str,
    paths: Mapping[str, Path],
) -> dict[str, str]:
    result: dict[str, str] = {}
    for identifier in _profile_order(contract, profile):
        raw = contract["profiles"][identifier].get("environment", {})
        require(isinstance(raw, dict), "workspace_contract_invalid")
        for variable, value in raw.items():
            require(isinstance(variable, str) and isinstance(value, str), "workspace_contract_invalid")
            name, separator, suffix = value.partition("/")
            require(name in paths, "workspace_contract_invalid")
            target = paths[name]
            if separator:
                target = bounded_path(target, safe_relative_path(suffix))
            result[variable] = str(target)
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
    material = {
        "repository": repository_name(repository),
        "source_sha": full_sha(source_sha, "cache_source_sha_required"),
        "lock_digest": sha256_hex(lock_digest, "cache_lock_digest_required"),
        "platform": runner_os,
        "profile": profile,
    }
    allowed = contract.get("allowed_trust_modes", {}).get(mode)
    require(isinstance(allowed, list) and trust_mode in allowed, "cache_trust_scope_forbidden")
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
    return (
        root / safe_relative_path(contract.get("marker_file")),
        root / safe_relative_path(contract.get("registry_file")),
    )


def _write_marker(
    path: Path,
    *,
    state_id: str,
    context: WorkspaceContext,
    root: Path,
    profile: str,
    status: str,
) -> None:
    atomic_write_json(
        path,
        {
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
        },
    )


def _write_registry(path: Path, root: Path, state_id: str, entries: Sequence[RegisteredPath]) -> None:
    atomic_write_json(
        path,
        {
            "schema_version": 1,
            "state_id": state_id,
            "root": str(root),
            "paths": [entry.__dict__ for entry in entries],
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
    require(context.run_attempt >= 1 and str(context.run_id).strip() and context.job.strip(), "invalid_workflow_identity")
    workspace = context.workspace.resolve()
    runner_temp = context.runner_temp.resolve()
    require(workspace.is_dir() and runner_temp.is_dir(), "workspace_context_unavailable")
    require(not context.workspace.is_symlink() and not context.runner_temp.is_symlink(), "symlink_escape_detected")

    entries = _contract_paths(contract, profile)
    state_id = _state_id(context)
    parent = runner_temp / safe_id(contract.get("state_root_directory"), "workspace_contract_invalid")
    parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    root = parent / state_id
    require(not root.exists(), "workspace_state_already_exists")
    root.mkdir(mode=0o700)
    marker, registry = _state_files(root, contract)
    prepared_context = WorkspaceContext(
        workspace, runner_temp, context.repository, str(context.run_id),
        context.run_attempt, context.job, context.runner_os,
    )
    _write_marker(
        marker,
        state_id=state_id,
        context=prepared_context,
        root=root,
        profile=profile,
        status="preparing",
    )

    resolved: dict[str, Path] = {}
    try:
        for entry in entries:
            target = bounded_path(root, entry.relative)
            ensure_no_symlink_escape(root, target)
            target.mkdir(mode=0o700, parents=True, exist_ok=False)
            resolved[entry.name] = target
        _write_registry(registry, root, state_id, entries)
        _write_marker(
            marker,
            state_id=state_id,
            context=prepared_context,
            root=root,
            profile=profile,
            status="prepared",
        )
    except BaseException:
        # Marker retention allows a later always() cleanup to terminalize an
        # interrupted setup without accepting a caller-selected path.
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
        "HOME": str(resolved["home"]),
        "TMPDIR": str(resolved["tmp"]),
        "XDG_CACHE_HOME": str(resolved["xdg_cache"]),
        "XDG_CONFIG_HOME": str(resolved["xdg_config"]),
        "XDG_DATA_HOME": str(resolved["xdg_data"]),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_TERMINAL_PROMPT": "0",
        "CI_CREDENTIAL_ROOT": str(resolved["credentials"]),
        "CI_EVIDENCE_ROOT": str(resolved["evidence"]),
        "CI_ARTIFACT_ROOT": str(resolved["artifacts"]),
        "CI_GENERATED_ROOT": str(resolved["generated"]),
        "CI_TOOL_ROOT": str(resolved["tools"]),
        "CI_DEPENDENCY_ROOT": str(resolved["dependencies"]),
    }
    environment.update(_profile_environment(contract, profile, resolved))
    return WorkspaceState(
        state_id, root, profile, cache_mode, cache_key, environment, tuple(entries)
    )


def resolve_state_root(
    *,
    runner_temp: Path,
    state_id: str,
    declared_root: str,
    contract_root: Path,
) -> Path:
    """Derive state beneath protected runner temp and reject path substitution."""

    contract = load_contract(contract_root, WORKSPACE_CONTRACT)
    state_id = safe_id(state_id, "workspace_state_id_invalid")
    require(runner_temp.is_absolute(), "runner_temp_must_be_absolute")
    require(not runner_temp.is_symlink(), "symlink_escape_detected")
    resolved_temp = runner_temp.resolve()
    require(resolved_temp.is_dir(), "workspace_context_unavailable")
    parent = resolved_temp / safe_id(
        contract.get("state_root_directory"),
        "workspace_contract_invalid",
    )
    derived = parent / state_id
    require(
        bool(declared_root) and Path(declared_root).resolve() == derived,
        "workspace_root_environment_mismatch",
    )
    return derived


def _load_state(
    root: Path,
    contract_root: Path,
) -> tuple[Mapping[str, Any], Mapping[str, Any] | None, Mapping[str, Any]]:
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
    expected = runner_temp / safe_id(contract.get("state_root_directory"), "workspace_contract_invalid") / state_id
    require(root.resolve() == expected, "unsafe_cleanup_target")
    require(marker.get("root") == str(root.resolve()), "workspace_marker_invalid")

    registry: Mapping[str, Any] | None = None
    if registry_path.exists():
        try:
            candidate = json.loads(registry_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise FoundationError("workspace_registry_invalid") from error
        require(
            isinstance(candidate, dict)
            and candidate.get("schema_version") == 1
            and candidate.get("state_id") == state_id
            and candidate.get("root") == str(root.resolve()),
            "workspace_registry_invalid",
        )
        registry = candidate
    return marker, registry, contract


def _lexical_target(root: Path, relative: str) -> Path:
    relative = safe_relative_path(relative)
    target = root.resolve() / Path(*PurePosixPath(relative).parts)
    ensure_no_symlink_escape(root, target)
    return bounded_path(root, relative)


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
    require(
        relative.split("/", 1)[0] in set(contract.get("dynamic_roots", [])),
        "unapproved_dynamic_path",
    )
    raw_paths = registry.get("paths")
    require(isinstance(raw_paths, list), "workspace_registry_invalid")
    entries = [_path_entry(value) for value in raw_paths]
    require(name not in {entry.name for entry in entries}, "duplicate_registered_path")
    target = _lexical_target(state_root, relative)
    if create:
        target.mkdir(mode=0o700, parents=True, exist_ok=False)
    entries.append(RegisteredPath(name, relative, kind))
    _, registry_path = _state_files(state_root, contract)
    _write_registry(registry_path, state_root.resolve(), marker["state_id"], entries)
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
    if runner_os == "macOS":
        for candidate in sorted(path.rglob("*"), reverse=True):
            _chmod_writable(candidate)
    _chmod_writable(path)

    def recover(function: Any, candidate: str, _error: Any) -> None:
        target = Path(candidate)
        _chmod_writable(target)
        try:
            function(candidate)
        except OSError as error:
            raise FoundationError("cleanup_residue_detected") from error

    shutil.rmtree(path, onerror=recover)


def remove_registered_path(
    state_root: Path,
    *,
    name: str,
    contract_root: Path,
) -> bool:
    marker, registry, _contract = _load_state(state_root, contract_root)
    require(registry is not None and marker.get("status") == "prepared", "workspace_not_prepared")
    name = safe_id(name, "invalid_registered_path_name")
    raw_paths = registry.get("paths")
    require(isinstance(raw_paths, list), "workspace_registry_invalid")
    entry = next((_path_entry(value) for value in raw_paths if isinstance(value, dict) and value.get("name") == name), None)
    require(entry is not None, "registered_path_not_found")
    target = _lexical_target(state_root, entry.relative)
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
    partial_setup = registry is None
    entries: list[RegisteredPath] = []
    if registry is not None:
        raw_paths = registry.get("paths")
        require(isinstance(raw_paths, list), "workspace_registry_invalid")
        entries = [_path_entry(value) for value in raw_paths]

    # Validate every target before deleting anything, so one malicious registry
    # entry cannot turn cleanup into a partial arbitrary deletion primitive.
    validated = [(entry, _lexical_target(state_root, entry.relative)) for entry in entries]
    sensitive = set(contract.get("sensitive_kinds", []))
    removed = 0
    removed_sensitive = 0
    for entry, target in sorted(validated, key=lambda value: len(value[1].parts), reverse=True):
        if target.exists():
            removed += 1
            removed_sensitive += int(entry.kind in sensitive)
        _remove_tree(target, runner_os)
        require(not target.exists(), "cleanup_residue_detected")

    _remove_tree(state_root, runner_os)
    require(not state_root.exists(), "cleanup_residue_detected")
    return CleanupReport(
        state_id, removed, removed_sensitive, partial_setup, runner_os
    )
