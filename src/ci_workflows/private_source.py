"""Resolve one configured private StreamScapeTV branch to an exact commit SHA."""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Mapping

_ORGANIZATION = "StreamScapeTV"
_REPOSITORY_NAME = re.compile(r"[A-Za-z0-9_.-]+")
_FULL_SHA = re.compile(r"[0-9a-f]{40}")
_FORBIDDEN_BRANCH_FRAGMENTS = ("..", "@{")
_FORBIDDEN_BRANCH_CHARACTERS = frozenset(" ~^:?*[\\")


class PrivateSourceError(RuntimeError):
    """Fail-closed error whose code never contains a repository, branch, or token."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise PrivateSourceError(code)


def _repository_name(value: object) -> str:
    _require(isinstance(value, str), "invalid_repository_name")
    _require(value == value.strip(), "invalid_repository_name")
    _require(_REPOSITORY_NAME.fullmatch(value) is not None, "invalid_repository_name")
    return value


def _branch(value: object) -> str:
    _require(isinstance(value, str), "invalid_branch")
    _require(value == value.strip(), "invalid_branch")
    _require(0 < len(value.encode("utf-8")) <= 255, "invalid_branch")
    _require(not value.startswith(("-", "refs/", "/", ".")), "invalid_branch")
    _require(not value.endswith(("/", ".", ".lock")), "invalid_branch")
    _require(all(fragment not in value for fragment in _FORBIDDEN_BRANCH_FRAGMENTS), "invalid_branch")
    _require(not any(character in _FORBIDDEN_BRANCH_CHARACTERS for character in value), "invalid_branch")
    _require(all(ord(character) >= 32 and ord(character) != 127 for character in value), "invalid_branch")
    _require(all(part and not part.startswith(".") and not part.endswith(".lock") for part in value.split("/")), "invalid_branch")
    return value


def _mapping(value: object) -> Mapping[str, Any]:
    _require(isinstance(value, Mapping), "github_api_invalid_response")
    return value


def resolve_private_branch(
    *,
    repository_name: str,
    branch: str,
    token: str,
    opener: Any = urllib.request.urlopen,
) -> str:
    """Resolve one fixed-organization private branch without exposing mutable source."""

    repository = _repository_name(repository_name)
    requested_branch = _branch(branch)
    _require(isinstance(token, str) and bool(token), "github_token_required")

    url = (
        f"https://api.github.com/repos/{_ORGANIZATION}/"
        f"{urllib.parse.quote(repository, safe='')}/commits/"
        f"{urllib.parse.quote(requested_branch, safe='')}"
    )
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with opener(request, timeout=30) as response:
            payload = _mapping(json.load(response))
    except urllib.error.HTTPError as error:
        raise PrivateSourceError(f"github_api_http_{error.code}") from None
    except (OSError, ValueError, urllib.error.URLError):
        raise PrivateSourceError("github_api_unavailable") from None

    sha = payload.get("sha")
    _require(isinstance(sha, str) and _FULL_SHA.fullmatch(sha) is not None, "invalid_resolved_sha")
    return sha


__all__ = ("PrivateSourceError", "resolve_private_branch")
