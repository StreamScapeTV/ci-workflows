#!/usr/bin/env python3
"""Synchronize StreamScapeTV issue dependency manifests with native GitHub edges."""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import re
import sys
from typing import Any, Mapping, Sequence
import urllib.error
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ci_workflows.issue_dependencies import (  # noqa: E402
    DependencySyncError,
    IssueRecord,
    IssueRef,
    NativeDependency,
    RepositoryRecord,
    sync_organization,
)

API_ROOT = "https://api.github.com"
API_VERSION = "2026-03-10"
ORGANIZATION = "StreamScapeTV"
EXPECTED_REPOSITORY = "StreamScapeTV/ci-workflows"
EXPECTED_REF = "refs/heads/main"
SUPPORTED_EVENTS = frozenset({"schedule", "workflow_dispatch"})
_ALLOWED_CONTENT_PATHS = frozenset({"AGENTS.md", "ISSUE_DEPENDENCIES.yml"})
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_API_REPOSITORY_RE = re.compile(
    r"^https://api\.github\.com/repos/"
    r"(?P<owner>[A-Za-z0-9](?:[A-Za-z0-9-]{0,38}))/"
    r"(?P<repo>[A-Za-z0-9](?:[A-Za-z0-9._-]{0,99}))$"
)


class GitHubApiError(DependencySyncError):
    """Sanitized GitHub API failure."""


class GitHubRestGateway:
    """Least-privilege GitHub REST adapter for dependency reconciliation."""

    def __init__(self, token: str) -> None:
        if not token or token.strip() != token or "\n" in token or "\r" in token:
            raise GitHubApiError("GH_TOKEN is missing or malformed")
        self._token = token

    def _request_json(
        self,
        method: str,
        path: str,
        *,
        payload: Mapping[str, Any] | None = None,
        allow_not_found: bool = False,
    ) -> Any | None:
        if not path.startswith("/") or "://" in path:
            raise GitHubApiError("refused non-relative GitHub API path")
        url = f"{API_ROOT}{path}"
        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self._token}",
            "User-Agent": "StreamScapeTV-ci-workflows-issue-dependency-sync/1",
            "X-GitHub-Api-Version": API_VERSION,
        }
        data: bytes | None = None
        if payload is not None:
            data = json.dumps(
                payload, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
            headers["Content-Type"] = "application/json"

        request = urllib.request.Request(
            url,
            data=data,
            headers=headers,
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                status = int(response.status)
                body = response.read()
        except urllib.error.HTTPError as exc:
            if allow_not_found and exc.code == 404:
                return None
            raise GitHubApiError(
                f"GitHub API {method} {path.split('?', 1)[0]} returned HTTP {exc.code}"
            ) from None
        except urllib.error.URLError:
            raise GitHubApiError(
                f"GitHub API {method} {path.split('?', 1)[0]} failed"
            ) from None

        expected = {"GET": {200}, "POST": {201}, "DELETE": {200}}
        if status not in expected.get(method, {200}):
            raise GitHubApiError(
                f"GitHub API {method} {path.split('?', 1)[0]} "
                f"returned unexpected HTTP {status}"
            )
        if not body:
            return None
        try:
            return json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise GitHubApiError(
                f"GitHub API {method} {path.split('?', 1)[0]} "
                "returned invalid JSON"
            ) from None

    @staticmethod
    def _repo_name(repository: str) -> str:
        prefix = f"{ORGANIZATION}/"
        if not repository.startswith(prefix):
            raise GitHubApiError(f"unsupported repository: {repository!r}")
        name = repository[len(prefix) :]
        if not name or "/" in name:
            raise GitHubApiError(f"unsupported repository: {repository!r}")
        return name

    def list_repositories(self) -> Sequence[RepositoryRecord]:
        repositories: list[RepositoryRecord] = []
        page = 1
        while True:
            payload = self._request_json(
                "GET",
                f"/orgs/{ORGANIZATION}/repos?type=all&per_page=100&page={page}",
            )
            if not isinstance(payload, list):
                raise GitHubApiError("organization repository response is not a list")
            for item in payload:
                if not isinstance(item, Mapping):
                    raise GitHubApiError("organization repository entry is malformed")
                full_name = item.get("full_name")
                default_branch = item.get("default_branch")
                if not isinstance(full_name, str):
                    raise GitHubApiError("organization repository is missing full_name")
                if default_branch is None:
                    default_branch = ""
                if not isinstance(default_branch, str):
                    raise GitHubApiError(
                        f"{full_name}: default_branch is not a string"
                    )
                repositories.append(
                    RepositoryRecord(
                        full_name=full_name,
                        default_branch=default_branch,
                    )
                )
            if len(payload) < 100:
                break
            page += 1
            if page > 1000:
                raise GitHubApiError("repository enumeration exceeded safety bound")
        return tuple(sorted(repositories))

    def read_file(self, repository: str, path: str, ref: str) -> str | None:
        if path not in _ALLOWED_CONTENT_PATHS:
            raise GitHubApiError(f"refused unsupported repository file path: {path}")
        repo = urllib.parse.quote(self._repo_name(repository), safe="")
        encoded_path = urllib.parse.quote(path, safe="/")
        encoded_ref = urllib.parse.quote(ref, safe="")
        payload = self._request_json(
            "GET",
            f"/repos/{ORGANIZATION}/{repo}/contents/{encoded_path}?ref={encoded_ref}",
            allow_not_found=True,
        )
        if payload is None:
            return None
        if not isinstance(payload, Mapping):
            raise GitHubApiError(f"{repository}/{path}: content response is malformed")
        if payload.get("type") != "file" or payload.get("encoding") != "base64":
            raise GitHubApiError(f"{repository}/{path}: expected base64 file content")
        content = payload.get("content")
        if not isinstance(content, str):
            raise GitHubApiError(f"{repository}/{path}: content is missing")
        try:
            raw = base64.b64decode(content)
            return raw.decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            raise GitHubApiError(f"{repository}/{path}: content is not valid UTF-8") from None

    def get_issue(self, ref: IssueRef) -> IssueRecord | None:
        repo = urllib.parse.quote(self._repo_name(ref.repository), safe="")
        payload = self._request_json(
            "GET",
            f"/repos/{ORGANIZATION}/{repo}/issues/{ref.number}",
            allow_not_found=True,
        )
        if payload is None:
            return None
        if not isinstance(payload, Mapping):
            raise GitHubApiError(f"{ref.url}: issue response is malformed")
        issue_id = payload.get("id")
        number = payload.get("number")
        state = payload.get("state")
        state_reason = payload.get("state_reason")
        if (
            isinstance(issue_id, bool)
            or not isinstance(issue_id, int)
            or issue_id < 1
            or number != ref.number
            or not isinstance(state, str)
            or (state_reason is not None and not isinstance(state_reason, str))
        ):
            raise GitHubApiError(f"{ref.url}: issue response fields are malformed")
        return IssueRecord(
            ref=ref,
            issue_id=issue_id,
            state=state,
            state_reason=state_reason,
            is_pull_request="pull_request" in payload,
        )

    def list_blocked_by(self, ref: IssueRef) -> Sequence[NativeDependency]:
        repo = urllib.parse.quote(self._repo_name(ref.repository), safe="")
        dependencies: list[NativeDependency] = []
        page = 1
        while True:
            payload = self._request_json(
                "GET",
                f"/repos/{ORGANIZATION}/{repo}/issues/{ref.number}"
                f"/dependencies/blocked_by?per_page=100&page={page}",
            )
            if not isinstance(payload, list):
                raise GitHubApiError(f"{ref.url}: dependency response is not a list")
            for item in payload:
                if not isinstance(item, Mapping):
                    raise GitHubApiError(f"{ref.url}: dependency entry is malformed")
                issue_id = item.get("id")
                number = item.get("number")
                repository_url = item.get("repository_url")
                if (
                    isinstance(issue_id, bool)
                    or not isinstance(issue_id, int)
                    or issue_id < 1
                    or isinstance(number, bool)
                    or not isinstance(number, int)
                    or number < 1
                    or not isinstance(repository_url, str)
                ):
                    raise GitHubApiError(
                        f"{ref.url}: dependency response fields are malformed"
                    )
                repo_match = _API_REPOSITORY_RE.fullmatch(repository_url)
                if not repo_match:
                    raise GitHubApiError(
                        f"{ref.url}: dependency repository identity is malformed"
                    )
                canonical_url = (
                    f"https://github.com/{repo_match.group('owner')}/"
                    f"{repo_match.group('repo')}/issues/{number}"
                )
                dependencies.append(
                    NativeDependency(url=canonical_url, issue_id=issue_id)
                )
            if len(payload) < 100:
                break
            page += 1
            if page > 1000:
                raise GitHubApiError(f"{ref.url}: dependency pagination exceeded bound")
        return tuple(dependencies)

    def add_blocked_by(self, dependent: IssueRef, blocker: IssueRecord) -> None:
        repo = urllib.parse.quote(self._repo_name(dependent.repository), safe="")
        self._request_json(
            "POST",
            f"/repos/{ORGANIZATION}/{repo}/issues/{dependent.number}"
            "/dependencies/blocked_by",
            payload={"issue_id": blocker.issue_id},
        )

    def remove_blocked_by(
        self, dependent: IssueRef, blocker: NativeDependency
    ) -> None:
        repo = urllib.parse.quote(self._repo_name(dependent.repository), safe="")
        self._request_json(
            "DELETE",
            f"/repos/{ORGANIZATION}/{repo}/issues/{dependent.number}"
            f"/dependencies/blocked_by/{blocker.issue_id}",
        )


def _verify_execution_environment(env: Mapping[str, str]) -> str:
    repository = env.get("GITHUB_REPOSITORY", "")
    ref = env.get("GITHUB_REF", "")
    event = env.get("GITHUB_EVENT_NAME", "")
    sha = env.get("GITHUB_SHA", "")
    if repository != EXPECTED_REPOSITORY:
        raise DependencySyncError("dependency sync requires the central repository")
    if ref != EXPECTED_REF:
        raise DependencySyncError("dependency sync requires protected main")
    if event not in SUPPORTED_EVENTS:
        raise DependencySyncError(f"unsupported dependency sync event: {event!r}")
    if not _SHA_RE.fullmatch(sha):
        raise DependencySyncError("dependency sync requires an exact central SHA")
    token = env.get("GH_TOKEN", "")
    if not token or token.strip() != token or "\n" in token or "\r" in token:
        raise GitHubApiError("GH_TOKEN is missing or malformed")
    return token


def run(
    *,
    env: Mapping[str, str],
    gateway_factory: type[GitHubRestGateway] = GitHubRestGateway,
) -> dict[str, int]:
    token = _verify_execution_environment(env)
    gateway = gateway_factory(token)
    schema_path = ROOT / "contracts" / "issue-dependencies.schema.json"
    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise DependencySyncError("checked-in issue dependency schema is unreadable") from None
    if not isinstance(schema, Mapping):
        raise DependencySyncError("checked-in issue dependency schema is not an object")
    return sync_organization(gateway, schema).as_dict()


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args:
        print("::error::issue dependency sync accepts no arguments", file=sys.stderr)
        return 2
    try:
        summary = run(env=os.environ)
    except DependencySyncError as exc:
        print(f"::error::issue dependency sync failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(summary, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
