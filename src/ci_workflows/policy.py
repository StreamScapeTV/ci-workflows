"""Tracked-secret, forbidden-file, clean-tree, generated, artifact, and cache policy."""
from __future__ import annotations

import fnmatch
import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence

from .foundation_types import (
    FoundationError,
    FULL_SHA,
    SHA256,
    load_contract,
    repository_name,
    require,
    safe_id,
    safe_relative_path,
    stable_identifier,
)

REPOSITORY_POLICY = "contracts/repository-policy.json"
ARTIFACT_EXCEPTIONS = "contracts/artifact-exceptions.json"
CACHE_POLICY = "contracts/cache-policy.json"


@dataclass(frozen=True)
class ArtifactDeclaration:
    name: str
    size_bytes: int
    retention_days: int


@dataclass(frozen=True)
class CacheDecision:
    mode: str
    restore: bool
    save: bool
    key: str | None


@dataclass(frozen=True)
class PolicyReport:
    repository: str
    phase: str
    tracked_files: int
    scanned_files: int
    generated_outputs: int
    artifact_count: int
    exception_id: str | None
    evidence_id: str

    def output_values(self) -> dict[str, str]:
        return {
            "repository": self.repository,
            "phase": self.phase,
            "tracked_files": str(self.tracked_files),
            "scanned_files": str(self.scanned_files),
            "generated_outputs": str(self.generated_outputs),
            "artifact_count": str(self.artifact_count),
            "artifact_exception_id": self.exception_id or "",
            "policy_evidence_id": self.evidence_id,
            "verified": "true",
        }


def _run_git(root: Path, arguments: Sequence[str], instruction: str) -> bytes:
    try:
        completed = subprocess.run(
            ["git", *arguments],
            cwd=root,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    except OSError as error:
        raise FoundationError(instruction) from error
    require(completed.returncode == 0, instruction)
    return completed.stdout


def tracked_paths(root: Path) -> tuple[str, ...]:
    raw = _run_git(root, ["ls-files", "-z"], "tracked_file_inventory_failed")
    values = []
    for item in raw.split(b"\0"):
        if not item:
            continue
        try:
            path = item.decode("utf-8")
        except UnicodeDecodeError as error:
            raise FoundationError("tracked_path_encoding_invalid") from error
        values.append(safe_relative_path(path, "tracked_path_invalid"))
    return tuple(sorted(values))


def _matches(path: str, patterns: Sequence[str]) -> bool:
    pure = PurePosixPath(path)
    return any(fnmatch.fnmatchcase(path, pattern) or pure.match(pattern) for pattern in patterns)


def _repository_exception_paths(contract: Mapping[str, Any], repository: str) -> set[str]:
    result: set[str] = set()
    exceptions = contract.get("repository_exceptions", [])
    require(isinstance(exceptions, list), "repository_policy_invalid")
    for entry in exceptions:
        require(isinstance(entry, dict), "repository_policy_invalid")
        if entry.get("repository") != repository:
            continue
        paths = entry.get("paths")
        require(isinstance(paths, list) and all(isinstance(value, str) for value in paths), "repository_policy_invalid")
        result.update(safe_relative_path(value) for value in paths)
    return result


def _forbidden_path(path: str, contract: Mapping[str, Any], repository: str) -> bool:
    forbidden = contract.get("forbidden_paths")
    allowed = contract.get("allowed_forbidden_path_exceptions")
    require(
        isinstance(forbidden, list)
        and isinstance(allowed, list)
        and all(isinstance(value, str) for value in forbidden + allowed),
        "repository_policy_invalid",
    )
    if path in _repository_exception_paths(contract, repository):
        return False
    return _matches(path, forbidden) and not _matches(path, allowed)


def _contains_token_like(content: str, contract: Mapping[str, Any]) -> bool:
    rules = contract.get("token_rules")
    markers = contract.get("split_markers")
    require(isinstance(rules, list) and isinstance(markers, list), "repository_policy_invalid")
    for rule in rules:
        require(isinstance(rule, dict), "repository_policy_invalid")
        prefixes = rule.get("prefixes")
        minimum = rule.get("minimum_length")
        require(
            isinstance(prefixes, list)
            and all(isinstance(value, str) and value for value in prefixes)
            and isinstance(minimum, int)
            and minimum > 0,
            "repository_policy_invalid",
        )
        for prefix in prefixes:
            start = 0
            while True:
                index = content.find(prefix, start)
                if index < 0:
                    break
                candidate = re.match(r"[A-Za-z0-9_+=./~-]+", content[index:])
                if candidate and len(candidate.group(0)) >= minimum:
                    return True
                start = index + len(prefix)
    for marker in markers:
        require(isinstance(marker, dict), "repository_policy_invalid")
        segments = marker.get("segments")
        minimum_trailing = marker.get("minimum_trailing_length")
        require(
            isinstance(segments, list)
            and all(isinstance(value, str) for value in segments)
            and (
                minimum_trailing is None
                or isinstance(minimum_trailing, int)
                and minimum_trailing > 0
            ),
            "repository_policy_invalid",
        )
        joined = "".join(segments)
        if minimum_trailing is None:
            if joined in content:
                return True
            continue
        start = 0
        while True:
            index = content.find(joined, start)
            if index < 0:
                break
            suffix = content[index + len(joined):]
            candidate = re.match(r"[A-Za-z0-9_+=./~-]+", suffix)
            if candidate and len(candidate.group(0)) >= minimum_trailing:
                return True
            start = index + len(joined)
    # Bounded JWT signature detection avoids storing a token-shaped fixture in
    # the policy contract itself.
    if re.search(r"\beyJ[A-Za-z0-9_-]{12,}\.[A-Za-z0-9_-]{12,}\.[A-Za-z0-9_-]{12,}\b", content):
        return True
    return False


def scan_tracked_repository(
    root: Path,
    *,
    repository: str,
    contract_root: Path,
) -> tuple[int, int]:
    repository = repository_name(repository)
    contract = load_contract(contract_root, REPOSITORY_POLICY)
    require(contract.get("organization") == "StreamScapeTV", "repository_policy_invalid")
    maximum = contract.get("maximum_scanned_bytes")
    require(isinstance(maximum, int) and maximum > 0, "repository_policy_invalid")
    paths = tracked_paths(root)
    scanned = 0
    root = root.resolve()
    for relative in paths:
        require(not _forbidden_path(relative, contract, repository), "forbidden_tracked_file")
        target = root / Path(*PurePosixPath(relative).parts)
        if target.is_symlink():
            resolved = target.resolve(strict=False)
            require(root == resolved or root in resolved.parents, "tracked_symlink_escape")
            continue
        if not target.is_file():
            continue
        try:
            size = target.stat().st_size
        except OSError as error:
            raise FoundationError("tracked_file_unavailable") from error
        if size > maximum:
            continue
        try:
            raw = target.read_bytes()
        except OSError as error:
            raise FoundationError("tracked_file_unavailable") from error
        if b"\x00" in raw:
            continue
        content = raw.decode("utf-8", errors="replace")
        require(not _contains_token_like(content, contract), "tracked_secret_detected")
        scanned += 1
    return len(paths), scanned


def verify_clean_tree(root: Path) -> None:
    raw = _run_git(
        root,
        ["status", "--porcelain=v1", "-z", "--untracked-files=all"],
        "clean_tree_check_failed",
    )
    require(not raw, "repository_tree_dirty")


def verify_generated_outputs(root: Path, *, contract_root: Path) -> int:
    contract = load_contract(contract_root, REPOSITORY_POLICY)
    paths = contract.get("generated_outputs")
    require(isinstance(paths, list) and all(isinstance(value, str) for value in paths), "repository_policy_invalid")
    for raw in paths:
        relative = safe_relative_path(raw)
        completed = subprocess.run(
            ["git", "diff", "--quiet", "--", relative],
            cwd=root,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        require(completed.returncode == 0, "generated_output_dirty")
    return len(paths)


def parse_artifact_declarations(raw: str) -> tuple[ArtifactDeclaration, ...]:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as error:
        raise FoundationError("artifact_manifest_invalid") from error
    require(isinstance(payload, list) and len(payload) <= 20, "artifact_manifest_invalid")
    result: list[ArtifactDeclaration] = []
    for item in payload:
        require(isinstance(item, dict), "artifact_manifest_invalid")
        name = safe_id(item.get("name"), "artifact_manifest_invalid")
        size = item.get("size_bytes")
        retention = item.get("retention_days")
        require(
            isinstance(size, int)
            and 0 <= size <= 10 * 1024 * 1024 * 1024
            and isinstance(retention, int)
            and 1 <= retention <= 90,
            "artifact_manifest_invalid",
        )
        result.append(ArtifactDeclaration(name=name, size_bytes=size, retention_days=retention))
    return tuple(result)


def validate_artifacts(
    artifacts: Sequence[ArtifactDeclaration],
    *,
    exception_id: str | None,
    trust_mode: str | None,
    contract_root: Path,
) -> str | None:
    contract = load_contract(contract_root, ARTIFACT_EXCEPTIONS)
    require(contract.get("default") == "zero-artifacts", "artifact_contract_invalid")
    if not artifacts:
        require(exception_id in {None, ""}, "unused_artifact_exception")
        return None
    require(exception_id not in {None, ""}, "undeclared_artifact")
    exception_id = safe_id(exception_id, "artifact_exception_invalid")
    exceptions = contract.get("exceptions")
    require(isinstance(exceptions, list), "artifact_contract_invalid")
    entry = next(
        (
            value
            for value in exceptions
            if isinstance(value, dict) and value.get("id") == exception_id
        ),
        None,
    )
    require(isinstance(entry, dict), "artifact_exception_not_registered")
    allowed_trust = entry.get("trust_modes")
    require(isinstance(allowed_trust, list) and trust_mode in allowed_trust, "artifact_exception_trust_forbidden")
    maximum_count = entry.get("maximum_count")
    maximum_bytes = entry.get("maximum_total_bytes")
    maximum_retention = entry.get("maximum_retention_days")
    allowed_names = entry.get("allowed_names")
    require(
        isinstance(maximum_count, int)
        and isinstance(maximum_bytes, int)
        and isinstance(maximum_retention, int)
        and isinstance(allowed_names, list)
        and all(isinstance(value, str) for value in allowed_names),
        "artifact_contract_invalid",
    )
    require(
        {item.name for item in artifacts} <= set(allowed_names),
        "artifact_exception_name_forbidden",
    )
    require(len(artifacts) <= maximum_count, "artifact_exception_limit_exceeded")
    require(sum(item.size_bytes for item in artifacts) <= maximum_bytes, "artifact_exception_limit_exceeded")
    require(all(item.retention_days <= maximum_retention for item in artifacts), "artifact_exception_limit_exceeded")
    return exception_id


def validate_cache_request(
    *,
    mode: str,
    repository: str,
    source_sha: str | None,
    lock_digest: str | None,
    platform: str,
    profile: str,
    trust_mode: str | None,
    contract_root: Path,
) -> CacheDecision:
    contract = load_contract(contract_root, CACHE_POLICY)
    modes = contract.get("modes")
    require(isinstance(modes, dict) and mode in modes, "unsupported_cache_mode")
    settings = modes[mode]
    require(isinstance(settings, dict), "cache_contract_invalid")
    if mode == contract.get("default_mode") == "disabled":
        return CacheDecision(mode=mode, restore=False, save=False, key=None)
    require(
        isinstance(source_sha, str)
        and FULL_SHA.fullmatch(source_sha) is not None
        and isinstance(lock_digest, str)
        and SHA256.fullmatch(lock_digest) is not None,
        "cache_key_material_required",
    )
    allowed = contract.get("allowed_trust_modes", {}).get(mode)
    require(isinstance(allowed, list) and trust_mode in allowed, "cache_trust_scope_forbidden")
    key = stable_identifier(
        "cache",
        {
            "repository": repository_name(repository),
            "source_sha": source_sha,
            "lock_digest": lock_digest,
            "platform": safe_id(platform.lower(), "cache_platform_invalid"),
            "profile": safe_id(profile, "cache_profile_invalid"),
        },
        length=32,
    )
    require(len(key) <= int(contract.get("max_key_length", 0)), "cache_key_too_long")
    return CacheDecision(
        mode=mode,
        restore=bool(settings.get("restore")),
        save=bool(settings.get("save")),
        key=key,
    )


def verify_repository_policy(
    root: Path,
    *,
    repository: str,
    phase: str,
    artifact_manifest_json: str,
    artifact_exception_id: str | None,
    trust_mode: str | None,
    contract_root: Path,
) -> PolicyReport:
    """Run one deterministic repository and artifact policy gate."""

    require(phase in {"before", "after"}, "unsupported_policy_phase")
    repository = repository_name(repository)
    verify_clean_tree(root)
    tracked, scanned = scan_tracked_repository(
        root,
        repository=repository,
        contract_root=contract_root,
    )
    generated = verify_generated_outputs(root, contract_root=contract_root)
    artifacts = parse_artifact_declarations(artifact_manifest_json)
    exception_id = validate_artifacts(
        artifacts,
        exception_id=artifact_exception_id,
        trust_mode=trust_mode,
        contract_root=contract_root,
    )
    evidence_id = stable_identifier(
        "policy",
        {
            "repository": repository,
            "phase": phase,
            "tracked": tracked,
            "scanned": scanned,
            "generated": generated,
            "artifacts": [item.__dict__ for item in artifacts],
            "exception_id": exception_id,
        },
    )
    return PolicyReport(
        repository=repository,
        phase=phase,
        tracked_files=tracked,
        scanned_files=scanned,
        generated_outputs=generated,
        artifact_count=len(artifacts),
        exception_id=exception_id,
        evidence_id=evidence_id,
    )
