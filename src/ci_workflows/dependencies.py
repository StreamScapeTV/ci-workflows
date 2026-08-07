"""Exact private dependency checkout integrated with source admission contracts."""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

from .foundation_types import (
    FoundationError,
    bounded_int,
    bounded_path,
    ensure_no_symlink_escape,
    full_sha,
    repository_name,
    require,
    safe_id,
    safe_relative_path,
)
from .workspace import register_state_path, remove_registered_path

_TOKEN_LIKE = re.compile(
    r"(?:gh[pousr]_[A-Za-z0-9_]{24,}|github_pat_[A-Za-z0-9_]{40,}|AKIA[0-9A-Z]{16}|Bearer\s+[A-Za-z0-9._~-]{16,})"
)


@dataclass(frozen=True)
class DependencyResult:
    dependency_id: str
    repository: str
    head_sha: str
    relative_path: str
    expected_subpath: str
    remotes_erased: bool
    credentials_erased: bool

    def output_values(self) -> dict[str, str]:
        return {
            "dependency_id": self.dependency_id,
            "repository": self.repository,
            "head_sha": self.head_sha,
            "relative_path": self.relative_path,
            "expected_subpath": self.expected_subpath,
            "remotes_erased": "true" if self.remotes_erased else "false",
            "credentials_erased": "true" if self.credentials_erased else "false",
            "verified": "true",
        }


def _run_git(
    target: Path,
    arguments: Sequence[str],
    *,
    allow_failure: bool = False,
) -> str:
    try:
        completed = subprocess.run(
            ["git", *arguments],
            cwd=target,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
    except OSError as error:
        raise FoundationError("dependency_git_failure") from error
    if completed.returncode != 0 and not allow_failure:
        raise FoundationError("dependency_git_failure")
    return completed.stdout.strip()


def _exact_checkout(**kwargs: object) -> Mapping[str, str]:
    # Import the published #7 facade only at the integration boundary. Tests
    # patch this private adapter rather than adding callback-style APIs.
    from .source import exact_checkout

    return exact_checkout(**kwargs)


def _erase_git_connection_state(target: Path) -> None:
    for remote in _run_git(target, ["remote"]).splitlines():
        if remote.strip():
            _run_git(target, ["remote", "remove", remote.strip()])
    names = _run_git(target, ["config", "--local", "--name-only", "--list"]).splitlines()
    sensitive = (
        "credential.",
        "http.",
        "https.",
        "url.",
        "core.sshcommand",
        "include.",
        "includeif.",
    )
    for name in names:
        lowered = name.lower()
        if lowered.startswith(sensitive) or lowered.endswith("extraheader"):
            _run_git(target, ["config", "--local", "--unset-all", name], allow_failure=True)


def _verify_expected_subpath(target: Path, raw_subpath: str) -> str:
    if raw_subpath in {"", "."}:
        return "."
    relative = safe_relative_path(raw_subpath, "invalid_dependency_subpath")
    expected = bounded_path(target, relative, "dependency_subpath_escape")
    ensure_no_symlink_escape(target, expected)
    require(expected.exists(), "dependency_subpath_missing")
    return relative


def _verify_detached_credential_free(target: Path, admitted_sha: str, token: str) -> None:
    head = _run_git(target, ["rev-parse", "HEAD"])
    require(head == admitted_sha, "dependency_head_mismatch")
    symbolic = subprocess.run(
        ["git", "symbolic-ref", "-q", "HEAD"],
        cwd=target,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    require(symbolic.returncode != 0, "dependency_not_detached")
    require(not _run_git(target, ["remote"]), "dependency_remote_residue")
    config = target / ".git" / "config"
    try:
        content = config.read_text(encoding="utf-8", errors="replace")
    except OSError as error:
        raise FoundationError("dependency_config_unavailable") from error
    lowered = content.lower()
    require(
        "extraheader" not in lowered
        and "authorization" not in lowered
        and "credential.helper" not in lowered
        and "github.com/" not in lowered,
        "dependency_credential_residue",
    )
    require(_TOKEN_LIKE.search(content) is None, "dependency_credential_residue")
    if token:
        require(token not in content, "dependency_credential_residue")


def checkout_private_dependency(
    *,
    state_root: Path,
    repository: str,
    admitted_sha: str,
    dependency_id: str,
    expected_subpath: str,
    fetch_depth: int,
    token: str,
    contract_root: Path,
) -> DependencyResult:
    """Checkout one admitted StreamScapeTV dependency and erase connection state."""

    repository = repository_name(repository, "invalid_dependency_repository")
    require(repository.startswith("StreamScapeTV/"), "dependency_repository_not_approved")
    admitted_sha = full_sha(admitted_sha, "dependency_sha_must_be_full_sha")
    dependency_id = safe_id(dependency_id, "invalid_dependency_id")
    fetch_depth = bounded_int(
        fetch_depth,
        minimum=1,
        maximum=1000,
        instruction="invalid_dependency_fetch_depth",
    )
    relative_path = f"dependencies/{dependency_id}"
    registry_name = f"dependency-{dependency_id}"
    target = register_state_path(
        state_root,
        name=registry_name,
        relative=relative_path,
        kind="dependency",
        contract_root=contract_root,
        create=True,
    )
    try:
        result = _exact_checkout(
            repository=repository,
            admitted_sha=admitted_sha,
            path=relative_path,
            fetch_depth=fetch_depth,
            token=token,
            workspace=state_root,
        )
        require(result.get("head_sha") == admitted_sha, "dependency_head_mismatch")
        _erase_git_connection_state(target)
        verified_subpath = _verify_expected_subpath(target, expected_subpath)
        _verify_detached_credential_free(target, admitted_sha, token)
    except BaseException:
        remove_registered_path(
            state_root,
            name=registry_name,
            contract_root=contract_root,
        )
        raise
    return DependencyResult(
        dependency_id=dependency_id,
        repository=repository,
        head_sha=admitted_sha,
        relative_path=relative_path,
        expected_subpath=verified_subpath,
        remotes_erased=True,
        credentials_erased=True,
    )
