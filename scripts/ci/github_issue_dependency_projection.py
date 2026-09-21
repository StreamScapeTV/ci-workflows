#!/usr/bin/env python3
"""Apply one claimed Agent State issue-dependency projection to GitHub."""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

REPOSITORY = re.compile(r"StreamScapeTV/(?P<name>[A-Za-z0-9_.-]{1,100})\Z")
MAX_ISSUE_NUMBER = 9223372036854775807
MAX_BLOCKED_BY_PAGES = 10
PAGE_SIZE = 100
HTTP_TIMEOUT_SECONDS = 30
CLAIM_FILE = Path(".github-issue-dependency-projection.json")
GITHUB_API = "https://api.github.com"


class ProjectionError(RuntimeError):
    def __init__(self, error_code: str, *, retryable: bool) -> None:
        super().__init__(error_code)
        self.error_code = error_code
        self.retryable = retryable


@dataclass(frozen=True)
class IssueIdentity:
    project_key: str
    issue_number: int
    repository_full_name: str


@dataclass(frozen=True)
class Projection:
    projection_id: str
    claim_token: str
    operation: str
    dependent: IssueIdentity
    blocker: IssueIdentity

    @property
    def cross_repository(self) -> bool:
        return self.dependent.repository_full_name != self.blocker.repository_full_name


class GitHubLike(Protocol):
    def resolve_open_issue(self, identity: IssueIdentity) -> int: ...
    def blocked_by_ids(self, dependent: IssueIdentity) -> set[int]: ...
    def add_blocker(self, dependent: IssueIdentity, blocker_id: int, *, cross_repository: bool) -> None: ...
    def remove_blocker(self, dependent: IssueIdentity, blocker_id: int, *, cross_repository: bool) -> None: ...


def _positive_issue_number(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= MAX_ISSUE_NUMBER:
        raise ProjectionError("validation_failed", retryable=False)
    return value


def _uuid(value: object) -> str:
    if not isinstance(value, str):
        raise ProjectionError("validation_failed", retryable=False)
    try:
        parsed = uuid.UUID(value)
    except ValueError:
        raise ProjectionError("validation_failed", retryable=False) from None
    if str(parsed) != value.lower():
        raise ProjectionError("validation_failed", retryable=False)
    return str(parsed)


def _identity(value: object) -> IssueIdentity:
    if not isinstance(value, dict):
        raise ProjectionError("validation_failed", retryable=False)
    project_key = value.get("project_key")
    repository = value.get("repository_full_name")
    if not isinstance(project_key, str) or not project_key or len(project_key.encode("utf-8")) > 128:
        raise ProjectionError("validation_failed", retryable=False)
    if not isinstance(repository, str) or REPOSITORY.fullmatch(repository) is None:
        raise ProjectionError("validation_failed", retryable=False)
    return IssueIdentity(project_key, _positive_issue_number(value.get("issue_number")), repository)


def parse_projection(value: object) -> Projection:
    if not isinstance(value, dict):
        raise ProjectionError("validation_failed", retryable=False)
    operation = value.get("operation")
    desired_active = value.get("desired_active")
    if not isinstance(operation, str) or operation not in {"add", "remove"}:
        raise ProjectionError("validation_failed", retryable=False)
    if not isinstance(desired_active, bool) or desired_active != (operation == "add"):
        raise ProjectionError("validation_failed", retryable=False)
    if value.get("status") != "claimed" or value.get("max_attempts") != 8:
        raise ProjectionError("validation_failed", retryable=False)
    attempts = value.get("attempt_count")
    if isinstance(attempts, bool) or not isinstance(attempts, int) or not 1 <= attempts <= 8:
        raise ProjectionError("validation_failed", retryable=False)
    return Projection(
        projection_id=_uuid(value.get("projection_id")),
        claim_token=_uuid(value.get("claim_token")),
        operation=operation,
        dependent=_identity(value.get("dependent")),
        blocker=_identity(value.get("blocker")),
    )


def result_for(error: ProjectionError | None = None) -> dict[str, str]:
    if error is None:
        return {"outcome": "succeeded"}
    return {
        "outcome": "retry" if error.retryable else "failed",
        "error_code": error.error_code,
    }


@dataclass
class AgentStateApi:
    url: str
    secret: str
    opener: object = urllib.request

    def _rpc(self, function: str, payload: dict[str, object]) -> dict[str, object]:
        request = urllib.request.Request(
            f"{self.url.rstrip('/')}/rest/v1/rpc/{function}",
            data=json.dumps(payload, separators=(",", ":")).encode(),
            method="POST",
            headers={
                "apikey": self.secret,
                "Content-Type": "application/json",
                "Content-Profile": "agent_api",
                "Accept-Profile": "agent_api",
            },
        )
        try:
            with self.opener.urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:  # type: ignore[attr-defined]
                raw = response.read()
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"Agent State RPC was refused with HTTP {exc.code}") from None
        except (OSError, urllib.error.URLError):
            raise RuntimeError("Agent State RPC request failed") from None
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise RuntimeError("Agent State RPC returned invalid JSON") from None
        if not isinstance(value, dict):
            raise RuntimeError("Agent State RPC returned non-object JSON")
        return value

    def claim(self) -> dict[str, object]:
        value = self._rpc("claim_github_issue_dependency_projection", {})
        if value.get("ok") is not True or value.get("code") not in {"work", "no_work"}:
            raise RuntimeError("Agent State projection claim returned an invalid response")
        return value

    def finish(self, projection: Projection, result: dict[str, str]) -> None:
        value = self._rpc(
            "finish_github_issue_dependency_projection",
            {
                "p_projection_id": projection.projection_id,
                "p_claim_token": projection.claim_token,
                "p_result": result,
            },
        )
        if value.get("ok") is not True or value.get("code") not in {
            "completed", "retry_scheduled", "failed"
        }:
            raise RuntimeError("Agent State projection finish returned an invalid response")


@dataclass
class GitHubApi:
    token: str
    opener: object = urllib.request

    def _request_json(
        self,
        url: str,
        *,
        method: str = "GET",
        payload: dict[str, object] | None = None,
        validation_code: str = "validation_failed",
        allow_empty: bool = False,
    ) -> Any:
        data = None if payload is None else json.dumps(payload, separators=(",", ":")).encode()
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2026-03-10",
        }
        if data is not None:
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with self.opener.urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:  # type: ignore[attr-defined]
                raw = response.read()
        except urllib.error.HTTPError as exc:
            remaining = exc.headers.get("X-RateLimit-Remaining", "") if exc.headers else ""
            if exc.code == 429 or (exc.code == 403 and remaining == "0"):
                raise ProjectionError("rate_limited", retryable=True) from None
            if exc.code in {408, 425}:
                raise ProjectionError("transient", retryable=True) from None
            if 500 <= exc.code <= 599:
                raise ProjectionError("server_error", retryable=True) from None
            if exc.code in {401, 403}:
                raise ProjectionError("permission_denied", retryable=False) from None
            if exc.code == 404:
                raise ProjectionError("not_found", retryable=False) from None
            if exc.code == 410:
                raise ProjectionError("gone", retryable=False) from None
            if exc.code in {400, 422}:
                raise ProjectionError(validation_code, retryable=False) from None
            raise ProjectionError("permanent_error", retryable=False) from None
        except (OSError, urllib.error.URLError):
            raise ProjectionError("network_error", retryable=True) from None
        if not raw:
            if allow_empty:
                return None
            raise ProjectionError("server_error", retryable=True)
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ProjectionError("server_error", retryable=True) from None

    @staticmethod
    def _repository_path(repository_full_name: str) -> str:
        owner, name = repository_full_name.split("/", 1)
        return f"{urllib.parse.quote(owner, safe='')}/{urllib.parse.quote(name, safe='')}"

    def resolve_open_issue(self, identity: IssueIdentity) -> int:
        repository = self._repository_path(identity.repository_full_name)
        value = self._request_json(f"{GITHUB_API}/repos/{repository}/issues/{identity.issue_number}")
        if not isinstance(value, dict):
            raise ProjectionError("server_error", retryable=True)
        if value.get("state") != "open":
            raise ProjectionError("gone", retryable=False)
        if "pull_request" in value:
            raise ProjectionError("validation_failed", retryable=False)
        issue_id = value.get("id")
        number = value.get("number")
        if isinstance(issue_id, bool) or not isinstance(issue_id, int) or issue_id <= 0:
            raise ProjectionError("server_error", retryable=True)
        expected_repository_url = f"{GITHUB_API}/repos/{identity.repository_full_name}"
        if number != identity.issue_number or value.get("repository_url") != expected_repository_url:
            raise ProjectionError("validation_failed", retryable=False)
        return issue_id

    def blocked_by_ids(self, dependent: IssueIdentity) -> set[int]:
        repository = self._repository_path(dependent.repository_full_name)
        values: set[int] = set()
        for page in range(1, MAX_BLOCKED_BY_PAGES + 1):
            query = urllib.parse.urlencode({"per_page": PAGE_SIZE, "page": page})
            response = self._request_json(
                f"{GITHUB_API}/repos/{repository}/issues/{dependent.issue_number}/dependencies/blocked_by?{query}"
            )
            if not isinstance(response, list):
                raise ProjectionError("server_error", retryable=True)
            for item in response:
                if not isinstance(item, dict):
                    raise ProjectionError("server_error", retryable=True)
                issue_id = item.get("id")
                if isinstance(issue_id, bool) or not isinstance(issue_id, int) or issue_id <= 0:
                    raise ProjectionError("server_error", retryable=True)
                values.add(issue_id)
            if len(response) < PAGE_SIZE:
                return values
        raise ProjectionError("permanent_error", retryable=False)

    def add_blocker(self, dependent: IssueIdentity, blocker_id: int, *, cross_repository: bool) -> None:
        repository = self._repository_path(dependent.repository_full_name)
        self._request_json(
            f"{GITHUB_API}/repos/{repository}/issues/{dependent.issue_number}/dependencies/blocked_by",
            method="POST",
            payload={"issue_id": blocker_id},
            validation_code="unsupported_cross_repository" if cross_repository else "validation_failed",
        )

    def remove_blocker(self, dependent: IssueIdentity, blocker_id: int, *, cross_repository: bool) -> None:
        repository = self._repository_path(dependent.repository_full_name)
        self._request_json(
            f"{GITHUB_API}/repos/{repository}/issues/{dependent.issue_number}/dependencies/blocked_by/{blocker_id}",
            method="DELETE",
            validation_code="unsupported_cross_repository" if cross_repository else "validation_failed",
            allow_empty=True,
        )


def execute_projection(projection: Projection, github: GitHubLike) -> dict[str, str]:
    try:
        github.resolve_open_issue(projection.dependent)
        blocker_id = github.resolve_open_issue(projection.blocker)
        current = github.blocked_by_ids(projection.dependent)
        if projection.operation == "add":
            if blocker_id not in current:
                github.add_blocker(
                    projection.dependent,
                    blocker_id,
                    cross_repository=projection.cross_repository,
                )
            if blocker_id not in github.blocked_by_ids(projection.dependent):
                raise ProjectionError("readback_mismatch", retryable=True)
        else:
            if blocker_id in current:
                github.remove_blocker(
                    projection.dependent,
                    blocker_id,
                    cross_repository=projection.cross_repository,
                )
            if blocker_id in github.blocked_by_ids(projection.dependent):
                raise ProjectionError("readback_mismatch", retryable=True)
    except ProjectionError as exc:
        return result_for(exc)
    return result_for()


def _required_environment(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        raise SystemExit(f"{name} is required")
    return value


def _write_output(name: str, value: str) -> None:
    output = _required_environment("GITHUB_OUTPUT")
    with open(output, "a", encoding="utf-8") as stream:
        stream.write(f"{name}={value}\n")


def claim_command() -> int:
    state = AgentStateApi(
        _required_environment("AGENT_STATE_SUPABASE_URL"),
        _required_environment("AGENT_STATE_SUPABASE_SECRET_KEY"),
    )
    response = state.claim()
    if response["code"] == "no_work":
        CLAIM_FILE.unlink(missing_ok=True)
        _write_output("has_work", "false")
        return 0
    projection = parse_projection(response.get("projection"))
    CLAIM_FILE.write_text(
        json.dumps(response["projection"], sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    CLAIM_FILE.chmod(0o600)
    repositories = sorted(
        {
            projection.dependent.repository_full_name.split("/", 1)[1],
            projection.blocker.repository_full_name.split("/", 1)[1],
        }
    )
    _write_output("repositories", ",".join(repositories))
    _write_output("has_work", "true")
    return 0


def apply_command() -> int:
    state = AgentStateApi(
        _required_environment("AGENT_STATE_SUPABASE_URL"),
        _required_environment("AGENT_STATE_SUPABASE_SECRET_KEY"),
    )
    token = _required_environment("GITHUB_ISSUES_TOKEN")
    try:
        projection = parse_projection(json.loads(CLAIM_FILE.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit("claimed projection file is unavailable or invalid") from exc
    result = execute_projection(projection, GitHubApi(token))
    state.finish(projection, result)
    CLAIM_FILE.unlink(missing_ok=True)
    print(json.dumps({"outcome": result["outcome"], "error_code": result.get("error_code")}, sort_keys=True))
    return 0 if result["outcome"] == "succeeded" else 1


def main(argv: list[str]) -> int:
    if argv == ["claim"]:
        return claim_command()
    if argv == ["apply"]:
        return apply_command()
    raise SystemExit("usage: github_issue_dependency_projection.py claim|apply")


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
