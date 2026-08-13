"""Bounded GitHub Actions dependency-cache planning for self-hosted validation."""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

CONTRACT_PATH = Path("contracts/cache-policy.json")
_FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
_SAFE_TOKEN = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
_PHASES = {"restore", "save"}


class DependencyCacheError(RuntimeError):
    """Stable sanitized dependency-cache planning failure."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class DependencyCachePlan:
    phase: str
    family: str
    cache_key: str
    lock_digest: str
    cache_paths: tuple[str, ...]
    identity_file_count: int
    save_allowed: bool

    def outputs(self) -> dict[str, str]:
        return {
            "phase": self.phase,
            "family": self.family,
            "cache_key": self.cache_key,
            "lock_digest": self.lock_digest,
            "cache_paths": "\n".join(self.cache_paths),
            "identity_file_count": str(self.identity_file_count),
            "save_allowed": str(self.save_allowed).lower(),
        }


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise DependencyCacheError(code)


def _load_contract(contract_root: Path) -> Mapping[str, Any]:
    try:
        payload = json.loads((contract_root / CONTRACT_PATH).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise DependencyCacheError("cache_contract_invalid") from error
    _require(isinstance(payload, Mapping) and payload.get("schema_version") == 1, "cache_contract_invalid")
    native = payload.get("native_dependency_cache")
    _require(isinstance(native, Mapping), "cache_contract_invalid")
    _require(native.get("provider") == "github-actions-cache", "cache_contract_invalid")
    _require(native.get("exact_key_only") is True, "cache_contract_invalid")
    _require(native.get("save_policy") == "successful-protected-default-branch-push-only", "cache_contract_invalid")
    action_sha = native.get("action_sha")
    _require(isinstance(action_sha, str) and _FULL_SHA.fullmatch(action_sha) is not None, "cache_contract_invalid")
    families = native.get("families")
    _require(isinstance(families, Mapping) and families, "cache_contract_invalid")
    return payload


def _safe_relative(value: str, *, allow_dot: bool = False) -> str:
    _require(isinstance(value, str), "cache_path_invalid")
    candidate = value.strip()
    if allow_dot and candidate == ".":
        return "."
    path = PurePosixPath(candidate)
    _require(
        bool(candidate)
        and not path.is_absolute()
        and ".." not in path.parts
        and "\\" not in candidate
        and all(part not in {"", "."} for part in path.parts),
        "cache_path_invalid",
    )
    return path.as_posix()


def _working_root(source_root: Path, working_directory: str) -> Path:
    _require(source_root.is_dir() and not source_root.is_symlink(), "cache_source_invalid")
    resolved_source = source_root.resolve()
    relative = _safe_relative(working_directory, allow_dot=True)
    if relative == ".":
        return resolved_source
    current = resolved_source
    for part in PurePosixPath(relative).parts:
        current /= part
        _require(not current.is_symlink(), "cache_identity_symlink")
    target = current.resolve(strict=False)
    _require(resolved_source in target.parents and target.is_dir(), "cache_working_directory_invalid")
    return target


def _reject_symlink_components(root: Path, path: Path) -> None:
    relative = path.relative_to(root)
    current = root
    for part in relative.parts:
        current /= part
        _require(not current.is_symlink(), "cache_identity_symlink")


def _identity_digest(
    *,
    source_root: Path,
    working_root: Path,
    patterns: list[str],
    max_files: int,
    max_bytes: int,
) -> tuple[str, int]:
    files: dict[str, Path] = {}
    total_bytes = 0
    for pattern in patterns:
        _require(isinstance(pattern, str) and pattern and "\\" not in pattern, "cache_contract_invalid")
        for candidate in working_root.glob(pattern):
            if not candidate.is_file():
                continue
            _reject_symlink_components(working_root, candidate)
            resolved = candidate.resolve()
            _require(source_root == resolved or source_root in resolved.parents, "cache_identity_escape")
            relative = resolved.relative_to(source_root).as_posix()
            files[relative] = resolved
    _require(bool(files), "cache_identity_missing")
    _require(len(files) <= max_files, "cache_identity_too_many_files")
    digest = hashlib.sha256()
    for relative in sorted(files):
        path = files[relative]
        try:
            content = path.read_bytes()
        except OSError as error:
            raise DependencyCacheError("cache_identity_unreadable") from error
        total_bytes += len(content)
        _require(total_bytes <= max_bytes, "cache_identity_too_large")
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(content)
        digest.update(b"\0")
    return digest.hexdigest(), len(files)


def _cache_paths(
    *,
    source_root: Path,
    workflow_root: Path,
    rows: list[Mapping[str, Any]],
    environment: Mapping[str, str],
    max_paths: int,
) -> tuple[str, ...]:
    _require(workflow_root.is_absolute() and workflow_root.is_dir(), "cache_workflow_root_invalid")
    resolved_workflow = workflow_root.resolve()
    result: list[str] = []
    for row in rows:
        _require(isinstance(row, Mapping), "cache_contract_invalid")
        base_name = row.get("base_env")
        relative = row.get("relative")
        _require(isinstance(base_name, str) and _SAFE_TOKEN.fullmatch(base_name) is not None, "cache_contract_invalid")
        _require(isinstance(relative, str), "cache_contract_invalid")
        raw_base = environment.get(base_name, "")
        _require(bool(raw_base), "cache_path_environment_missing")
        base = Path(raw_base)
        _require(base.is_absolute(), "cache_path_environment_invalid")
        clean_relative = _safe_relative(relative, allow_dot=True)
        target = base if clean_relative == "." else base.joinpath(*PurePosixPath(clean_relative).parts)
        resolved = target.resolve(strict=False)
        _require(resolved == resolved_workflow or resolved_workflow in resolved.parents, "cache_path_outside_workflow_state")
        resolved_source = source_root.resolve()
        _require(not (resolved == resolved_source or resolved_source in resolved.parents), "cache_source_path_forbidden")
        result.append(str(resolved))
    result = sorted(set(result))
    _require(bool(result) and len(result) <= max_paths, "cache_path_count_invalid")
    return tuple(result)


def _default_branch(environment: Mapping[str, str]) -> str:
    event_path = environment.get("GITHUB_EVENT_PATH", "")
    _require(bool(event_path), "cache_event_invalid")
    try:
        payload = json.loads(Path(event_path).read_text(encoding="utf-8"))
        repository = payload["repository"]
        default_branch = repository["default_branch"]
        full_name = repository["full_name"]
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as error:
        raise DependencyCacheError("cache_event_invalid") from error
    _require(full_name == environment.get("GITHUB_REPOSITORY"), "cache_event_repository_mismatch")
    _require(isinstance(default_branch, str) and _SAFE_TOKEN.fullmatch(default_branch) is not None, "cache_event_invalid")
    return default_branch


def _save_allowed(
    *,
    source_trust: str,
    validation_succeeded: bool,
    environment: Mapping[str, str],
) -> bool:
    if not validation_succeeded or source_trust != "trusted-exact":
        return False
    if environment.get("GITHUB_EVENT_NAME") != "push":
        return False
    if environment.get("GITHUB_REF_PROTECTED") != "true":
        return False
    default_branch = _default_branch(environment)
    return environment.get("GITHUB_REF") == f"refs/heads/{default_branch}"


def _slug(value: str) -> str:
    candidate = re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip("-.")
    _require(bool(candidate), "cache_key_material_invalid")
    return candidate[:64]


def plan_dependency_cache(
    *,
    contract_root: Path,
    source_root: Path,
    working_directory: str,
    phase: str,
    family: str,
    source_trust: str,
    profile: str,
    validation_succeeded: bool,
    environment: Mapping[str, str],
) -> DependencyCachePlan:
    """Return one exact-lock native-cache plan without caller-selected paths or keys."""

    contract = _load_contract(contract_root)
    native = contract["native_dependency_cache"]
    families = native["families"]
    _require(phase in _PHASES, "cache_phase_unsupported")
    _require(family in families, "cache_family_unsupported")
    _require(isinstance(profile, str) and _SAFE_TOKEN.fullmatch(profile) is not None, "cache_profile_invalid")
    allowed_restore = contract.get("allowed_trust_modes", {}).get("restore-only", [])
    _require(source_trust in allowed_restore, "cache_trust_forbidden")
    if phase == "save":
        allowed_save = contract.get("allowed_trust_modes", {}).get("read-write", [])
        _require(source_trust in allowed_save, "cache_save_trust_forbidden")
    repository = environment.get("GITHUB_REPOSITORY", "")
    _require(bool(repository) and "/" in repository, "cache_repository_invalid")
    runner_os = environment.get("RUNNER_OS", "")
    runner_arch = environment.get("RUNNER_ARCH", "")
    _require(bool(runner_os) and bool(runner_arch), "cache_runner_invalid")
    resolved_source = source_root.resolve()
    working_root = _working_root(source_root, working_directory)
    family_contract = families[family]
    patterns = family_contract.get("identity_globs")
    rows = family_contract.get("paths")
    _require(isinstance(patterns, list) and patterns and all(isinstance(value, str) for value in patterns), "cache_contract_invalid")
    _require(isinstance(rows, list) and rows, "cache_contract_invalid")
    lock_digest, file_count = _identity_digest(
        source_root=resolved_source,
        working_root=working_root,
        patterns=list(patterns),
        max_files=int(native.get("max_identity_files", 0)),
        max_bytes=int(native.get("max_identity_bytes", 0)),
    )
    workflow_root_raw = environment.get("CI_WORKFLOW_ROOT", "")
    _require(bool(workflow_root_raw), "cache_workflow_root_invalid")
    paths = _cache_paths(
        source_root=resolved_source,
        workflow_root=Path(workflow_root_raw),
        rows=list(rows),
        environment=environment,
        max_paths=int(native.get("max_paths", 0)),
    )
    repository_hash = hashlib.sha256(repository.encode("utf-8")).hexdigest()[:12]
    key = "-".join(
        [
            "ci-deps",
            str(native.get("key_version")),
            _slug(family),
            _slug(runner_os.lower()),
            _slug(runner_arch.lower()),
            _slug(profile),
            repository_hash,
            lock_digest,
        ]
    )
    _require(len(key) <= int(contract.get("max_key_length", 0)), "cache_key_too_long")
    save_allowed = phase == "save" and _save_allowed(
        source_trust=source_trust,
        validation_succeeded=validation_succeeded,
        environment=environment,
    )
    return DependencyCachePlan(
        phase=phase,
        family=family,
        cache_key=key,
        lock_digest=lock_digest,
        cache_paths=paths,
        identity_file_count=file_count,
        save_allowed=save_allowed,
    )


def _write_outputs(path: Path, outputs: Mapping[str, str]) -> None:
    with path.open("a", encoding="utf-8") as handle:
        for name, value in outputs.items():
            if "\n" in value:
                delimiter = f"CIW_{name.upper()}_EOF"
                handle.write(f"{name}<<{delimiter}\n{value}\n{delimiter}\n")
            else:
                handle.write(f"{name}={value}\n")


def main() -> int:
    contract_root = Path(__file__).resolve().parents[2]
    try:
        plan = plan_dependency_cache(
            contract_root=contract_root,
            source_root=Path(os.environ.get("INPUT_SOURCE_ROOT", "source")),
            working_directory=os.environ.get("INPUT_WORKING_DIRECTORY", "."),
            phase=os.environ.get("INPUT_PHASE", ""),
            family=os.environ.get("INPUT_FAMILY", ""),
            source_trust=os.environ.get("INPUT_SOURCE_TRUST", ""),
            profile=os.environ.get("INPUT_PROFILE", ""),
            validation_succeeded=os.environ.get("INPUT_VALIDATION_SUCCEEDED", "false") == "true",
            environment=os.environ,
        )
    except DependencyCacheError as error:
        print(f"dependency-cache:{error.code}", file=sys.stderr)
        return 2
    output_path = os.environ.get("GITHUB_OUTPUT", "")
    if output_path:
        _write_outputs(Path(output_path), plan.outputs())
    else:
        print(json.dumps(plan.outputs(), sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
