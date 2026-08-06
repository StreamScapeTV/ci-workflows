"""Bounded HTTP clients for the Agent State command workflow."""
from __future__ import annotations

import hashlib
import ipaddress
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, Mapping

from .agent_state_contract import (
    Command,
    CommandFailure,
    HttpStatusError,
    SHA_PATTERN,
    TargetContext,
    require,
)

LIFECYCLE_MARKER = "<!-- agent-state-request:v1 -->"
LIFECYCLE_CONTEXT_ENDPOINT = "/api/github/lifecycle/context"
LIFECYCLE_EXECUTE_ENDPOINT = "/api/github/lifecycle/execute"
LOCK_BUSY_STATUS = 423


def _is_loopback(hostname: str) -> bool:
    if hostname.casefold() == "localhost":
        return True
    try:
        return ipaddress.ip_address(hostname).is_loopback
    except ValueError:
        return False


def normalize_api_url(value: object) -> str:
    require(isinstance(value, str) and value, "agent_state_api_url_missing")
    require(value == value.strip(), "agent_state_api_url_invalid")
    require(
        not any(ord(character) < 32 or ord(character) == 127 for character in value),
        "agent_state_api_url_invalid",
    )
    try:
        parsed = urllib.parse.urlsplit(value)
    except ValueError as error:
        raise CommandFailure("agent_state_api_url_invalid") from error
    require(parsed.scheme in {"http", "https"}, "agent_state_api_url_invalid")
    require(parsed.hostname is not None and parsed.netloc, "agent_state_api_url_invalid")
    require(
        parsed.username is None and parsed.password is None and "@" not in parsed.netloc,
        "agent_state_api_url_invalid",
    )
    require(not parsed.query and not parsed.fragment, "agent_state_api_url_invalid")
    require(parsed.path in {"", "/"}, "agent_state_api_url_invalid")
    require(
        not parsed.netloc.endswith(":") and "\\" not in parsed.netloc,
        "agent_state_api_url_invalid",
    )
    try:
        port = parsed.port
    except ValueError as error:
        raise CommandFailure("agent_state_api_url_invalid") from error
    require(port is None or port > 0, "agent_state_api_url_invalid")
    hostname = parsed.hostname
    require(
        hostname is not None and not any(character.isspace() for character in hostname),
        "agent_state_api_url_invalid",
    )
    require(
        parsed.scheme == "https"
        or _is_loopback(hostname)
        or hostname.endswith((".svc", ".svc.cluster.local", ".internal", ".local")),
        "agent_state_api_url_requires_private_or_https",
    )
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, "", "", "")).rstrip("/")


def _parse_body(body: bytes) -> Any:
    if not body:
        return None
    try:
        return json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None


class JsonHttpClient:
    def __init__(
        self,
        *,
        opener: Callable[..., Any] = urllib.request.urlopen,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self.opener = opener
        self.sleeper = sleeper

    def request(
        self,
        method: str,
        url: str,
        *,
        service: str,
        token: str | None = None,
        payload: Mapping[str, Any] | None = None,
        timeout: int = 30,
    ) -> tuple[int, Mapping[str, str], Any]:
        data = None
        headers = {
            "Accept": "application/json",
            "User-Agent": "StreamScapeTV-agent-state-command-v1",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        if service == "GitHub":
            headers.update(
                {
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                }
            )
        if payload is not None:
            data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with self.opener(request, timeout=timeout) as response:
                return (
                    int(response.status),
                    dict(response.headers),
                    _parse_body(response.read()),
                )
        except urllib.error.HTTPError as error:
            raise HttpStatusError(
                service,
                error.code,
                dict(error.headers),
                _parse_body(error.read()),
            ) from error
        except urllib.error.URLError as error:
            raise CommandFailure(
                f"{service.casefold().replace(' ', '_')}_unavailable",
                decision="error",
                exit_code=3,
            ) from error


class GitHubClient:
    def __init__(self, token: str, api_url: str, http: JsonHttpClient) -> None:
        require(bool(token), "agent_state_github_token_missing")
        self.token = token
        self.api_url = api_url.rstrip("/")
        self.http = http

    def _get(self, path: str) -> Mapping[str, Any]:
        _, _, payload = self.http.request(
            "GET", self.api_url + path, service="GitHub", token=self.token
        )
        require(isinstance(payload, dict), "github_metadata_invalid")
        return payload

    def repository(self, repository: str) -> Mapping[str, Any]:
        encoded = urllib.parse.quote(repository, safe="/")
        payload = self._get(f"/repos/{encoded}")
        require(payload.get("full_name") == repository, "github_repository_binding_mismatch")
        require(payload.get("archived") is not True, "target_repository_is_archived")
        return payload

    def branch_sha(self, repository: str, branch: str) -> str:
        repo = urllib.parse.quote(repository, safe="/")
        ref = urllib.parse.quote(branch, safe="")
        payload = self._get(f"/repos/{repo}/branches/{ref}")
        sha = (payload.get("commit") or {}).get("sha")
        require(
            isinstance(sha, str) and SHA_PATTERN.fullmatch(sha.casefold()) is not None,
            "github_integration_sha_invalid",
        )
        return sha.casefold()

    def issue(self, repository: str, number: int) -> Mapping[str, Any]:
        repo = urllib.parse.quote(repository, safe="/")
        payload = self._get(f"/repos/{repo}/issues/{number}")
        require(payload.get("number") == number, "github_issue_binding_mismatch")
        require(not payload.get("pull_request"), "origin_issue_is_pull_request")
        require(payload.get("state") in {"open", "closed"}, "github_issue_state_invalid")
        return payload

    def pull(self, repository: str, number: int) -> Mapping[str, Any]:
        repo = urllib.parse.quote(repository, safe="/")
        payload = self._get(f"/repos/{repo}/pulls/{number}")
        require(payload.get("number") == number, "github_pull_binding_mismatch")
        require(payload.get("state") in {"open", "closed"}, "github_pull_state_invalid")
        return payload


class AgentStateClient:
    def __init__(
        self,
        api_url: str,
        token: str | None,
        http: JsonHttpClient,
        *,
        retry_attempts: int,
        retry_after_max: int,
    ) -> None:
        self.api_url = normalize_api_url(api_url)
        self.token = token or None
        self.http = http
        self.retry_attempts = retry_attempts
        self.retry_after_max = retry_after_max

    def _retry_delay(self, error: HttpStatusError) -> int:
        header = error.headers.get("Retry-After")
        if isinstance(header, str) and header.isdigit():
            return max(1, min(int(header), self.retry_after_max))
        payload = error.payload
        value = payload.get("retry_after_seconds") if isinstance(payload, dict) else None
        return max(1, min(value, self.retry_after_max)) if isinstance(value, int) else 2

    def request(
        self,
        method: str,
        path: str,
        *,
        payload: Mapping[str, Any] | None = None,
    ) -> Mapping[str, Any]:
        last_retry: Mapping[str, Any] | None = None
        for attempt in range(1, self.retry_attempts + 1):
            try:
                _, _, value = self.http.request(
                    method,
                    self.api_url + path,
                    service="Agent State",
                    token=self.token,
                    payload=payload,
                )
                require(isinstance(value, dict), "agent_state_result_invalid")
                return value
            except HttpStatusError as error:
                retry = error.payload
                if (
                    error.status == LOCK_BUSY_STATUS
                    and isinstance(retry, dict)
                    and retry.get("decision") == "retry"
                    and retry.get("retryable") is True
                ):
                    last_retry = retry
                    if attempt < self.retry_attempts:
                        self.http.sleeper(self._retry_delay(error))
                        continue
                    break
                if isinstance(retry, dict) and error.status < 500:
                    return retry
                raise
        raise CommandFailure(
            str((last_retry or {}).get("instruction") or "agent_state_retry_budget_exhausted"),
            decision="retry",
            retryable=True,
            exit_code=75,
        )

    def context(self, repository: str, project: str) -> Mapping[str, Any]:
        query = urllib.parse.urlencode({"repository": repository, "project": project})
        return self.request("GET", f"{LIFECYCLE_CONTEXT_ENDPOINT}?{query}")

    def direct(self, command: Command, target: TargetContext) -> Mapping[str, Any]:
        project = urllib.parse.quote(command.project, safe="")
        agent_id = urllib.parse.quote(command.agent_id or "", safe="")
        github = {
            "repository": command.repository,
            "issue_number": command.issue_number,
            "comment_id": synthetic_command_id(command.request_id),
            "actor": target.actor,
        }
        if command.action == "start":
            payload: dict[str, Any] = {
                "agent_id": command.agent_id,
                "session_name": command.session_name,
                "task": command.task,
                "files": list(command.files),
                "packages": list(command.packages),
                "claim_type": command.claim_type,
                "claim_mode": command.claim_mode,
                "allow_warnings": False,
                "branch": command.branch,
                "request_id": command.request_id,
                "github": {**github, "base_sha": target.integration_sha},
            }
            path = f"/api/projects/{project}/agents/start"
        elif command.action == "claim":
            payload = {
                "files": list(command.files),
                "packages": list(command.packages),
                "claim_type": command.claim_type,
                "claim_mode": command.claim_mode,
                "allow_warnings": False,
                "request_id": command.request_id,
                "github": github,
            }
            path = f"/api/projects/{project}/agents/{agent_id}/claim"
        elif command.action == "release":
            payload = {
                "files": list(command.files),
                "packages": list(command.packages),
                "request_id": command.request_id,
                "github": github,
            }
            path = f"/api/projects/{project}/agents/{agent_id}/release"
        elif command.action in {"block", "cancel"}:
            payload = {
                "reason": command.reason,
                "request_id": command.request_id,
                "github": github,
            }
            path = f"/api/projects/{project}/agents/{agent_id}/{command.action}"
        elif command.action in {"review", "done"}:
            payload = {
                "summary": command.summary,
                "request_id": command.request_id,
                "github": github,
            }
            path = f"/api/projects/{project}/agents/{agent_id}/{command.action}"
        else:  # pragma: no cover
            raise CommandFailure("unsupported_direct_action")
        return self.request("POST", path, payload=payload)

    def lifecycle_compat(
        self,
        command: Command,
        target: TargetContext,
    ) -> Mapping[str, Any]:
        body: dict[str, Any] = {"action": command.action}
        if command.action == "resume":
            body["session_name"] = command.session_name
        elif command.action == "reconcile_base":
            body.update({"agent_id": command.agent_id, "branch": command.branch})
        else:  # pragma: no cover
            raise CommandFailure("unsupported_lifecycle_compat_action")
        payload = {
            "contract_version": 1,
            "project": command.project,
            "repository": command.repository,
            "integration_ref": target.integration_ref,
            "integration_sha": target.integration_sha,
            "issue_number": command.issue_number or 1,
            "issue_state": target.issue_state or "open",
            "issue_is_pull_request": False,
            "comment_id": synthetic_command_id(command.request_id),
            "comment_actor": target.actor,
            "author_association": "OWNER",
            "comment_body": LIFECYCLE_MARKER
            + "\n"
            + json.dumps(body, separators=(",", ":")),
        }
        return self.request("POST", LIFECYCLE_EXECUTE_ENDPOINT, payload=payload)


def synthetic_command_id(request_id: str) -> int:
    """Map an explicit request ID to a stable high-range lifecycle evidence ID."""

    digest = hashlib.sha256(request_id.encode("utf-8")).digest()
    return (int.from_bytes(digest[:8], "big") & ((1 << 62) - 1)) | (1 << 62)
