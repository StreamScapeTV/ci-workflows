"""Read-only GitHub metadata provider for source admission."""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Mapping

from .source_evidence import _mapping
from .source_types import (
    REPOSITORY,
    SourceAdmissionError,
    _full_sha,
    _require,
)


class GitHubSourceProvider:
    """Small read-only GitHub API client for exact source metadata."""

    def __init__(
        self,
        token: str,
        api_url: str = "https://api.github.com",
    ) -> None:
        _require(bool(token), "github_token_missing")
        self._token = token
        self._api_url = api_url.rstrip("/")
        _require(
            self._api_url == "https://api.github.com",
            "unsupported_github_api_url",
        )

    def _get(self, path: str) -> Mapping[str, Any]:
        url = f"{self._api_url}{path}"
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self._token}",
                "User-Agent": "StreamScapeTV-ci-workflows-source-admission",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (
            urllib.error.URLError,
            TimeoutError,
            json.JSONDecodeError,
        ) as error:
            raise SourceAdmissionError("github_metadata_unavailable") from error
        _require(isinstance(payload, Mapping), "github_metadata_invalid")
        return payload

    @staticmethod
    def _repository_path(repository: str) -> str:
        _require(
            REPOSITORY.fullmatch(repository) is not None,
            "invalid_caller_repository",
        )
        owner, name = repository.split("/", 1)
        return f"/repos/{urllib.parse.quote(owner)}/{urllib.parse.quote(name)}"

    def repository(self, repository: str) -> Mapping[str, Any]:
        return self._get(self._repository_path(repository))

    def collaborator_permission(self, repository: str, actor: str) -> str:
        actor_path = urllib.parse.quote(actor)
        result = self._get(
            f"{self._repository_path(repository)}/collaborators/{actor_path}/permission"
        )
        return str(result.get("permission", ""))

    def pull_request(
        self,
        repository: str,
        number: int,
    ) -> Mapping[str, Any]:
        return self._get(
            f"{self._repository_path(repository)}/pulls/{number}"
        )

    def commit(self, repository: str, sha: str) -> Mapping[str, Any]:
        return self._get(
            f"{self._repository_path(repository)}/commits/{sha}"
        )

    def branch_sha(self, repository: str, branch: str) -> str:
        branch_path = urllib.parse.quote(branch, safe="")
        result = self._get(
            f"{self._repository_path(repository)}/branches/{branch_path}"
        )
        commit = _mapping(
            result.get("commit"),
            "github_branch_metadata_invalid",
        )
        return _full_sha(
            commit.get("sha"),
            "github_branch_sha_invalid",
        )

    def tag_ref(
        self,
        repository: str,
        tag_name: str,
    ) -> Mapping[str, Any]:
        tag_path = urllib.parse.quote(tag_name, safe="")
        return self._get(
            f"{self._repository_path(repository)}/git/ref/tags/{tag_path}"
        )

    def tag_object(
        self,
        repository: str,
        sha: str,
    ) -> Mapping[str, Any]:
        return self._get(
            f"{self._repository_path(repository)}/git/tags/{sha}"
        )
