#!/usr/bin/env python3
"""Reconcile private Google Drive CI logs against current Agent State CI-run truth."""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Iterable, Protocol

FOLDER_MIME = "application/vnd.google-apps.folder"
OWNER = "StreamScapeTV"
REPOSITORY_NAME = re.compile(r"[A-Za-z0-9_.-]{1,100}\Z")
LOG_FILENAME = re.compile(
    r"(?P<run>[1-9][0-9]{0,18})-(?P<attempt>[1-9][0-9]{0,9})"
    r"(?:-[A-Za-z0-9][A-Za-z0-9._-]{0,127})?\.txt\Z"
)
MAX_BATCH = 500
MAX_PAGES = 100
MAX_CHILDREN_PER_PARENT = 10_000
MAX_DISCOVERED_LOGS = 50_000
HTTP_TIMEOUT_SECONDS = 30


class ReconcileError(RuntimeError):
    pass


def _query_literal(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


def _valid_ref(value: str) -> bool:
    return bool(value) and len(value.encode("utf-8")) <= 512 and not any(ch in value for ch in ("\x00", "\r", "\n"))


@dataclass(frozen=True, order=True)
class ExecutionIdentity:
    repository: str
    ref: str
    external_run_id: int
    external_run_attempt: int

    def payload(self) -> dict[str, object]:
        return {
            "repository": self.repository,
            "ref": self.ref,
            "external_run_id": self.external_run_id,
            "external_run_attempt": self.external_run_attempt,
        }


@dataclass(frozen=True)
class DiscoveredLog:
    identity: ExecutionIdentity
    file_id: str
    repository_folder_id: str
    ref_folder_id: str


def parse_log_filename(name: str) -> tuple[int, int] | None:
    match = LOG_FILENAME.fullmatch(name)
    if match is None:
        return None
    run_id = int(match.group("run"))
    attempt = int(match.group("attempt"))
    if run_id > 9223372036854775807 or attempt > 2147483647:
        return None
    return run_id, attempt


class DriveLike(Protocol):
    def children(self, parent_id: str) -> list[dict[str, str]]: ...
    def delete_file(self, file_id: str) -> None: ...


@dataclass
class DriveApi:
    root_folder_id: str
    client_id: str
    client_secret: str
    refresh_token: str
    opener: object = urllib.request
    _access_token: str | None = None

    def _request_json(self, request: urllib.request.Request) -> dict[str, object]:
        try:
            with self.opener.urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:  # type: ignore[attr-defined]
                raw = response.read()
        except urllib.error.HTTPError as exc:
            raise ReconcileError(f"Google API request was refused with HTTP {exc.code}") from None
        except (OSError, urllib.error.URLError):
            raise ReconcileError("Google API request failed") from None
        if not raw:
            return {}
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ReconcileError("Google API returned invalid JSON") from None
        if not isinstance(value, dict):
            raise ReconcileError("Google API returned non-object JSON")
        return value

    def access_token(self) -> str:
        if self._access_token is not None:
            return self._access_token
        encoded = urllib.parse.urlencode(
            {
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "refresh_token": self.refresh_token,
                "grant_type": "refresh_token",
            }
        ).encode()
        value = self._request_json(
            urllib.request.Request(
                "https://oauth2.googleapis.com/token",
                data=encoded,
                method="POST",
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
        )
        token = value.get("access_token")
        if not isinstance(token, str) or not token:
            raise ReconcileError("Google OAuth token response has no access token")
        self._access_token = token
        return token

    def _drive_request(self, url: str, *, method: str = "GET") -> urllib.request.Request:
        return urllib.request.Request(
            url,
            method=method,
            headers={"Authorization": f"Bearer {self.access_token()}", "Accept": "application/json"},
        )

    def children(self, parent_id: str) -> list[dict[str, str]]:
        token = ""
        values: list[dict[str, str]] = []
        for _ in range(MAX_PAGES):
            params = {
                "q": f"'{_query_literal(parent_id)}' in parents and trashed = false",
                "spaces": "drive",
                "fields": "nextPageToken,files(id,name,mimeType)",
                "pageSize": "100",
            }
            if token:
                params["pageToken"] = token
            payload = self._request_json(
                self._drive_request("https://www.googleapis.com/drive/v3/files?" + urllib.parse.urlencode(params))
            )
            files = payload.get("files", [])
            if not isinstance(files, list):
                raise ReconcileError("Google Drive child listing returned invalid files")
            for entry in files:
                if not isinstance(entry, dict):
                    raise ReconcileError("Google Drive child listing returned invalid metadata")
                file_id, name, mime = entry.get("id"), entry.get("name"), entry.get("mimeType")
                if not all(isinstance(item, str) and item for item in (file_id, name, mime)):
                    raise ReconcileError("Google Drive child listing returned incomplete metadata")
                values.append({"id": file_id, "name": name, "mimeType": mime})
                if len(values) > MAX_CHILDREN_PER_PARENT:
                    raise ReconcileError("Google Drive child listing exceeded bounded parent size")
            next_token = payload.get("nextPageToken", "")
            if not isinstance(next_token, str):
                raise ReconcileError("Google Drive child listing returned invalid pagination token")
            token = next_token
            if not token:
                return values
        raise ReconcileError("Google Drive child listing exceeded bounded pagination")

    def delete_file(self, file_id: str) -> None:
        request = self._drive_request(
            f"https://www.googleapis.com/drive/v3/files/{urllib.parse.quote(file_id, safe='')}",
            method="DELETE",
        )
        try:
            with self.opener.urlopen(request, timeout=HTTP_TIMEOUT_SECONDS):  # type: ignore[attr-defined]
                pass
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return
            raise ReconcileError(f"Google Drive deletion was refused with HTTP {exc.code}") from None
        except (OSError, urllib.error.URLError):
            raise ReconcileError("Google Drive deletion failed") from None


@dataclass
class AgentStateApi:
    url: str
    secret: str
    opener: object = urllib.request

    def filter_current(self, candidates: list[ExecutionIdentity]) -> set[ExecutionIdentity]:
        current: set[ExecutionIdentity] = set()
        for start in range(0, len(candidates), MAX_BATCH):
            batch = candidates[start : start + MAX_BATCH]
            body = json.dumps(
                {"p_candidates": [candidate.payload() for candidate in batch]},
                separators=(",", ":"),
            ).encode()
            request = urllib.request.Request(
                f"{self.url.rstrip('/')}/rest/v1/rpc/filter_current_ci_run_identities",
                data=body,
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
                raise ReconcileError(f"Agent State identity filter was refused with HTTP {exc.code}") from None
            except (OSError, urllib.error.URLError):
                raise ReconcileError("Agent State identity filter request failed") from None
            try:
                value = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                raise ReconcileError("Agent State identity filter returned invalid JSON") from None
            if not isinstance(value, dict) or value.get("ok") is not True or not isinstance(value.get("current"), list):
                raise ReconcileError("Agent State identity filter returned an invalid response")
            for item in value["current"]:
                if not isinstance(item, dict) or set(item) != {
                    "repository", "ref", "external_run_id", "external_run_attempt"
                }:
                    raise ReconcileError("Agent State identity filter returned invalid identity metadata")
                try:
                    identity = ExecutionIdentity(
                        repository=item["repository"],
                        ref=item["ref"],
                        external_run_id=item["external_run_id"],
                        external_run_attempt=item["external_run_attempt"],
                    )
                except TypeError as exc:
                    raise ReconcileError("Agent State identity filter returned malformed identity metadata") from exc
                if identity not in set(batch):
                    raise ReconcileError("Agent State identity filter returned an identity outside the request batch")
                current.add(identity)
        return current


def discover_logs(drive: DriveLike, root_folder_id: str) -> tuple[list[DiscoveredLog], set[str], set[str]]:
    logs: list[DiscoveredLog] = []
    repository_folders: set[str] = set()
    ref_folders: set[str] = set()
    for repository_entry in drive.children(root_folder_id):
        if repository_entry["mimeType"] != FOLDER_MIME or REPOSITORY_NAME.fullmatch(repository_entry["name"]) is None:
            continue
        repository_folder_id = repository_entry["id"]
        repository = f"{OWNER}/{repository_entry['name']}"
        repository_folders.add(repository_folder_id)
        for ref_entry in drive.children(repository_folder_id):
            if ref_entry["mimeType"] != FOLDER_MIME or not _valid_ref(ref_entry["name"]):
                continue
            ref_folder_id = ref_entry["id"]
            ref_folders.add(ref_folder_id)
            for file_entry in drive.children(ref_folder_id):
                if file_entry["mimeType"] == FOLDER_MIME:
                    continue
                parsed = parse_log_filename(file_entry["name"])
                if parsed is None:
                    continue
                run_id, attempt = parsed
                logs.append(
                    DiscoveredLog(
                        ExecutionIdentity(repository, ref_entry["name"], run_id, attempt),
                        file_entry["id"],
                        repository_folder_id,
                        ref_folder_id,
                    )
                )
                if len(logs) > MAX_DISCOVERED_LOGS:
                    raise ReconcileError("private CI-log discovery exceeded bounded log count")
    return logs, repository_folders, ref_folders


def _chunks(values: Iterable[ExecutionIdentity]) -> list[ExecutionIdentity]:
    return sorted(set(values))


def reconcile(drive: DriveLike, state: AgentStateApi, *, root_folder_id: str) -> dict[str, int]:
    logs, repository_folders, ref_folders = discover_logs(drive, root_folder_id)
    candidates = _chunks(log.identity for log in logs)
    current = state.filter_current(candidates) if candidates else set()

    deleted_files = 0
    touched_refs: set[str] = set()
    touched_repositories: set[str] = set()
    for log in logs:
        if log.identity in current:
            continue
        drive.delete_file(log.file_id)
        deleted_files += 1
        touched_refs.add(log.ref_folder_id)
        touched_repositories.add(log.repository_folder_id)

    deleted_ref_folders = 0
    for folder_id in sorted(touched_refs & ref_folders):
        if not drive.children(folder_id):
            drive.delete_file(folder_id)
            deleted_ref_folders += 1

    deleted_repository_folders = 0
    for folder_id in sorted(touched_repositories & repository_folders):
        if folder_id != root_folder_id and not drive.children(folder_id):
            drive.delete_file(folder_id)
            deleted_repository_folders += 1

    return {
        "discovered_files": len(logs),
        "candidate_identities": len(candidates),
        "current_identities": len(current),
        "deleted_files": deleted_files,
        "deleted_ref_folders": deleted_ref_folders,
        "deleted_repository_folders": deleted_repository_folders,
    }


def main() -> int:
    root = os.environ.get("GOOGLE_DRIVE_CI_LOGS_FOLDER_ID", "")
    client_id = os.environ.get("GOOGLE_DRIVE_CLIENT_ID", "")
    client_secret = os.environ.get("GOOGLE_DRIVE_CLIENT_SECRET", "")
    refresh_token = os.environ.get("GOOGLE_DRIVE_REFRESH_TOKEN", "")
    agent_state_url = os.environ.get("AGENT_STATE_SUPABASE_URL", "")
    agent_state_secret = os.environ.get("AGENT_STATE_SUPABASE_SECRET_KEY", "")
    if not all((root, client_id, client_secret, refresh_token, agent_state_url, agent_state_secret)):
        raise SystemExit("CI-log retention requires fixed Agent State and Google Drive configuration")
    drive = DriveApi(root, client_id, client_secret, refresh_token)
    state = AgentStateApi(agent_state_url, agent_state_secret)
    result = reconcile(drive, state, root_folder_id=root)
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
