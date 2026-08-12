"""Fail-closed Helm registry publication and exact immutable read-back."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Mapping

from .helm_contract import SEMVER, require
from .helm_execution import (
    _registry_host,
    _run,
    _runtime_environment,
    _verify_no_kubernetes_authority,
    normalize_chart_archive,
    sanitize,
)
from .helm_types import HelmPlan, HelmPublicationResult, HelmValidationResult


_RELEASE_MODES = {"tag-push", "existing-tag"}
_DEFINITE_ABSENCE_MARKERS = (
    "manifest_unknown",
    "manifest unknown",
    "name_unknown",
)


def _release_mode(environment: Mapping[str, str]) -> str:
    mode = environment.get("INPUT_RELEASE_MODE", "").strip()
    require(mode in _RELEASE_MODES, "release_mode_invalid")
    return mode


def _definite_remote_absence(
    stdout: str,
    stderr: str,
    *,
    source_root: Path,
    state_root: Path,
) -> bool:
    """Accept only registry-standard unknown-manifest/name signals as absence."""

    message = sanitize(stdout + stderr, (source_root, state_root)).casefold()
    return any(marker in message for marker in _DEFINITE_ABSENCE_MARKERS)


def _assert_remote_archive(
    remote_root: Path,
    remote_archive: Path,
) -> None:
    try:
        entries = list(remote_root.iterdir())
    except OSError:
        require(False, "remote_read_back_failed")
        return
    require(
        len(entries) == 1
        and entries[0] == remote_archive
        and remote_archive.is_file()
        and not remote_archive.is_symlink(),
        "remote_read_back_failed",
    )


def publish_and_read_back(
    source_root: Path,
    state_root: Path,
    plan: HelmPlan,
    validation: HelmValidationResult,
    inherited: Mapping[str, str],
) -> HelmPublicationResult:
    """Publish only on trusted tag-push or verify one existing immutable chart."""

    require(
        plan.release_version is not None
        and SEMVER.fullmatch(plan.release_version) is not None,
        "release_version_mismatch",
    )
    release_mode = _release_mode(inherited)
    allow_publish = release_mode == "tag-push"
    _verify_no_kubernetes_authority(inherited)

    username = inherited.get("INPUT_REGISTRY_USERNAME", "")
    token = inherited.get("INPUT_REGISTRY_TOKEN", "")
    require(bool(username) and bool(token), "registry_auth_missing")
    environment = _runtime_environment(inherited, state_root)
    host = _registry_host(plan.product.registry_repository)
    _run(
        [
            "helm",
            "registry",
            "login",
            host,
            "--username",
            username,
            "--password-stdin",
        ],
        cwd=source_root,
        environment=environment,
        timeout=60,
        code="registry_auth_failed",
        stdin=f"{token}\n",
    )

    remote_root = state_root / "helm-validation" / "read-back"
    remote_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    require(not any(remote_root.iterdir()), "remote_read_back_failed")
    chart_reference = f"{plan.product.registry_repository}/{plan.product.chart_name}"
    remote_archive = remote_root / (
        f"{plan.product.chart_name}-{plan.release_version}.tgz"
    )
    pull_args = [
        "helm",
        "pull",
        chart_reference,
        "--version",
        plan.release_version,
        "--destination",
        str(remote_root),
    ]
    prior = _run(
        pull_args,
        cwd=source_root,
        environment=environment,
        timeout=120,
        code="remote_read_back_failed",
        check=False,
    )

    published = False
    if prior.returncode == 0:
        _assert_remote_archive(remote_root, remote_archive)
    else:
        require(
            _definite_remote_absence(
                prior.stdout,
                prior.stderr,
                source_root=source_root,
                state_root=state_root,
            ),
            "registry_lookup_failed",
        )
        require(not any(remote_root.iterdir()), "registry_lookup_failed")
        require(allow_publish, "remote_version_missing")
        _run(
            [
                "helm",
                "push",
                str(validation.archive_path),
                plan.product.registry_repository,
            ],
            cwd=source_root,
            environment=environment,
            timeout=120,
            code="publication_failed",
        )
        _run(
            pull_args,
            cwd=source_root,
            environment=environment,
            timeout=120,
            code="remote_read_back_failed",
        )
        _assert_remote_archive(remote_root, remote_archive)
        published = True

    readback = remote_root / "normalized.tgz"
    remote_sha256 = normalize_chart_archive(
        remote_archive,
        readback,
        plan.product.chart_name,
    )
    require(remote_sha256 == validation.package_sha256, "immutable_conflict")
    reference = f"{chart_reference}:{plan.release_version}"
    immutable = json.dumps(
        {
            "chart": reference,
            "chart_digest": validation.chart_digest,
            "package_sha256": validation.package_sha256,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return HelmPublicationResult(
        chart_digest=validation.chart_digest,
        immutable_references_json=immutable,
        package_sha256=validation.package_sha256,
        published=published,
    )
