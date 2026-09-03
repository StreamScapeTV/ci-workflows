#!/usr/bin/env python3
"""Find stale/missing canonical main/develop source snapshots.

This planner is intentionally read-only. Actual Drive mutation is performed only by
`actions/source-snapshot` after the workflow self-dispatches one exact target.
"""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

GITHUB_API = "https://api.github.com"
DRIVE_API = "https://www.googleapis.com/drive/v3"
INTEGRATION_REFS = ("main", "develop")
SHA_RE = re.compile(r"[0-9a-f]{40}\Z")
SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")


class ReconcileError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class Target:
    repository: str
    repository_name: str
    ref: str
    source_sha: str
    tree_sha: str
    reason: str

    def as_json(self) -> dict[str, str]:
        return {
            "repository": self.repository,
            "repository_name": self.repository_name,
            "ref": self.ref,
            "source_sha": self.source_sha,
            "tree_sha": self.tree_sha,
            "reason": self.reason,
        }


def _request_json(
    url: str,
    *,
    token: str,
    accept: str = "application/json",
    allow_404: bool = False,
    extra_headers: dict[str, str] | None = None,
) -> Any | None:
    request = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": accept,
            **(extra_headers or {}),
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read()
    except urllib.error.HTTPError as error:
        if allow_404 and error.code == 404:
            return None
        raise ReconcileError(f"HTTP {error.code} reading {url}") from None
    except (OSError, urllib.error.URLError) as error:
        raise ReconcileError(f"failed reading {url}: {error}") from None
    try:
        return json.loads(raw.decode())
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ReconcileError(f"invalid JSON from {url}") from error


def github_json(path: str, token: str, *, allow_404: bool = False) -> Any | None:
    return _request_json(
        f"{GITHUB_API}{path}",
        token=token,
        accept="application/vnd.github+json",
        allow_404=allow_404,
        extra_headers={"X-GitHub-Api-Version": "2022-11-28"},
    )


def drive_json(path: str, token: str, *, allow_404: bool = False) -> Any | None:
    return _request_json(
        f"{DRIVE_API}{path}",
        token=token,
        allow_404=allow_404,
    )


def _drive_query(parent: str, name: str, *, folder: bool) -> str:
    literal_parent = parent.replace("\\", "\\\\").replace("'", "\\'")
    literal_name = name.replace("\\", "\\\\").replace("'", "\\'")
    clauses = [
        f"'{literal_parent}' in parents",
        f"name = '{literal_name}'",
        "trashed = false",
        (
            "mimeType = 'application/vnd.google-apps.folder'"
            if folder
            else "mimeType != 'application/vnd.google-apps.folder'"
        ),
    ]
    return urllib.parse.urlencode(
        {
            "q": " and ".join(clauses),
            "spaces": "drive",
            "fields": "files(id,name,mimeType,size,parents,trashed)",
            "pageSize": "100",
        }
    )


def exact_drive_child(parent: str, name: str, *, folder: bool, token: str) -> dict[str, Any] | None:
    payload = drive_json(f"/files?{_drive_query(parent, name, folder=folder)}", token)
    if not isinstance(payload, dict) or not isinstance(payload.get("files"), list):
        raise ReconcileError("Google Drive exact-child lookup returned invalid metadata")
    files = payload["files"]
    if len(files) > 1:
        kind = "folders" if folder else "files"
        raise ReconcileError(f"duplicate Google Drive {kind} for exact parent/name {parent}/{name}")
    if not files:
        return None
    value = files[0]
    if not isinstance(value, dict) or not isinstance(value.get("id"), str):
        raise ReconcileError("Google Drive exact-child lookup returned incomplete metadata")
    return value


def installed_repositories(token: str) -> list[dict[str, Any]]:
    repositories: list[dict[str, Any]] = []
    page = 1
    while True:
        payload = github_json(f"/installation/repositories?per_page=100&page={page}", token)
        if not isinstance(payload, dict) or not isinstance(payload.get("repositories"), list):
            raise ReconcileError("GitHub installation repository listing returned invalid metadata")
        batch = payload["repositories"]
        for value in batch:
            if not isinstance(value, dict):
                raise ReconcileError("GitHub installation repository listing returned invalid repository metadata")
            full_name = value.get("full_name")
            name = value.get("name")
            if (
                isinstance(full_name, str)
                and full_name.startswith("StreamScapeTV/")
                and isinstance(name, str)
                and full_name == f"StreamScapeTV/{name}"
            ):
                repositories.append(value)
        if len(batch) < 100:
            break
        page += 1
        if page > 100:
            raise ReconcileError("GitHub installation repository listing exceeded bounded pagination")
    repositories.sort(key=lambda value: str(value["full_name"]))
    return repositories


def branch_identity(repository: str, ref: str, token: str) -> tuple[str, str] | None:
    encoded = urllib.parse.quote(ref, safe="")
    ref_payload = github_json(f"/repos/{repository}/git/ref/heads/{encoded}", token, allow_404=True)
    if ref_payload is None:
        return None
    try:
        source_sha = ref_payload["object"]["sha"]
    except (KeyError, TypeError):
        raise ReconcileError(f"GitHub branch metadata is incomplete for {repository}@{ref}") from None
    if not isinstance(source_sha, str) or SHA_RE.fullmatch(source_sha) is None:
        raise ReconcileError(f"GitHub branch SHA is invalid for {repository}@{ref}")
    commit = github_json(f"/repos/{repository}/git/commits/{source_sha}", token)
    try:
        tree_sha = commit["tree"]["sha"]
    except (KeyError, TypeError):
        raise ReconcileError(f"GitHub commit tree metadata is incomplete for {repository}@{ref}") from None
    if not isinstance(tree_sha, str) or SHA_RE.fullmatch(tree_sha) is None:
        raise ReconcileError(f"GitHub tree SHA is invalid for {repository}@{ref}")
    return source_sha, tree_sha


def manifest_is_current(
    manifest: Any,
    *,
    repository: str,
    repository_name: str,
    ref: str,
    source_sha: str,
    tree_sha: str,
    ref_folder_id: str,
    manifest_file_id: str,
    archive_metadata: dict[str, Any] | None,
) -> bool:
    if not isinstance(manifest, dict):
        return False
    expected_archive_name = f"{repository_name}-{urllib.parse.quote(ref, safe='')}.zip"
    digest = manifest.get("archive_sha256")
    archive_size = manifest.get("archive_size_bytes")
    archive_file_id = manifest.get("archive_file_id")
    if not isinstance(digest, str) or SHA256_RE.fullmatch(digest) is None:
        return False
    if isinstance(archive_size, bool) or not isinstance(archive_size, int) or archive_size < 0:
        return False
    if not isinstance(archive_file_id, str) or not archive_file_id:
        return False
    expected = {
        "repository": repository,
        "repository_name": repository_name,
        "requested_ref": ref,
        "is_tag": False,
        "resolved_source_sha": source_sha,
        "tree_sha": tree_sha,
        "archive_format": "zip",
        "archive_format_version": 1,
        "archive_filename": expected_archive_name,
        "source_zip_sha256": digest,
        "source_zip_size_bytes": archive_size,
        "archive_file_id": archive_file_id,
        "source_zip_file_id": archive_file_id,
        "manifest_file_id": manifest_file_id,
        "folder_id": ref_folder_id,
    }
    for key, value in expected.items():
        if manifest.get(key) != value:
            return False
    if archive_metadata is None:
        return False
    if archive_metadata.get("id") != archive_file_id:
        return False
    if archive_metadata.get("name") != expected_archive_name:
        return False
    if archive_metadata.get("trashed") is True:
        return False
    parents = archive_metadata.get("parents")
    if not isinstance(parents, list) or ref_folder_id not in parents:
        return False
    try:
        remote_size = int(archive_metadata.get("size"))
    except (TypeError, ValueError):
        return False
    return remote_size == archive_size


def target_for_ref(
    *,
    repository: str,
    repository_name: str,
    ref: str,
    source_sha: str,
    tree_sha: str,
    drive_root: str,
    drive_token: str,
) -> Target | None:
    repo_folder = exact_drive_child(drive_root, repository_name, folder=True, token=drive_token)
    if repo_folder is None:
        return Target(repository, repository_name, ref, source_sha, tree_sha, "missing-repository-folder")
    ref_folder = exact_drive_child(repo_folder["id"], ref, folder=True, token=drive_token)
    if ref_folder is None:
        return Target(repository, repository_name, ref, source_sha, tree_sha, "missing-ref-folder")
    manifest_file = exact_drive_child(ref_folder["id"], "manifest.json", folder=False, token=drive_token)
    if manifest_file is None:
        return Target(repository, repository_name, ref, source_sha, tree_sha, "missing-manifest")
    manifest = drive_json(f"/files/{manifest_file['id']}?alt=media", drive_token, allow_404=True)
    if manifest is None:
        return Target(repository, repository_name, ref, source_sha, tree_sha, "missing-manifest-bytes")
    archive_file_id = manifest.get("archive_file_id") if isinstance(manifest, dict) else None
    archive_metadata: dict[str, Any] | None = None
    if isinstance(archive_file_id, str) and archive_file_id:
        value = drive_json(
            f"/files/{archive_file_id}?fields=id,name,size,parents,trashed",
            drive_token,
            allow_404=True,
        )
        archive_metadata = value if isinstance(value, dict) else None
    if manifest_is_current(
        manifest,
        repository=repository,
        repository_name=repository_name,
        ref=ref,
        source_sha=source_sha,
        tree_sha=tree_sha,
        ref_folder_id=ref_folder["id"],
        manifest_file_id=manifest_file["id"],
        archive_metadata=archive_metadata,
    ):
        return None
    return Target(repository, repository_name, ref, source_sha, tree_sha, "stale-or-invalid-manifest")


def plan(*, github_token: str, drive_token: str, drive_root: str) -> list[Target]:
    targets: list[Target] = []
    for repository_value in installed_repositories(github_token):
        repository = str(repository_value["full_name"])
        repository_name = str(repository_value["name"])
        for ref in INTEGRATION_REFS:
            identity = branch_identity(repository, ref, github_token)
            if identity is None:
                continue
            source_sha, tree_sha = identity
            target = target_for_ref(
                repository=repository,
                repository_name=repository_name,
                ref=ref,
                source_sha=source_sha,
                tree_sha=tree_sha,
                drive_root=drive_root,
                drive_token=drive_token,
            )
            if target is not None:
                targets.append(target)
    targets.sort(key=lambda value: (value.repository, value.ref))
    return targets


def main() -> int:
    github_token = os.environ.get("SOURCE_GITHUB_TOKEN", "")
    drive_token = os.environ.get("GOOGLE_DRIVE_ACCESS_TOKEN", "")
    drive_root = os.environ.get("GOOGLE_DRIVE_ROOT_FOLDER_ID", "")
    if not github_token or not drive_token or not drive_root:
        print("source snapshot reconciliation requires GitHub token, Drive token, and Drive root", file=sys.stderr)
        return 2
    try:
        targets = plan(github_token=github_token, drive_token=drive_token, drive_root=drive_root)
    except ReconcileError as error:
        print(str(error), file=sys.stderr)
        return 1
    print(json.dumps([target.as_json() for target in targets], sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
