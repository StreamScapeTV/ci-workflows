#!/usr/bin/env python3
"""Bounded deletion of one Google Drive repository/ref source snapshot."""

from __future__ import annotations

import argparse
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

FOLDER_MIME = "application/vnd.google-apps.folder"
SHA40 = re.compile(r"[0-9a-f]{40}")
REPOSITORY = re.compile(r"StreamScapeTV/[A-Za-z0-9_.-]{1,100}")


class SnapshotDeleteError(RuntimeError):
    pass


def validate_request(repository: str, ref: str, expected_source_sha: str) -> None:
    if not REPOSITORY.fullmatch(repository):
        raise SnapshotDeleteError("snapshot deletion repository must be one bounded StreamScapeTV owner/name")
    if ref in {"main", "develop"}:
        raise SnapshotDeleteError("snapshot deletion refuses protected integration ref names")
    if ref.startswith(("refs/heads/", "refs/tags/")):
        raise SnapshotDeleteError("snapshot deletion requires one logical ref name")
    if not ref or len(ref.encode("utf-8")) > 255 or "\x00" in ref:
        raise SnapshotDeleteError("snapshot deletion ref name is invalid")
    if expected_source_sha and not SHA40.fullmatch(expected_source_sha):
        raise SnapshotDeleteError("snapshot deletion expected source SHA must be one lowercase 40-character Git SHA")


def _query_literal(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


@dataclass
class DriveClient:
    access_token: str
    api_root: str = "https://www.googleapis.com/drive/v3"
    max_pages: int = 10

    def _request(
        self,
        path: str,
        *,
        method: str = "GET",
        json_body: dict[str, Any] | None = None,
    ) -> bytes:
        data = None
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Accept": "application/json",
        }
        if json_body is not None:
            data = json.dumps(json_body, separators=(",", ":")).encode("utf-8")
            headers["Content-Type"] = "application/json; charset=UTF-8"
        request = urllib.request.Request(
            self.api_root.rstrip("/") + path,
            method=method,
            headers=headers,
            data=data,
        )
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            raise SnapshotDeleteError(f"Google Drive snapshot deletion request was refused with HTTP {exc.code}") from None
        except urllib.error.URLError:
            raise SnapshotDeleteError("Google Drive snapshot deletion request failed") from None

    def _json(self, path: str, *, method: str = "GET", json_body: dict[str, Any] | None = None) -> Any:
        body = self._request(path, method=method, json_body=json_body)
        try:
            return json.loads(body or b"{}")
        except json.JSONDecodeError:
            raise SnapshotDeleteError("Google Drive snapshot deletion returned invalid JSON") from None

    def list_query(self, clauses: list[str]) -> list[dict[str, Any]]:
        page_token = ""
        values: list[dict[str, Any]] = []
        for _ in range(self.max_pages):
            params = {
                "q": " and ".join(clauses),
                "spaces": "drive",
                "pageSize": "100",
                "fields": "nextPageToken,files(id,name,mimeType,parents,trashed)",
            }
            if page_token:
                params["pageToken"] = page_token
            payload = self._json("/files?" + urllib.parse.urlencode(params))
            files = payload.get("files") if isinstance(payload, dict) else None
            if not isinstance(files, list):
                raise SnapshotDeleteError("Google Drive snapshot deletion returned invalid file metadata")
            for value in files:
                if not isinstance(value, dict) or not value.get("id") or not value.get("name") or not value.get("mimeType"):
                    raise SnapshotDeleteError("Google Drive snapshot deletion returned incomplete file metadata")
                values.append(value)
            page_token = payload.get("nextPageToken", "") if isinstance(payload, dict) else ""
            if not page_token:
                return values
            if not isinstance(page_token, str):
                raise SnapshotDeleteError("Google Drive snapshot deletion returned invalid pagination metadata")
        raise SnapshotDeleteError("Google Drive snapshot deletion exceeded bounded pagination limit")

    def exact_folders(self, parent: str, name: str) -> list[dict[str, Any]]:
        return self.list_query(
            [
                f"'{_query_literal(parent)}' in parents",
                f"name = '{_query_literal(name)}'",
                "trashed = false",
                f"mimeType = '{FOLDER_MIME}'",
            ]
        )

    def children(self, parent: str) -> list[dict[str, Any]]:
        return self.list_query(
            [
                f"'{_query_literal(parent)}' in parents",
                "trashed = false",
            ]
        )

    def media(self, file_id: str) -> bytes:
        return self._request(f"/files/{urllib.parse.quote(file_id, safe='')}?alt=media")

    def trash(self, file_id: str) -> None:
        payload = self._json(
            f"/files/{urllib.parse.quote(file_id, safe='')}?fields=id,trashed",
            method="PATCH",
            json_body={"trashed": True},
        )
        if not isinstance(payload, dict) or payload.get("id") != file_id or payload.get("trashed") is not True:
            raise SnapshotDeleteError("Google Drive snapshot deletion could not verify trashed ref folder")


def _unique_folder(values: list[dict[str, Any]], label: str, *, allow_missing: bool) -> dict[str, Any] | None:
    if not values:
        if allow_missing:
            return None
        raise SnapshotDeleteError(f"Google Drive snapshot deletion could not find the canonical {label} folder")
    if len(values) != 1:
        raise SnapshotDeleteError(f"Google Drive snapshot deletion found ambiguous {label} folders")
    return values[0]


def _validate_manifest(
    raw: bytes,
    *,
    repository: str,
    ref: str,
    expected_source_sha: str,
    ref_folder_id: str,
    manifest_id: str,
    archive: dict[str, Any] | None,
) -> None:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        raise SnapshotDeleteError("Google Drive snapshot manifest is invalid JSON") from None
    if not isinstance(value, dict):
        raise SnapshotDeleteError("Google Drive snapshot manifest must be one JSON object")
    if value.get("repository") != repository or value.get("requested_ref") != ref:
        raise SnapshotDeleteError("Google Drive snapshot manifest repository/ref identity mismatch")
    if value.get("is_tag") is not False:
        raise SnapshotDeleteError("Google Drive snapshot deletion refuses tag snapshots")
    source_sha = value.get("resolved_source_sha")
    if not isinstance(source_sha, str) or not SHA40.fullmatch(source_sha):
        raise SnapshotDeleteError("Google Drive snapshot manifest has invalid resolved source SHA")
    if expected_source_sha and source_sha != expected_source_sha:
        raise SnapshotDeleteError("Google Drive snapshot manifest source SHA does not match expected source SHA")
    # Embedded Drive IDs are descriptive snapshot-era metadata, not deletion
    # authority. Older copied/migrated snapshots can legitimately retain IDs
    # from the original Drive objects. The live bounded parent/name path and
    # manifest repository/ref/source identity above are authoritative.
    if archive is None:
        return

    archive_name = archive["name"]
    declared_name = value.get("archive_filename")
    if declared_name is not None and declared_name != archive_name:
        raise SnapshotDeleteError("Google Drive snapshot manifest archive filename mismatch")
    if declared_name is None and archive_name != "source.zip":
        raise SnapshotDeleteError("legacy Google Drive snapshot archive must be named source.zip")


def delete_snapshot(
    client: DriveClient,
    *,
    root_folder_id: str,
    repository: str,
    ref: str,
    expected_source_sha: str = "",
) -> str:
    validate_request(repository, ref, expected_source_sha)
    if not root_folder_id:
        raise SnapshotDeleteError("Google Drive repositories root folder ID is required")

    repository_name = repository.rsplit("/", 1)[1]
    repository_folder = _unique_folder(
        client.exact_folders(root_folder_id, repository_name),
        "repository",
        allow_missing=False,
    )
    assert repository_folder is not None

    ref_folder = _unique_folder(
        client.exact_folders(repository_folder["id"], ref),
        "ref",
        allow_missing=True,
    )
    if ref_folder is None:
        return "already-absent"

    children = client.children(ref_folder["id"])
    if not children:
        if expected_source_sha:
            raise SnapshotDeleteError("Google Drive snapshot ref folder is empty; expected manifest source identity cannot be verified")
        client.trash(ref_folder["id"])
        return "trashed-empty"
    if any(child.get("mimeType") == FOLDER_MIME for child in children):
        raise SnapshotDeleteError("Google Drive snapshot ref folder contains an unexpected child folder")

    manifests = [child for child in children if child.get("name") == "manifest.json"]
    archives = [child for child in children if child.get("name") != "manifest.json"]
    if len(manifests) != 1 or len(archives) > 1:
        raise SnapshotDeleteError("Google Drive snapshot ref folder must contain exactly manifest.json and at most one archive")

    manifest = manifests[0]
    archive = archives[0] if archives else None
    _validate_manifest(
        client.media(manifest["id"]),
        repository=repository,
        ref=ref,
        expected_source_sha=expected_source_sha,
        ref_folder_id=ref_folder["id"],
        manifest_id=manifest["id"],
        archive=archive,
    )
    client.trash(ref_folder["id"])
    return "trashed"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", required=True)
    parser.add_argument("--ref", required=True)
    parser.add_argument("--expected-source-sha", default="")
    parser.add_argument("--api-root", default="https://www.googleapis.com/drive/v3")
    args = parser.parse_args()

    access_token = os.environ.get("GOOGLE_DRIVE_ACCESS_TOKEN", "")
    root_folder_id = os.environ.get("GOOGLE_DRIVE_ROOT_FOLDER_ID", "")
    if not access_token:
        raise SystemExit("GOOGLE_DRIVE_ACCESS_TOKEN is required")
    try:
        result = delete_snapshot(
            DriveClient(access_token=access_token, api_root=args.api_root),
            root_folder_id=root_folder_id,
            repository=args.repository,
            ref=args.ref,
            expected_source_sha=args.expected_source_sha,
        )
    except SnapshotDeleteError as exc:
        raise SystemExit(str(exc)) from None
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
