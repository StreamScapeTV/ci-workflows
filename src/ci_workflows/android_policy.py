"""Reviewed repository-policy projection for Android validation."""
from __future__ import annotations

import hashlib
import re
import subprocess
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

from . import policy as foundation_policy
from .android_execution import isolated_git_environment, pre_execution_status
from .android_types import AndroidValidationRequest
from .foundation_types import (
    FoundationError,
    load_contract,
    repository_name,
    safe_relative_path,
)

SOURCE_POLICY_PATH = "contracts/android-source-policy.json"
_GIT_BLOB_SHA1 = re.compile(r"^[0-9a-f]{40}$")
_SAFE_RULE = re.compile(r"^[a-z][a-z0-9_]{2,95}$")
_SHA256_SUBJECT = re.compile(r"^sha256:[0-9a-f]{64}$")


class AndroidPolicyFinding(FoundationError):
    """Policy failure carrying only one stable rule and bounded safe subject."""

    def __init__(self, instruction: str, subject: str) -> None:
        super().__init__(instruction)
        if _SHA256_SUBJECT.fullmatch(subject) is None:
            subject = safe_relative_path(subject, "policy_diagnostic_invalid")
        if len(subject) > 255:
            raise ValueError("policy diagnostic subject exceeds the bounded limit")
        self.subject = subject


def _strings(value: Any, instruction: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise FoundationError(instruction)
    if not all(isinstance(item, str) and item for item in value):
        raise FoundationError(instruction)
    if len(value) != len(set(value)):
        raise FoundationError(instruction)
    return tuple(value)


def load_android_source_policy(root: Path) -> Mapping[str, Any]:
    """Load and validate the Android-only source-policy projection contract."""

    contract = load_contract(root, SOURCE_POLICY_PATH)
    if (
        contract.get("organization") != "StreamScapeTV"
        or contract.get("workflow_api") != "validation.android"
        or contract.get("contract_version") != "1.0.0"
    ):
        raise FoundationError("android_source_policy_invalid")

    diagnostic = contract.get("diagnostic_policy")
    if not isinstance(diagnostic, dict):
        raise FoundationError("android_source_policy_invalid")
    if diagnostic.get("maximum_subject_length") != 255:
        raise FoundationError("android_source_policy_invalid")
    if diagnostic.get("file_content_forbidden") is not True:
        raise FoundationError("android_source_policy_invalid")
    if diagnostic.get("absolute_path_forbidden") is not True:
        raise FoundationError("android_source_policy_invalid")

    projection = contract.get("failure_projection")
    if not isinstance(projection, dict):
        raise FoundationError("android_source_policy_invalid")
    exact = projection.get("exact")
    prefixes = projection.get("prefixes")
    fallback = projection.get("fallback")
    if (
        not isinstance(exact, dict)
        or not exact
        or not all(
            isinstance(key, str)
            and _SAFE_RULE.fullmatch(key) is not None
            and isinstance(value, str)
            and _SAFE_RULE.fullmatch(value) is not None
            for key, value in exact.items()
        )
        or not isinstance(prefixes, list)
        or not all(
            isinstance(item, dict)
            and isinstance(item.get("prefix"), str)
            and _SAFE_RULE.fullmatch(item.get("code", "")) is not None
            for item in prefixes
        )
        or not isinstance(fallback, str)
        or _SAFE_RULE.fullmatch(fallback) is None
    ):
        raise FoundationError("android_source_policy_invalid")

    entries = contract.get("tracked_secret_exceptions")
    if not isinstance(entries, list):
        raise FoundationError("android_source_policy_invalid")
    identities: set[str] = set()
    keys: set[tuple[str, str, str]] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise FoundationError("android_source_policy_invalid")
        identity = entry.get("id")
        repository = entry.get("repository")
        profiles = _strings(
            entry.get("validation_profiles"),
            "android_source_policy_invalid",
        )
        if (
            not isinstance(identity, str)
            or _SAFE_RULE.fullmatch(identity) is None
            or identity in identities
            or repository_name(repository, "android_source_policy_invalid")
            != repository
            or entry.get("rule_id") != "tracked_secret_detected"
            or entry.get("digest_algorithm") != "git-blob-sha1"
        ):
            raise FoundationError("android_source_policy_invalid")
        identities.add(identity)
        paths = entry.get("paths")
        if not isinstance(paths, list) or not paths:
            raise FoundationError("android_source_policy_invalid")
        for item in paths:
            if not isinstance(item, dict):
                raise FoundationError("android_source_policy_invalid")
            path = safe_relative_path(
                item.get("path"),
                "android_source_policy_invalid",
            )
            digest = item.get("git_blob_sha1")
            if (
                not isinstance(digest, str)
                or _GIT_BLOB_SHA1.fullmatch(digest) is None
            ):
                raise FoundationError("android_source_policy_invalid")
            for profile in profiles:
                key = (repository, profile, path)
                if key in keys:
                    raise FoundationError("android_source_policy_invalid")
                keys.add(key)
    return contract


def _active_secret_exceptions(
    contract: Mapping[str, Any],
    request: AndroidValidationRequest,
) -> dict[str, str]:
    result: dict[str, str] = {}
    entries = contract["tracked_secret_exceptions"]
    for entry in entries:
        if entry["repository"] != request.repository:
            continue
        if request.validation_profile not in entry["validation_profiles"]:
            continue
        for item in entry["paths"]:
            result[item["path"]] = item["git_blob_sha1"]
    return result


def _git_blob_sha1(raw: bytes) -> str:
    return hashlib.sha1(
        f"blob {len(raw)}\0".encode("ascii") + raw
    ).hexdigest()


def _generated_output_check(
    root: Path,
    *,
    repository_policy: Mapping[str, Any],
    environment: Mapping[str, str],
) -> None:
    outputs = repository_policy.get("generated_outputs")
    if not isinstance(outputs, list) or not all(
        isinstance(value, str) for value in outputs
    ):
        raise FoundationError("repository_policy_invalid")
    for raw in outputs:
        relative = safe_relative_path(raw, "repository_policy_invalid")
        try:
            completed = subprocess.run(
                ["git", "diff", "--quiet", "--", relative],
                cwd=root,
                env=isolated_git_environment(environment),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=60,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise FoundationError("generated_output_check_failed") from error
        if completed.returncode == 1:
            raise AndroidPolicyFinding("generated_output_dirty", relative)
        if completed.returncode != 0:
            raise FoundationError("generated_output_check_failed")


def _clean_tree_check(
    root: Path,
    *,
    environment: Mapping[str, str],
) -> None:
    status = pre_execution_status(root, environment)
    if not status:
        return
    digest = hashlib.sha256(
        "\n".join(sorted(status)).encode("utf-8")
    ).hexdigest()
    raise AndroidPolicyFinding(
        "repository_tree_dirty",
        f"sha256:{digest}",
    )


def _scan_tracked_source(
    root: Path,
    *,
    request: AndroidValidationRequest,
    repository_policy: Mapping[str, Any],
    source_policy: Mapping[str, Any],
) -> None:
    maximum = repository_policy.get("maximum_scanned_bytes")
    if not isinstance(maximum, int) or maximum <= 0:
        raise FoundationError("repository_policy_invalid")

    exceptions = _active_secret_exceptions(source_policy, request)
    tracked = foundation_policy.tracked_paths(root)
    boundary = root.resolve()
    for relative in tracked:
        if foundation_policy._forbidden_path(
            relative,
            repository_policy,
            request.repository,
        ):
            raise AndroidPolicyFinding("forbidden_tracked_file", relative)

        target = boundary / Path(*PurePosixPath(relative).parts)
        if target.is_symlink():
            resolved = target.resolve(strict=False)
            if not (resolved == boundary or boundary in resolved.parents):
                raise AndroidPolicyFinding("tracked_symlink_escape", relative)
            continue
        if not target.is_file():
            continue

        try:
            size = target.stat().st_size
        except OSError as error:
            raise AndroidPolicyFinding(
                "tracked_file_unavailable",
                relative,
            ) from error
        if size > maximum:
            continue
        try:
            raw = target.read_bytes()
        except OSError as error:
            raise AndroidPolicyFinding(
                "tracked_file_unavailable",
                relative,
            ) from error
        if b"\x00" in raw:
            continue

        content = raw.decode("utf-8", errors="replace")
        if not foundation_policy._contains_token_like(
            content,
            repository_policy,
        ):
            continue
        expected = exceptions.get(relative)
        if expected is None or _git_blob_sha1(raw) != expected:
            raise AndroidPolicyFinding(
                "tracked_secret_detected",
                relative,
            )


def verify_android_repository_policy(
    root: Path,
    *,
    request: AndroidValidationRequest,
    phase: str,
    contract_root: Path,
    environment: Mapping[str, str],
    source_policy: Mapping[str, Any],
) -> None:
    """Apply the reviewed Android projection without weakening global policy."""

    if phase not in {"before", "after"}:
        raise FoundationError("unsupported_policy_phase")
    if repository_name(
        request.repository,
        "invalid_repository",
    ) != request.repository:
        raise FoundationError("invalid_repository")

    repository_policy = load_contract(
        contract_root,
        foundation_policy.REPOSITORY_POLICY,
    )
    if repository_policy.get("organization") != "StreamScapeTV":
        raise FoundationError("repository_policy_invalid")

    # Generated drift is classified before the general clean-tree digest so it
    # remains a distinct stable failure without weakening the subsequent check.
    _generated_output_check(
        root,
        repository_policy=repository_policy,
        environment=environment,
    )
    _clean_tree_check(root, environment=environment)
    _scan_tracked_source(
        root,
        request=request,
        repository_policy=repository_policy,
        source_policy=source_policy,
    )

    artifacts = foundation_policy.parse_artifact_declarations("[]")
    foundation_policy.validate_artifacts(
        artifacts,
        exception_id=request.artifact_exception_id,
        trust_mode=request.source_trust,
        contract_root=contract_root,
    )
