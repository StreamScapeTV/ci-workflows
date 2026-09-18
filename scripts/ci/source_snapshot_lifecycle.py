#!/usr/bin/env python3
"""Fail-closed lifecycle for mutable canonical Google Drive source archives."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

FOLDER_MIME = "application/vnd.google-apps.folder"
REPOSITORY = re.compile(r"StreamScapeTV/[A-Za-z0-9_.-]{1,100}\Z")
DRIVE_FILE_ID = re.compile(r"[A-Za-z0-9_-]{10,200}\Z")
SHA40 = re.compile(r"[0-9a-f]{40}\Z")
SHA64 = re.compile(r"[0-9a-f]{64}\Z")


class SourceSnapshotLifecycleError(RuntimeError):
    pass


def _query_literal(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


def _validate_common(repository: str, ref: str, archive_filename: str, is_tag: bool) -> None:
    if not REPOSITORY.fullmatch(repository):
        raise SourceSnapshotLifecycleError("source snapshot lifecycle repository is invalid")
    if not ref or ref.startswith(("refs/heads/", "refs/tags/")) or len(ref.encode()) > 255 or "\x00" in ref:
        raise SourceSnapshotLifecycleError("source snapshot lifecycle ref is invalid")
    repository_name = repository.rsplit("/", 1)[1]
    expected = f"{repository_name}-{urllib.parse.quote(ref, safe='')}.zip"
    if archive_filename != expected:
        raise SourceSnapshotLifecycleError("source snapshot lifecycle archive filename is not canonical for repository/ref")
    if not isinstance(is_tag, bool):
        raise SourceSnapshotLifecycleError("source snapshot lifecycle tag identity is invalid")


@dataclass
class DriveClient:
    access_token: str
    api_root: str = "https://www.googleapis.com/drive/v3"
    max_pages: int = 10

    def _request(self, path: str, *, method: str = "GET") -> bytes:
        request = urllib.request.Request(
            self.api_root.rstrip("/") + path,
            method=method,
            headers={"Authorization": f"Bearer {self.access_token}", "Accept": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            if method == "DELETE" and exc.code == 404:
                return b""
            raise SourceSnapshotLifecycleError(
                f"Google Drive source snapshot lifecycle request was refused with HTTP {exc.code}"
            ) from None
        except urllib.error.URLError:
            raise SourceSnapshotLifecycleError("Google Drive source snapshot lifecycle request failed") from None

    def _json(self, path: str) -> Any:
        try:
            return json.loads(self._request(path) or b"{}")
        except json.JSONDecodeError:
            raise SourceSnapshotLifecycleError("Google Drive source snapshot lifecycle returned invalid JSON") from None

    def list_query(self, clauses: list[str]) -> list[dict[str, Any]]:
        token = ""
        result: list[dict[str, Any]] = []
        for _ in range(self.max_pages):
            params = {
                "q": " and ".join(clauses),
                "spaces": "drive",
                "pageSize": "100",
                "fields": "nextPageToken,files(id,name,mimeType,parents,size,trashed)",
            }
            if token:
                params["pageToken"] = token
            payload = self._json("/files?" + urllib.parse.urlencode(params))
            files = payload.get("files") if isinstance(payload, dict) else None
            if not isinstance(files, list):
                raise SourceSnapshotLifecycleError("Google Drive source snapshot lookup returned invalid file metadata")
            for value in files:
                if not isinstance(value, dict) or not value.get("id") or not value.get("name") or not value.get("mimeType"):
                    raise SourceSnapshotLifecycleError("Google Drive source snapshot lookup returned incomplete file metadata")
                result.append(value)
            token = payload.get("nextPageToken", "") if isinstance(payload, dict) else ""
            if not token:
                return result
            if not isinstance(token, str):
                raise SourceSnapshotLifecycleError("Google Drive source snapshot lookup returned invalid pagination metadata")
        raise SourceSnapshotLifecycleError("Google Drive source snapshot lookup exceeded bounded pagination")

    def exact_folders(self, parent: str, name: str) -> list[dict[str, Any]]:
        return self.list_query([
            f"'{_query_literal(parent)}' in parents",
            f"name = '{_query_literal(name)}'",
            "trashed = false",
            f"mimeType = '{FOLDER_MIME}'",
        ])

    def exact_files(self, parent: str, name: str) -> list[dict[str, Any]]:
        return self.list_query([
            f"'{_query_literal(parent)}' in parents",
            f"name = '{_query_literal(name)}'",
            "trashed = false",
            f"mimeType != '{FOLDER_MIME}'",
        ])

    def media(self, file_id: str) -> bytes:
        if not DRIVE_FILE_ID.fullmatch(file_id or ""):
            raise SourceSnapshotLifecycleError("Google Drive source snapshot file ID is invalid")
        return self._request(f"/files/{urllib.parse.quote(file_id, safe='')}?alt=media")

    def delete(self, file_id: str) -> None:
        if not DRIVE_FILE_ID.fullmatch(file_id or ""):
            raise SourceSnapshotLifecycleError("Google Drive source snapshot delete file ID is invalid")
        self._request(f"/files/{urllib.parse.quote(file_id, safe='')}", method="DELETE")


def _unique_folder(values: list[dict[str, Any]], label: str, *, allow_missing: bool) -> dict[str, Any] | None:
    if not values:
        if allow_missing:
            return None
        raise SourceSnapshotLifecycleError(f"source snapshot lifecycle could not find canonical {label} folder")
    if len(values) != 1:
        raise SourceSnapshotLifecycleError(f"source snapshot lifecycle found ambiguous {label} folders")
    return values[0]


def _resolve_ref_folder(
    client: DriveClient,
    *,
    root_folder_id: str,
    repository: str,
    ref: str,
    repository_folder_id: str = "",
    allow_missing_ref: bool,
) -> dict[str, Any] | None:
    if not DRIVE_FILE_ID.fullmatch(root_folder_id or ""):
        raise SourceSnapshotLifecycleError("Google Drive repositories root folder ID is invalid")
    repository_name = repository.rsplit("/", 1)[1]
    folders = client.exact_folders(root_folder_id, repository_name)
    repository_folder = _unique_folder(folders, "repository", allow_missing=not repository_folder_id)
    if repository_folder_id:
        if repository_folder is None or repository_folder.get("id") != repository_folder_id:
            raise SourceSnapshotLifecycleError("configured source snapshot repository folder identity mismatch")
    if repository_folder is None:
        if allow_missing_ref:
            return None
        raise SourceSnapshotLifecycleError("source snapshot repository folder is missing")
    return _unique_folder(client.exact_folders(repository_folder["id"], ref), "ref", allow_missing=allow_missing_ref)


def _parse_manifest(raw: bytes) -> dict[str, Any]:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        raise SourceSnapshotLifecycleError("canonical source snapshot manifest is invalid JSON") from None
    if not isinstance(value, dict):
        raise SourceSnapshotLifecycleError("canonical source snapshot manifest must be one JSON object")
    return value


def preflight(
    client: DriveClient,
    *,
    root_folder_id: str,
    repository: str,
    ref: str,
    is_tag: bool,
    archive_filename: str,
    repository_folder_id: str,
    state_path: Path,
) -> dict[str, Any]:
    _validate_common(repository, ref, archive_filename, is_tag)
    ref_folder = _resolve_ref_folder(
        client,
        root_folder_id=root_folder_id,
        repository=repository,
        ref=ref,
        repository_folder_id=repository_folder_id,
        allow_missing_ref=True,
    )
    state: dict[str, Any] = {
        "repository": repository,
        "ref": ref,
        "is_tag": is_tag,
        "archive_filename": archive_filename,
        "ref_folder_id": ref_folder.get("id", "") if ref_folder else "",
        "manifest_file_id": "",
        "previous_archive_file_id": "",
        "prior_archive_file_ids": [],
    }
    if ref_folder is None:
        state_path.write_text(json.dumps(state, sort_keys=True, separators=(",", ":")) + "\n")
        return state

    folder_id = ref_folder["id"]
    manifests = client.exact_files(folder_id, "manifest.json")
    current = client.exact_files(folder_id, archive_filename)
    legacy = client.exact_files(folder_id, "source.zip")
    candidates = current + legacy
    state["prior_archive_file_ids"] = sorted({value["id"] for value in candidates})
    if not manifests:
        if candidates:
            raise SourceSnapshotLifecycleError("canonical source archive exists without one manifest identity")
        state_path.write_text(json.dumps(state, sort_keys=True, separators=(",", ":")) + "\n")
        return state
    if len(manifests) != 1:
        raise SourceSnapshotLifecycleError("source snapshot replacement requires exactly one canonical manifest")

    manifest_file = manifests[0]
    manifest = _parse_manifest(client.media(manifest_file["id"]))
    for key, expected in (
        ("repository", repository),
        ("requested_ref", ref),
        ("is_tag", is_tag),
        ("folder_id", folder_id),
    ):
        if manifest.get(key) != expected:
            raise SourceSnapshotLifecycleError(f"canonical source snapshot manifest identity mismatch: {key}")
    archive_id = manifest.get("source_zip_file_id") or manifest.get("archive_file_id")
    archive_name = manifest.get("archive_filename")
    if not isinstance(archive_id, str) or not DRIVE_FILE_ID.fullmatch(archive_id):
        raise SourceSnapshotLifecycleError("canonical source snapshot manifest has invalid archive file identity")
    if archive_name not in {archive_filename, "source.zip"}:
        raise SourceSnapshotLifecycleError("canonical source snapshot manifest archive filename is outside current/legacy names")
    matching = [value for value in candidates if value.get("id") == archive_id and value.get("name") == archive_name]
    if len(matching) != 1:
        raise SourceSnapshotLifecycleError("canonical source snapshot manifest archive object is not present in the exact ref folder")
    declared_size = manifest.get("archive_size_bytes", manifest.get("source_zip_size_bytes"))
    if isinstance(declared_size, int) and matching[0].get("size") not in (None, str(declared_size)):
        raise SourceSnapshotLifecycleError("canonical source snapshot archive size metadata does not match manifest")
    state["manifest_file_id"] = manifest_file["id"]
    state["previous_archive_file_id"] = archive_id
    state_path.write_text(json.dumps(state, sort_keys=True, separators=(",", ":")) + "\n")
    return state


def prune(
    client: DriveClient,
    *,
    root_folder_id: str,
    repository: str,
    ref: str,
    is_tag: bool,
    archive_filename: str,
    repository_folder_id: str,
    state_path: Path,
    manifest_path: Path,
) -> int:
    _validate_common(repository, ref, archive_filename, is_tag)
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise SourceSnapshotLifecycleError("source snapshot replacement state is unreadable") from None
    if not isinstance(state, dict):
        raise SourceSnapshotLifecycleError("source snapshot replacement state is invalid")
    for key, expected in (("repository", repository), ("ref", ref), ("is_tag", is_tag), ("archive_filename", archive_filename)):
        if state.get(key) != expected:
            raise SourceSnapshotLifecycleError(f"source snapshot replacement state identity mismatch: {key}")
    prior_ids = state.get("prior_archive_file_ids")
    if not isinstance(prior_ids, list) or any(not isinstance(value, str) or not DRIVE_FILE_ID.fullmatch(value) for value in prior_ids):
        raise SourceSnapshotLifecycleError("source snapshot replacement state has invalid prior archive identities")

    manifest = _parse_manifest(manifest_path.read_bytes())
    ref_folder = _resolve_ref_folder(
        client,
        root_folder_id=root_folder_id,
        repository=repository,
        ref=ref,
        repository_folder_id=repository_folder_id,
        allow_missing_ref=False,
    )
    assert ref_folder is not None
    folder_id = ref_folder["id"]
    for key, expected in (
        ("repository", repository),
        ("requested_ref", ref),
        ("is_tag", is_tag),
        ("folder_id", folder_id),
        ("archive_filename", archive_filename),
    ):
        if manifest.get(key) != expected:
            raise SourceSnapshotLifecycleError(f"current source snapshot manifest identity mismatch: {key}")
    current_id = manifest.get("source_zip_file_id") or manifest.get("archive_file_id")
    if not isinstance(current_id, str) or not DRIVE_FILE_ID.fullmatch(current_id):
        raise SourceSnapshotLifecycleError("current source snapshot manifest has invalid archive identity")
    manifests = client.exact_files(folder_id, "manifest.json")
    if len(manifests) != 1:
        raise SourceSnapshotLifecycleError("source snapshot pruning requires exactly one canonical manifest")
    manifest_id = manifest.get("manifest_file_id")
    if manifest_id != manifests[0]["id"]:
        raise SourceSnapshotLifecycleError("current source snapshot manifest Drive identity mismatch")
    current = client.exact_files(folder_id, archive_filename)
    legacy = client.exact_files(folder_id, "source.zip")
    all_candidates = current + legacy
    if not any(value.get("id") == current_id and value.get("name") == archive_filename for value in all_candidates):
        raise SourceSnapshotLifecycleError("current source snapshot archive is not present under canonical filename")

    prior_set = set(prior_ids)
    # Delete only objects observed before this replacement began. A same-name object
    # created concurrently after preflight is never deleted by this invocation.
    stale_ids = sorted({value["id"] for value in all_candidates if value["id"] in prior_set and value["id"] != current_id})
    for file_id in stale_ids:
        client.delete(file_id)

    final_current = client.exact_files(folder_id, archive_filename)
    final_legacy = client.exact_files(folder_id, "source.zip")
    if len(final_current) != 1 or final_current[0].get("id") != current_id or final_legacy:
        raise SourceSnapshotLifecycleError(
            "source snapshot replacement did not converge to exactly one canonical archive; concurrent or stale object remains"
        )
    return len(stale_ids)


def _bool(value: str) -> bool:
    if value == "true":
        return True
    if value == "false":
        return False
    raise argparse.ArgumentTypeError("must be true or false")


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("preflight", "prune"):
        cmd = sub.add_parser(name)
        cmd.add_argument("--repository", required=True)
        cmd.add_argument("--ref", required=True)
        cmd.add_argument("--is-tag", required=True, type=_bool)
        cmd.add_argument("--archive-filename", required=True)
        cmd.add_argument("--repository-folder-id", default="")
        cmd.add_argument("--state", required=True)
        cmd.add_argument("--api-root", default="https://www.googleapis.com/drive/v3")
        if name == "prune":
            cmd.add_argument("--manifest", required=True)
    args = parser.parse_args()
    token = os.environ.get("GOOGLE_DRIVE_ACCESS_TOKEN", "")
    root = os.environ.get("GOOGLE_DRIVE_ROOT_FOLDER_ID", "")
    if not token:
        raise SystemExit("GOOGLE_DRIVE_ACCESS_TOKEN is required")
    client = DriveClient(token, args.api_root)
    try:
        if args.command == "preflight":
            value = preflight(
                client,
                root_folder_id=root,
                repository=args.repository,
                ref=args.ref,
                is_tag=args.is_tag,
                archive_filename=args.archive_filename,
                repository_folder_id=args.repository_folder_id,
                state_path=Path(args.state),
            )
            print(json.dumps(value, sort_keys=True, separators=(",", ":")))
            return 0
        deleted = prune(
            client,
            root_folder_id=root,
            repository=args.repository,
            ref=args.ref,
            is_tag=args.is_tag,
            archive_filename=args.archive_filename,
            repository_folder_id=args.repository_folder_id,
            state_path=Path(args.state),
            manifest_path=Path(args.manifest),
        )
        print(json.dumps({"deleted_prior_archives": deleted}, sort_keys=True, separators=(",", ":")))
        return 0
    except SourceSnapshotLifecycleError as exc:
        raise SystemExit(str(exc)) from None


if __name__ == "__main__":
    raise SystemExit(main())
