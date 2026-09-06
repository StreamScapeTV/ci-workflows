#!/usr/bin/env python3
"""Publish one canonical Google Drive source checkpoint to one GitHub branch."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass
from typing import Any

FOLDER_MIME = "application/vnd.google-apps.folder"
REPOSITORY = re.compile(r"StreamScapeTV/[A-Za-z0-9_.-]{1,100}\Z")
SHA40 = re.compile(r"[0-9a-f]{40}\Z")
SHA64 = re.compile(r"[0-9a-f]{64}\Z")
DRIVE_FILE_ID = re.compile(r"[A-Za-z0-9_-]{10,200}\Z")
MAX_BUNDLE_BYTES = 512 * 1024 * 1024
MAX_MEMBER_BYTES = 100 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 512 * 1024 * 1024
MAX_MEMBERS = 20_000
MAX_PATH_BYTES = 1024
MAX_COMMIT_MESSAGE_BYTES = 16 * 1024
MAX_COMPARE_COMMITS = 250
ALLOWED_COMPRESSION = {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}


class CheckpointPublishError(RuntimeError):
    pass


def validate_request(repository: str, branch: str, expected_head: str) -> None:
    if not REPOSITORY.fullmatch(repository):
        raise CheckpointPublishError("checkpoint repository must be one bounded StreamScapeTV owner/name")
    if branch in {"main", "develop"}:
        raise CheckpointPublishError("checkpoint publication refuses protected integration branch names")
    if (
        not branch
        or branch.startswith(("refs/heads/", "refs/tags/"))
        or any(character in branch for character in ("\x00", "\r", "\n"))
        or len(branch.encode("utf-8")) > 255
    ):
        raise CheckpointPublishError("checkpoint branch name is invalid")
    if not SHA40.fullmatch(expected_head):
        raise CheckpointPublishError("checkpoint expected head must be one lowercase 40-character Git SHA")


def _query_literal(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


@dataclass
class DriveClient:
    access_token: str
    api_root: str = "https://www.googleapis.com/drive/v3"
    max_pages: int = 10

    def _request(self, path: str) -> bytes:
        request = urllib.request.Request(
            self.api_root.rstrip("/") + path,
            headers={"Authorization": f"Bearer {self.access_token}", "Accept": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            raise CheckpointPublishError(f"Google Drive checkpoint request was refused with HTTP {exc.code}") from None
        except urllib.error.URLError:
            raise CheckpointPublishError("Google Drive checkpoint request failed") from None

    def _json(self, path: str) -> Any:
        try:
            return json.loads(self._request(path) or b"{}")
        except json.JSONDecodeError:
            raise CheckpointPublishError("Google Drive checkpoint request returned invalid JSON") from None

    def list_query(self, clauses: list[str]) -> list[dict[str, Any]]:
        page_token = ""
        values: list[dict[str, Any]] = []
        for _ in range(self.max_pages):
            params = {
                "q": " and ".join(clauses),
                "spaces": "drive",
                "pageSize": "100",
                "fields": "nextPageToken,files(id,name,mimeType,parents,size,trashed)",
            }
            if page_token:
                params["pageToken"] = page_token
            payload = self._json("/files?" + urllib.parse.urlencode(params))
            files = payload.get("files") if isinstance(payload, dict) else None
            if not isinstance(files, list):
                raise CheckpointPublishError("Google Drive checkpoint lookup returned invalid file metadata")
            for value in files:
                if not isinstance(value, dict) or not value.get("id") or not value.get("name") or not value.get("mimeType"):
                    raise CheckpointPublishError("Google Drive checkpoint lookup returned incomplete file metadata")
                values.append(value)
            page_token = payload.get("nextPageToken", "") if isinstance(payload, dict) else ""
            if not page_token:
                return values
            if not isinstance(page_token, str):
                raise CheckpointPublishError("Google Drive checkpoint lookup returned invalid pagination metadata")
        raise CheckpointPublishError("Google Drive checkpoint lookup exceeded bounded pagination")

    def exact_folders(self, parent: str, name: str) -> list[dict[str, Any]]:
        return self.list_query([
            f"'{_query_literal(parent)}' in parents",
            f"name = '{_query_literal(name)}'",
            "trashed = false",
            f"mimeType = '{FOLDER_MIME}'",
        ])

    def children(self, parent: str) -> list[dict[str, Any]]:
        return self.list_query([f"'{_query_literal(parent)}' in parents", "trashed = false"])

    def media(self, file_id: str) -> bytes:
        return self._request(f"/files/{urllib.parse.quote(file_id, safe='')}?alt=media")


@dataclass
class GitHubClient:
    token: str
    repository: str
    api_root: str = "https://api.github.com"

    def _json(self, path: str) -> Any:
        request = urllib.request.Request(
            self.api_root.rstrip("/") + path,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self.token}",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.loads(response.read() or b"{}")
        except urllib.error.HTTPError as exc:
            raise CheckpointPublishError(f"GitHub checkpoint request was refused with HTTP {exc.code}") from None
        except urllib.error.URLError:
            raise CheckpointPublishError("GitHub checkpoint request failed") from None
        except json.JSONDecodeError:
            raise CheckpointPublishError("GitHub checkpoint request returned invalid JSON") from None

    @property
    def repo_path(self) -> str:
        owner, name = self.repository.split("/", 1)
        return f"/repos/{urllib.parse.quote(owner, safe='')}/{urllib.parse.quote(name, safe='')}"

    def verify_branch(self, branch: str, expected_head: str) -> None:
        repository = self._json(self.repo_path)
        if not isinstance(repository, dict) or repository.get("full_name") != self.repository:
            raise CheckpointPublishError("GitHub checkpoint repository identity mismatch")
        if repository.get("default_branch") == branch:
            raise CheckpointPublishError("checkpoint publication refuses the repository default branch")
        encoded = urllib.parse.quote(branch, safe="")
        metadata = self._json(f"{self.repo_path}/branches/{encoded}")
        if not isinstance(metadata, dict) or metadata.get("name") != branch:
            raise CheckpointPublishError("GitHub checkpoint branch identity mismatch")
        if metadata.get("protected") is True:
            raise CheckpointPublishError("checkpoint publication refuses protected branches")
        observed = metadata.get("commit", {}).get("sha") if isinstance(metadata.get("commit"), dict) else None
        if observed != expected_head:
            raise CheckpointPublishError("checkpoint expected head is stale")

    def _compare(self, base: str, head: str, *, page: int = 1, per_page: int = 100) -> dict[str, Any]:
        if not SHA40.fullmatch(base) or not SHA40.fullmatch(head):
            raise CheckpointPublishError("GitHub checkpoint comparison requires exact commit SHAs")
        params = urllib.parse.urlencode({"page": page, "per_page": per_page})
        value = self._json(f"{self.repo_path}/compare/{base}...{head}?{params}")
        if not isinstance(value, dict):
            raise CheckpointPublishError("GitHub checkpoint comparison returned invalid metadata")
        return value

    @staticmethod
    def _proves_ancestor(value: dict[str, Any], ancestor: str) -> bool:
        base = value.get("base_commit")
        merge_base = value.get("merge_base_commit")
        return (
            value.get("status") in {"ahead", "identical"}
            and isinstance(base, dict)
            and base.get("sha") == ancestor
            and isinstance(merge_base, dict)
            and merge_base.get("sha") == ancestor
        )

    def checkpoint_tree_is_published_ancestor(self, *, base: str, head: str, tree_sha: str) -> bool:
        """Prove an exact checkpoint tree is published between base and requested head."""
        if not SHA40.fullmatch(tree_sha):
            raise CheckpointPublishError("checkpoint ancestry classification requires an exact tree SHA")
        if base == head:
            return False

        first = self._compare(base, head)
        if not self._proves_ancestor(first, base):
            return False
        total = first.get("total_commits")
        if isinstance(total, bool) or not isinstance(total, int) or total < 0:
            raise CheckpointPublishError("GitHub checkpoint comparison returned invalid commit count")
        if total == 0:
            return False
        if total > MAX_COMPARE_COMMITS:
            raise CheckpointPublishError("GitHub checkpoint ancestry exceeds bounded comparison history")

        pages = (total + 99) // 100
        seen: set[str] = set()
        for page in range(1, pages + 1):
            value = first if page == 1 else self._compare(base, head, page=page)
            if not self._proves_ancestor(value, base):
                raise CheckpointPublishError("GitHub checkpoint ancestry changed during comparison")
            commits = value.get("commits")
            if not isinstance(commits, list):
                raise CheckpointPublishError("GitHub checkpoint comparison returned invalid commit metadata")
            for commit in commits:
                if not isinstance(commit, dict):
                    raise CheckpointPublishError("GitHub checkpoint comparison returned invalid commit metadata")
                commit_sha = commit.get("sha")
                metadata = commit.get("commit")
                tree = metadata.get("tree") if isinstance(metadata, dict) else None
                commit_tree = tree.get("sha") if isinstance(tree, dict) else None
                if not isinstance(commit_sha, str) or not SHA40.fullmatch(commit_sha):
                    raise CheckpointPublishError("GitHub checkpoint comparison returned invalid commit SHA")
                if not isinstance(commit_tree, str) or not SHA40.fullmatch(commit_tree):
                    raise CheckpointPublishError("GitHub checkpoint comparison returned invalid tree SHA")
                seen.add(commit_sha)
                if commit_tree != tree_sha:
                    continue
                if self._proves_ancestor(self._compare(base, commit_sha, per_page=1), base):
                    return True
        if len(seen) < total:
            raise CheckpointPublishError("GitHub checkpoint comparison did not return complete bounded history")
        return False


def _unique(values: list[dict[str, Any]], label: str) -> dict[str, Any]:
    if len(values) != 1:
        raise CheckpointPublishError(f"Google Drive checkpoint requires exactly one canonical {label}")
    return values[0]


def _optional_unique(values: list[dict[str, Any]], label: str) -> dict[str, Any] | None:
    if len(values) > 1:
        raise CheckpointPublishError(f"Google Drive checkpoint has duplicate canonical {label}")
    return values[0] if values else None


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _manifest_json(raw: bytes) -> dict[str, Any]:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        raise CheckpointPublishError("canonical checkpoint manifest is invalid JSON") from None
    if not isinstance(value, dict):
        raise CheckpointPublishError("canonical checkpoint manifest must be one JSON object")
    return value


def _canonical_archive_name(repository: str, branch: str) -> str:
    name = repository.rsplit("/", 1)[1]
    return f"{name}-{urllib.parse.quote(branch, safe='')}.zip"


def _validate_checkpoint_manifest(
    value: dict[str, Any],
    *,
    repository: str,
    branch: str,
    expected_head: str,
    ref_folder: dict[str, Any],
    manifest_file: dict[str, Any],
    archive_file: dict[str, Any],
) -> tuple[str, str]:
    known = {
        "repository", "repository_name", "requested_ref", "is_tag", "resolved_source_sha", "tree_sha",
        "archive_format", "archive_format_version", "archive_filename", "archive_sha256", "archive_size_bytes",
        "source_zip_sha256", "source_zip_size_bytes", "archive_file_id", "source_zip_file_id", "folder_id",
        "manifest_file_id", "checkpoint_format_version", "checkpoint_base_sha", "checkpoint_commit_message",
    }
    unknown = set(value) - known
    if unknown:
        raise CheckpointPublishError("canonical checkpoint manifest contains unsupported fields")
    repository_name = repository.rsplit("/", 1)[1]
    if value.get("repository") != repository or value.get("repository_name") != repository_name:
        raise CheckpointPublishError("canonical checkpoint manifest repository identity mismatch")
    if value.get("requested_ref") != branch or value.get("is_tag") is not False:
        raise CheckpointPublishError("canonical checkpoint manifest ref identity mismatch")
    if value.get("checkpoint_format_version") != 1:
        raise CheckpointPublishError("canonical checkpoint manifest is not a publishable local checkpoint")
    base = value.get("checkpoint_base_sha")
    if base != expected_head or value.get("resolved_source_sha") != expected_head:
        raise CheckpointPublishError("canonical checkpoint base does not match expected GitHub head")
    tree_sha = value.get("tree_sha")
    if not isinstance(tree_sha, str) or SHA40.fullmatch(tree_sha) is None:
        raise CheckpointPublishError("canonical checkpoint manifest has invalid tree SHA")
    if value.get("archive_format") != "zip" or value.get("archive_format_version") != 1:
        raise CheckpointPublishError("canonical checkpoint manifest has unsupported archive format")
    expected_name = _canonical_archive_name(repository, branch)
    if value.get("archive_filename") != expected_name or archive_file.get("name") != expected_name:
        raise CheckpointPublishError("canonical checkpoint archive filename mismatch")
    digest = value.get("archive_sha256")
    size = value.get("archive_size_bytes")
    if not isinstance(digest, str) or SHA64.fullmatch(digest) is None:
        raise CheckpointPublishError("canonical checkpoint manifest has invalid archive SHA-256")
    if not isinstance(size, int) or not 1 <= size <= MAX_BUNDLE_BYTES:
        raise CheckpointPublishError("canonical checkpoint manifest has invalid archive size")
    if value.get("source_zip_sha256") != digest or value.get("source_zip_size_bytes") != size:
        raise CheckpointPublishError("canonical checkpoint source ZIP aliases do not match archive identity")
    if value.get("folder_id") != ref_folder.get("id") or value.get("manifest_file_id") != manifest_file.get("id"):
        raise CheckpointPublishError("canonical checkpoint manifest Drive folder/file identity mismatch")
    if value.get("archive_file_id") != archive_file.get("id") or value.get("source_zip_file_id") != archive_file.get("id"):
        raise CheckpointPublishError("canonical checkpoint manifest archive Drive identity mismatch")
    message = value.get("checkpoint_commit_message")
    if not isinstance(message, str) or not message.strip() or "\x00" in message:
        raise CheckpointPublishError("canonical checkpoint commit message is invalid")
    if len(message.encode("utf-8")) > MAX_COMMIT_MESSAGE_BYTES:
        raise CheckpointPublishError("canonical checkpoint commit message is too large")
    return tree_sha, message


@dataclass(frozen=True)
class Checkpoint:
    manifest: dict[str, Any]
    manifest_bytes: bytes
    archive_bytes: bytes
    tree_sha: str
    commit_message: str


def load_canonical_checkpoint(
    client: DriveClient,
    *,
    root_folder_id: str,
    repository: str,
    branch: str,
    expected_head: str,
) -> Checkpoint:
    validate_request(repository, branch, expected_head)
    if not DRIVE_FILE_ID.fullmatch(root_folder_id or ""):
        raise CheckpointPublishError("Google Drive repositories root folder ID is invalid")
    repository_name = repository.rsplit("/", 1)[1]
    repository_folder = _unique(client.exact_folders(root_folder_id, repository_name), "repository folder")
    ref_folder = _unique(client.exact_folders(repository_folder["id"], branch), "ref folder")
    children = client.children(ref_folder["id"])
    if any(value.get("mimeType") == FOLDER_MIME for value in children):
        raise CheckpointPublishError("canonical checkpoint ref folder contains an unexpected child folder")
    manifests = [value for value in children if value.get("name") == "manifest.json"]
    archives = [value for value in children if value.get("name") != "manifest.json"]
    manifest_file = _unique(manifests, "manifest.json")
    archive_file = _unique(archives, "archive file")
    manifest_bytes = client.media(manifest_file["id"])
    value = _manifest_json(manifest_bytes)
    tree_sha, message = _validate_checkpoint_manifest(
        value,
        repository=repository,
        branch=branch,
        expected_head=expected_head,
        ref_folder=ref_folder,
        manifest_file=manifest_file,
        archive_file=archive_file,
    )
    archive_bytes = client.media(archive_file["id"])
    if len(archive_bytes) != value["archive_size_bytes"] or _sha256(archive_bytes) != value["archive_sha256"]:
        raise CheckpointPublishError("canonical checkpoint archive bytes do not match manifest identity")
    return Checkpoint(value, manifest_bytes, archive_bytes, tree_sha, message)


CHECKPOINT_FIELDS = {
    "checkpoint_format_version", "checkpoint_base_sha", "checkpoint_commit_message",
}


def resume_snapshot_action(
    client: DriveClient,
    *,
    root_folder_id: str,
    repository: str,
    branch: str,
    is_tag: bool,
    source_head: str,
    source_tree: str,
    github_client: GitHubClient | None = None,
) -> str:
    """Return preserve/refresh without ever mutating the canonical Drive checkpoint."""
    if not REPOSITORY.fullmatch(repository):
        raise CheckpointPublishError("snapshot repository must be one bounded StreamScapeTV owner/name")
    if not branch or any(character in branch for character in ("\x00", "\r", "\n")):
        raise CheckpointPublishError("snapshot ref name is invalid")
    if not SHA40.fullmatch(source_head) or not SHA40.fullmatch(source_tree):
        raise CheckpointPublishError("snapshot source identity is invalid")
    if is_tag:
        return "refresh"
    if not DRIVE_FILE_ID.fullmatch(root_folder_id or ""):
        raise CheckpointPublishError("Google Drive repositories root folder ID is invalid")

    repository_name = repository.rsplit("/", 1)[1]
    repository_folder = _optional_unique(client.exact_folders(root_folder_id, repository_name), "repository folder")
    if repository_folder is None:
        return "refresh"
    ref_folder = _optional_unique(client.exact_folders(repository_folder["id"], branch), "ref folder")
    if ref_folder is None:
        return "refresh"
    children = client.children(ref_folder["id"])
    if any(value.get("mimeType") == FOLDER_MIME for value in children):
        raise CheckpointPublishError("canonical checkpoint ref folder contains an unexpected child folder")
    manifests = [value for value in children if value.get("name") == "manifest.json"]
    archives = [value for value in children if value.get("name") != "manifest.json"]
    if not manifests and not archives:
        return "refresh"
    manifest_file = _optional_unique(manifests, "manifest.json")
    archive_file = _optional_unique(archives, "archive file")
    if manifest_file is None or archive_file is None:
        raise CheckpointPublishError("canonical checkpoint ref folder is incomplete")

    manifest_bytes = client.media(manifest_file["id"])
    value = _manifest_json(manifest_bytes)
    present_checkpoint_fields = CHECKPOINT_FIELDS & set(value)
    if not present_checkpoint_fields:
        return "refresh"
    if present_checkpoint_fields != CHECKPOINT_FIELDS:
        raise CheckpointPublishError("canonical checkpoint manifest has incomplete checkpoint metadata")
    base = value.get("checkpoint_base_sha")
    if not isinstance(base, str) or SHA40.fullmatch(base) is None:
        raise CheckpointPublishError("canonical checkpoint manifest has invalid checkpoint base SHA")
    tree_sha, _ = _validate_checkpoint_manifest(
        value,
        repository=repository,
        branch=branch,
        expected_head=base,
        ref_folder=ref_folder,
        manifest_file=manifest_file,
        archive_file=archive_file,
    )
    archive_bytes = client.media(archive_file["id"])
    if len(archive_bytes) != value["archive_size_bytes"] or _sha256(archive_bytes) != value["archive_sha256"]:
        raise CheckpointPublishError("canonical checkpoint archive bytes do not match manifest identity")
    if base == source_head:
        return "preserve"
    if tree_sha == source_tree:
        return "refresh"
    if github_client is not None and github_client.checkpoint_tree_is_published_ancestor(
        base=base, head=source_head, tree_sha=tree_sha
    ):
        return "refresh"
    raise CheckpointPublishError(
        "GitHub branch moved away from an unpublished canonical Drive checkpoint; refusing to overwrite it"
    )


def _safe_member_path(name: str) -> PurePosixPath:
    if (
        not name
        or name.startswith("/")
        or "\\" in name
        or "\x00" in name
        or any(ord(character) < 32 or ord(character) == 127 for character in name)
        or len(name.encode("utf-8")) > MAX_PATH_BYTES
    ):
        raise CheckpointPublishError("canonical checkpoint ZIP path is invalid")
    pure = PurePosixPath(name.rstrip("/"))
    if pure.is_absolute() or any(part in {"", ".", "..", ".git"} for part in pure.parts):
        raise CheckpointPublishError("canonical checkpoint ZIP path is unsafe")
    if pure.as_posix() != name.rstrip("/"):
        raise CheckpointPublishError("canonical checkpoint ZIP path is not canonical")
    return pure


def _member_kind(info: zipfile.ZipInfo) -> tuple[str, int]:
    mode = (info.external_attr >> 16) & 0xFFFF
    kind = stat.S_IFMT(mode)
    if info.is_dir():
        return "dir", mode
    if kind in (0, stat.S_IFREG):
        return "file", mode or (stat.S_IFREG | 0o644)
    if kind == stat.S_IFLNK:
        return "symlink", mode
    raise CheckpointPublishError("canonical checkpoint ZIP contains an unsupported file type")


def _clear_worktree(worktree: Path) -> None:
    if not (worktree / ".git").exists():
        raise CheckpointPublishError("checkpoint publication worktree is not a Git checkout")
    for child in worktree.iterdir():
        if child.name == ".git":
            continue
        if child.is_symlink() or child.is_file():
            child.unlink()
        else:
            shutil.rmtree(child)


def materialize_checkpoint(archive_path: Path, worktree: Path) -> None:
    if not archive_path.is_file() or archive_path.stat().st_size > MAX_BUNDLE_BYTES:
        raise CheckpointPublishError("canonical checkpoint ZIP is missing or oversized")
    try:
        archive = zipfile.ZipFile(archive_path)
    except (OSError, zipfile.BadZipFile):
        raise CheckpointPublishError("canonical checkpoint archive is not a valid ZIP") from None
    with archive:
        infos = archive.infolist()
        if not infos or len(infos) > MAX_MEMBERS:
            raise CheckpointPublishError("canonical checkpoint ZIP member count is outside the bounded limit")
        names = [info.filename for info in infos]
        if len(names) != len(set(names)):
            raise CheckpointPublishError("canonical checkpoint ZIP contains duplicate member names")
        total = 0
        entries: list[tuple[zipfile.ZipInfo, PurePosixPath, str, int]] = []
        non_dirs: set[PurePosixPath] = set()
        for info in infos:
            if info.flag_bits & 0x1:
                raise CheckpointPublishError("canonical checkpoint ZIP must not contain encrypted members")
            if info.compress_type not in ALLOWED_COMPRESSION:
                raise CheckpointPublishError("canonical checkpoint ZIP uses unsupported compression")
            if info.file_size < 0 or info.file_size > MAX_MEMBER_BYTES:
                raise CheckpointPublishError("canonical checkpoint ZIP member is oversized")
            total += info.file_size
            if total > MAX_UNCOMPRESSED_BYTES:
                raise CheckpointPublishError("canonical checkpoint ZIP is oversized after decompression")
            path = _safe_member_path(info.filename)
            kind, mode = _member_kind(info)
            if kind != "dir":
                non_dirs.add(path)
            entries.append((info, path, kind, mode))
        for path in non_dirs:
            parent = path.parent
            while parent != PurePosixPath("."):
                if parent in non_dirs:
                    raise CheckpointPublishError("canonical checkpoint ZIP contains a file/directory collision")
                parent = parent.parent

        _clear_worktree(worktree)
        for info, path, kind, mode in sorted(entries, key=lambda value: (len(value[1].parts), value[1].as_posix())):
            target = worktree.joinpath(*path.parts)
            if kind == "dir":
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            data = archive.read(info)
            if kind == "symlink":
                if b"\x00" in data:
                    raise CheckpointPublishError("canonical checkpoint symlink target is invalid")
                os.symlink(data, os.fsencode(target))
            else:
                target.write_bytes(data)
                os.chmod(target, 0o755 if mode & 0o111 else 0o644)


def run_git(worktree: Path, *args: str, capture: bool = True) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(worktree), *args],
            check=True,
            text=True,
            stdout=subprocess.PIPE if capture else None,
            stderr=subprocess.PIPE if capture else None,
        )
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or "").strip()
        raise CheckpointPublishError(f"Git checkpoint operation failed{': ' + detail if detail else ''}") from None
    return (result.stdout or "").strip()


def stage_and_verify_tree(worktree: Path, *, expected_head: str, expected_tree: str) -> str:
    head = run_git(worktree, "rev-parse", "HEAD")
    if head != expected_head:
        raise CheckpointPublishError("checkpoint worktree head does not match expected GitHub head")
    run_git(worktree, "add", "-A", "--", ".")
    tree = run_git(worktree, "write-tree")
    if tree != expected_tree:
        raise CheckpointPublishError("materialized checkpoint tree does not match manifest tree SHA")
    base_tree = run_git(worktree, "rev-parse", "HEAD^{tree}")
    if tree == base_tree:
        raise CheckpointPublishError("canonical checkpoint does not change the expected GitHub tree")
    return tree


def write_checkpoint_files(checkpoint: Checkpoint, *, archive_path: Path, manifest_path: Path, message_path: Path) -> None:
    archive_path.write_bytes(checkpoint.archive_bytes)
    manifest_path.write_bytes(checkpoint.manifest_bytes)
    message_path.write_text(checkpoint.commit_message, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)

    validate = sub.add_parser("validate")
    validate.add_argument("--repository", required=True)
    validate.add_argument("--branch", required=True)
    validate.add_argument("--expected-head", required=True)

    remote = sub.add_parser("verify-remote")
    remote.add_argument("--repository", required=True)
    remote.add_argument("--branch", required=True)
    remote.add_argument("--expected-head", required=True)
    remote.add_argument("--api-root", default="https://api.github.com")

    download = sub.add_parser("download")
    download.add_argument("--repository", required=True)
    download.add_argument("--branch", required=True)
    download.add_argument("--expected-head", required=True)
    download.add_argument("--archive", required=True)
    download.add_argument("--manifest", required=True)
    download.add_argument("--message", required=True)
    download.add_argument("--api-root", default="https://www.googleapis.com/drive/v3")

    materialize = sub.add_parser("materialize")
    materialize.add_argument("--archive", required=True)
    materialize.add_argument("--manifest", required=True)
    materialize.add_argument("--worktree", required=True)
    materialize.add_argument("--expected-head", required=True)

    resume = sub.add_parser("resume-action")
    resume.add_argument("--repository", required=True)
    resume.add_argument("--branch", required=True)
    resume.add_argument("--is-tag", choices=("true", "false"), required=True)
    resume.add_argument("--source-head", required=True)
    resume.add_argument("--source-tree", required=True)
    resume.add_argument("--api-root", default="https://www.googleapis.com/drive/v3")

    args = parser.parse_args()
    try:
        if args.command == "validate":
            validate_request(args.repository, args.branch, args.expected_head)
            return 0
        if args.command == "verify-remote":
            validate_request(args.repository, args.branch, args.expected_head)
            token = os.environ.get("TARGET_TOKEN", "")
            if not token:
                raise CheckpointPublishError("TARGET_TOKEN is required")
            GitHubClient(token, args.repository, args.api_root).verify_branch(args.branch, args.expected_head)
            return 0
        if args.command == "download":
            token = os.environ.get("GOOGLE_DRIVE_ACCESS_TOKEN", "")
            root = os.environ.get("GOOGLE_DRIVE_ROOT_FOLDER_ID", "")
            if not token:
                raise CheckpointPublishError("GOOGLE_DRIVE_ACCESS_TOKEN is required")
            checkpoint = load_canonical_checkpoint(
                DriveClient(token, args.api_root),
                root_folder_id=root,
                repository=args.repository,
                branch=args.branch,
                expected_head=args.expected_head,
            )
            write_checkpoint_files(
                checkpoint,
                archive_path=Path(args.archive),
                manifest_path=Path(args.manifest),
                message_path=Path(args.message),
            )
            print(json.dumps({
                "tree_sha": checkpoint.tree_sha,
                "archive_sha256": checkpoint.manifest["archive_sha256"],
                "archive_size_bytes": checkpoint.manifest["archive_size_bytes"],
            }, sort_keys=True, separators=(",", ":")))
            return 0
        if args.command == "materialize":
            value = _manifest_json(Path(args.manifest).read_bytes())
            tree = value.get("tree_sha")
            if not isinstance(tree, str) or SHA40.fullmatch(tree) is None:
                raise CheckpointPublishError("canonical checkpoint manifest has invalid tree SHA")
            worktree = Path(args.worktree)
            materialize_checkpoint(Path(args.archive), worktree)
            stage_and_verify_tree(worktree, expected_head=args.expected_head, expected_tree=tree)
            print(tree)
            return 0
        if args.command == "resume-action":
            token = os.environ.get("GOOGLE_DRIVE_ACCESS_TOKEN", "")
            root = os.environ.get("GOOGLE_DRIVE_ROOT_FOLDER_ID", "")
            if not token:
                raise CheckpointPublishError("GOOGLE_DRIVE_ACCESS_TOKEN is required")
            target_token = os.environ.get("TARGET_TOKEN", "")
            github_client = GitHubClient(target_token, args.repository) if target_token else None
            action = resume_snapshot_action(
                DriveClient(token, args.api_root),
                root_folder_id=root,
                repository=args.repository,
                branch=args.branch,
                is_tag=args.is_tag == "true",
                source_head=args.source_head,
                source_tree=args.source_tree,
                github_client=github_client,
            )
            print(action)
            return 0
        raise AssertionError(args.command)
    except CheckpointPublishError as exc:
        raise SystemExit(str(exc)) from None


if __name__ == "__main__":
    raise SystemExit(main())
