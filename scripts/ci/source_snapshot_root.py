#!/usr/bin/env python3
"""Resolve the canonical Google Drive repositories source-snapshot root."""

from __future__ import annotations

import argparse
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

FOLDER_MIME = "application/vnd.google-apps.folder"
REQUEST_TIMEOUT_SECONDS = 20


class SnapshotRootError(RuntimeError):
    pass


def _query_literal(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


def select_canonical_repositories_folder(values: list[dict[str, Any]]) -> dict[str, Any]:
    candidates: list[dict[str, Any]] = []
    for value in values:
        if (
            isinstance(value, dict)
            and value.get("name") == "repositories"
            and value.get("mimeType") == FOLDER_MIME
            and value.get("trashed") is not True
        ):
            file_id = value.get("id")
            created_time = value.get("createdTime")
            if not isinstance(file_id, str) or not file_id or not isinstance(created_time, str) or not created_time:
                raise SnapshotRootError("canonical repositories folder candidate has incomplete provider identity")
            candidates.append(value)
    if not candidates:
        raise SnapshotRootError("Google Drive canonical repositories folder was not found")
    return min(candidates, key=lambda value: (value["createdTime"], value["id"]))


@dataclass
class DriveClient:
    access_token: str
    api_root: str = "https://www.googleapis.com/drive/v3"
    max_pages: int = 10

    def _json(self, path: str) -> Any:
        request = urllib.request.Request(
            self.api_root.rstrip("/") + path,
            headers={
                "Authorization": f"Bearer {self.access_token}",
                "Accept": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
                body = response.read()
        except urllib.error.HTTPError as exc:
            raise SnapshotRootError(f"Google Drive repositories-root lookup was refused with HTTP {exc.code}") from None
        except urllib.error.URLError:
            raise SnapshotRootError("Google Drive repositories-root lookup failed") from None
        try:
            return json.loads(body or b"{}")
        except json.JSONDecodeError:
            raise SnapshotRootError("Google Drive repositories-root lookup returned invalid JSON") from None

    def exact_repositories_folders(self) -> list[dict[str, Any]]:
        page_token = ""
        values: list[dict[str, Any]] = []
        for _ in range(self.max_pages):
            params = {
                "q": " and ".join(
                    [
                        f"name = '{_query_literal('repositories')}'",
                        "trashed = false",
                        f"mimeType = '{FOLDER_MIME}'",
                    ]
                ),
                "spaces": "drive",
                "pageSize": "100",
                "fields": "nextPageToken,files(id,name,mimeType,createdTime,parents,trashed)",
            }
            if page_token:
                params["pageToken"] = page_token
            payload = self._json("/files?" + urllib.parse.urlencode(params))
            files = payload.get("files") if isinstance(payload, dict) else None
            if not isinstance(files, list):
                raise SnapshotRootError("Google Drive repositories-root lookup returned invalid file metadata")
            values.extend(files)
            page_token = payload.get("nextPageToken", "") if isinstance(payload, dict) else ""
            if not page_token:
                return values
            if not isinstance(page_token, str):
                raise SnapshotRootError("Google Drive repositories-root lookup returned invalid pagination metadata")
        raise SnapshotRootError("Google Drive repositories-root lookup exceeded bounded pagination limit")


def resolve_canonical_root(client: DriveClient) -> str:
    return select_canonical_repositories_folder(client.exact_repositories_folders())["id"]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-root", default="https://www.googleapis.com/drive/v3")
    args = parser.parse_args()

    access_token = os.environ.get("GOOGLE_DRIVE_ACCESS_TOKEN", "")
    if not access_token:
        raise SystemExit("GOOGLE_DRIVE_ACCESS_TOKEN is required")
    try:
        root_id = resolve_canonical_root(DriveClient(access_token=access_token, api_root=args.api_root))
    except SnapshotRootError as exc:
        raise SystemExit(str(exc)) from None
    print(root_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
