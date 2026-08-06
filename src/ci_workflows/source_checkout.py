"""Credential-free exact-SHA detached checkout implementation."""
from __future__ import annotations

import base64
import os
import shutil
import subprocess
from pathlib import Path, PurePosixPath
from typing import Mapping, Sequence

from .source_types import (
    REPOSITORY,
    SourceAdmissionError,
    _full_sha,
    _positive_int,
    _require,
)


def _safe_checkout_path(workspace: Path, raw_path: str) -> Path:
    pure = PurePosixPath(raw_path)
    _require(
        bool(raw_path)
        and not pure.is_absolute()
        and ".." not in pure.parts
        and "\\" not in raw_path,
        "invalid_checkout_path",
    )
    target = (workspace / Path(*pure.parts)).resolve()
    workspace = workspace.resolve()
    _require(
        target != workspace and workspace in target.parents,
        "invalid_checkout_path",
    )
    return target


def _run_git(
    arguments: Sequence[str],
    *,
    cwd: Path,
    environment: Mapping[str, str] | None = None,
) -> str:
    try:
        completed = subprocess.run(
            ["git", *arguments],
            cwd=cwd,
            env=None if environment is None else dict(environment),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
    except OSError as error:
        raise SourceAdmissionError("exact_checkout_git_failure") from error
    if completed.returncode != 0:
        raise SourceAdmissionError("exact_checkout_git_failure")
    return completed.stdout.strip()


def _clean_failed_checkout(target: Path, *, preserve_empty_path: bool) -> None:
    try:
        if target.exists():
            shutil.rmtree(target)
        if preserve_empty_path:
            target.mkdir(parents=True)
    except OSError as error:
        raise SourceAdmissionError("exact_checkout_cleanup_failure") from error


def exact_checkout(
    *,
    repository: str,
    admitted_sha: str,
    path: str,
    fetch_depth: int,
    token: str,
    workspace: Path,
    remote_url: str | None = None,
) -> Mapping[str, str]:
    """Fetch one exact commit into a detached checkout and verify equality."""

    _require(
        REPOSITORY.fullmatch(repository) is not None,
        "invalid_checkout_repository",
    )
    admitted_sha = _full_sha(
        admitted_sha,
        "admitted_sha_must_be_full_sha",
    )
    fetch_depth = _positive_int(fetch_depth, "invalid_history_depth")
    target = _safe_checkout_path(workspace, path)
    preserve_empty_path = target.exists()
    if preserve_empty_path:
        _require(target.is_dir(), "checkout_path_not_directory")
        _require(not any(target.iterdir()), "checkout_path_not_empty")
    else:
        target.mkdir(parents=True)
    remote = remote_url or f"https://github.com/{repository}.git"
    if remote_url is None:
        _require(
            remote == f"https://github.com/{repository}.git",
            "invalid_checkout_remote",
        )

    try:
        _run_git(["init", "--quiet"], cwd=target)
        _run_git(["remote", "add", "origin", remote], cwd=target)

        environment = os.environ.copy()
        if token:
            encoded = base64.b64encode(
                f"x-access-token:{token}".encode("utf-8")
            ).decode("ascii")
            environment.update(
                {
                    "GIT_CONFIG_COUNT": "1",
                    "GIT_CONFIG_KEY_0": "http.https://github.com/.extraheader",
                    "GIT_CONFIG_VALUE_0": f"AUTHORIZATION: basic {encoded}",
                    "GIT_TERMINAL_PROMPT": "0",
                }
            )
        _run_git(
            [
                "fetch",
                "--quiet",
                "--no-tags",
                f"--depth={fetch_depth}",
                "origin",
                admitted_sha,
            ],
            cwd=target,
            environment=environment,
        )
        _run_git(
            ["checkout", "--quiet", "--detach", "FETCH_HEAD"],
            cwd=target,
        )
        head = _run_git(["rev-parse", "HEAD"], cwd=target)
        _require(head == admitted_sha, "exact_checkout_head_mismatch")
        symbolic = subprocess.run(
            ["git", "symbolic-ref", "-q", "HEAD"],
            cwd=target,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        _require(symbolic.returncode != 0, "exact_checkout_not_detached")
        persisted = subprocess.run(
            [
                "git",
                "config",
                "--local",
                "--get-regexp",
                r"http\..*extraheader",
            ],
            cwd=target,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            check=False,
        )
        _require(
            not persisted.stdout.strip(),
            "checkout_credentials_persisted",
        )
    except (OSError, SourceAdmissionError) as error:
        _clean_failed_checkout(
            target,
            preserve_empty_path=preserve_empty_path,
        )
        if isinstance(error, SourceAdmissionError):
            raise
        raise SourceAdmissionError("exact_checkout_git_failure") from error

    return {
        "repository": repository,
        "head_sha": head,
        "path": str(target.relative_to(workspace.resolve())),
        "fetch_depth": str(fetch_depth),
        "verified": "true",
    }
