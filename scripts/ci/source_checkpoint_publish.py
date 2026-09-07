#!/usr/bin/env python3
"""Publish the unique latest immutable numbered Drive full-tree checkpoint."""
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
ALLOWED_COMPRESSION = {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}


class CheckpointPublishError(RuntimeError):
    pass


def validate_request(
    repository: str,
    branch: str,
    expected_head: str,
    expected_tree: str,
    archive_sha256: str,
    archive_size_bytes: int,
    commit_message: str,
) -> None:
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
    if not SHA40.fullmatch(expected_tree):
        raise CheckpointPublishError("checkpoint expected tree must be one lowercase 40-character Git SHA")
    if not SHA64.fullmatch(archive_sha256):
        raise CheckpointPublishError("checkpoint archive SHA-256 must be one lowercase 64-character digest")
    if isinstance(archive_size_bytes, bool) or not isinstance(archive_size_bytes, int) or not 1 <= archive_size_bytes <= MAX_BUNDLE_BYTES:
        raise CheckpointPublishError("checkpoint archive size is outside the bounded limit")
    if not isinstance(commit_message, str) or not commit_message.strip() or "\x00" in commit_message:
        raise CheckpointPublishError("checkpoint commit message is invalid")
    if len(commit_message.encode("utf-8")) > MAX_COMMIT_MESSAGE_BYTES:
        raise CheckpointPublishError("checkpoint commit message is too large")


def _query_literal(value: str) -> str:
    return value.replace("\\", "\\\\").replace("'", "\\'")


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def checkpoint_prefix(repository: str, branch: str) -> str:
    name = repository.rsplit("/", 1)[1]
    return f"{name}-{urllib.parse.quote(branch, safe='')}-checkpoint-"


def checkpoint_filename(repository: str, branch: str, sequence: int) -> str:
    if not 1 <= sequence <= 999999:
        raise CheckpointPublishError("checkpoint sequence is outside six-digit positive range")
    return f"{checkpoint_prefix(repository, branch)}{sequence:06d}.zip"


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
        if not DRIVE_FILE_ID.fullmatch(file_id or ""):
            raise CheckpointPublishError("Google Drive checkpoint file ID is invalid")
        return self._request(f"/files/{urllib.parse.quote(file_id, safe='')}?alt=media")


@dataclass(frozen=True)
class RemoteBranchState:
    action: str
    observed_head: str


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

    def classify_branch(
        self,
        *,
        branch: str,
        expected_head: str,
        expected_tree: str,
        commit_message: str,
    ) -> RemoteBranchState:
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
        if not isinstance(observed, str) or SHA40.fullmatch(observed) is None:
            raise CheckpointPublishError("GitHub checkpoint branch returned invalid head SHA")
        if observed == expected_head:
            return RemoteBranchState("publish", observed)

        commit = self._json(f"{self.repo_path}/commits/{urllib.parse.quote(observed, safe='')}")
        if not isinstance(commit, dict):
            raise CheckpointPublishError("GitHub checkpoint published-head metadata is invalid")
        commit_meta = commit.get("commit")
        tree = commit_meta.get("tree") if isinstance(commit_meta, dict) else None
        observed_tree = tree.get("sha") if isinstance(tree, dict) else None
        parents = commit.get("parents")
        message = commit_meta.get("message") if isinstance(commit_meta, dict) else None
        if (
            observed_tree == expected_tree
            and isinstance(parents, list)
            and len(parents) == 1
            and isinstance(parents[0], dict)
            and parents[0].get("sha") == expected_head
            and message == commit_message
        ):
            return RemoteBranchState("already-published", observed)
        raise CheckpointPublishError("checkpoint expected head is stale")


@dataclass(frozen=True)
class LatestCheckpoint:
    sequence: int
    filename: str
    file_id: str
    archive_bytes: bytes


def _unique(values: list[dict[str, Any]], label: str) -> dict[str, Any]:
    if len(values) != 1:
        raise CheckpointPublishError(f"Google Drive checkpoint requires exactly one canonical {label}")
    return values[0]


def select_latest_checkpoint_file(
    children: list[dict[str, Any]], *, repository: str, branch: str
) -> tuple[int, dict[str, Any]]:
    prefix = checkpoint_prefix(repository, branch)
    pattern = re.compile(re.escape(prefix) + r"([0-9]{6})\.zip\Z")
    by_sequence: dict[int, list[dict[str, Any]]] = {}
    for child in children:
        name = child.get("name")
        if not isinstance(name, str) or not name.startswith(prefix):
            continue
        match = pattern.fullmatch(name)
        if match is None:
            raise CheckpointPublishError("Google Drive checkpoint folder contains malformed matching checkpoint filename")
        sequence = int(match.group(1))
        if sequence < 1:
            raise CheckpointPublishError("Google Drive checkpoint sequence must be positive")
        if child.get("mimeType") == FOLDER_MIME:
            raise CheckpointPublishError("Google Drive checkpoint sequence resolves to a folder")
        by_sequence.setdefault(sequence, []).append(child)
    if not by_sequence:
        raise CheckpointPublishError("Google Drive checkpoint folder contains no numbered checkpoint")
    duplicates = [sequence for sequence, values in by_sequence.items() if len(values) != 1]
    if duplicates:
        raise CheckpointPublishError("Google Drive checkpoint folder contains duplicate checkpoint sequence")
    sequence = max(by_sequence)
    return sequence, by_sequence[sequence][0]


def load_latest_checkpoint(
    client: DriveClient,
    *,
    root_folder_id: str,
    repository: str,
    branch: str,
    expected_sha256: str,
    expected_size_bytes: int,
) -> LatestCheckpoint:
    if not DRIVE_FILE_ID.fullmatch(root_folder_id or ""):
        raise CheckpointPublishError("Google Drive repositories root folder ID is invalid")
    repository_name = repository.rsplit("/", 1)[1]
    repository_folder = _unique(client.exact_folders(root_folder_id, repository_name), "repository folder")
    ref_folder = _unique(client.exact_folders(repository_folder["id"], branch), "ref folder")
    children = client.children(ref_folder["id"])
    sequence, checkpoint_file = select_latest_checkpoint_file(children, repository=repository, branch=branch)
    filename = checkpoint_filename(repository, branch, sequence)
    if checkpoint_file.get("name") != filename:
        raise CheckpointPublishError("Google Drive checkpoint latest filename mismatch")
    raw_size = checkpoint_file.get("size")
    if raw_size is not None:
        try:
            metadata_size = int(raw_size)
        except (TypeError, ValueError):
            raise CheckpointPublishError("Google Drive checkpoint size metadata is invalid") from None
        if metadata_size != expected_size_bytes:
            raise CheckpointPublishError("latest Google Drive checkpoint size does not match request")
    archive_bytes = client.media(checkpoint_file["id"])
    if len(archive_bytes) != expected_size_bytes:
        raise CheckpointPublishError("latest Google Drive checkpoint byte size does not match request")
    if _sha256(archive_bytes) != expected_sha256:
        raise CheckpointPublishError("latest Google Drive checkpoint SHA-256 does not match request")
    return LatestCheckpoint(sequence, filename, checkpoint_file["id"], archive_bytes)


def _safe_member_path(name: str) -> PurePosixPath:
    if (
        not name
        or name.startswith("/")
        or "\\" in name
        or "\x00" in name
        or any(ord(character) < 32 or ord(character) == 127 for character in name)
        or len(name.encode("utf-8")) > MAX_PATH_BYTES
    ):
        raise CheckpointPublishError("checkpoint ZIP path is invalid")
    pure = PurePosixPath(name.rstrip("/"))
    if pure.is_absolute() or any(part in {"", ".", "..", ".git"} for part in pure.parts):
        raise CheckpointPublishError("checkpoint ZIP path is unsafe")
    if pure.as_posix() != name.rstrip("/"):
        raise CheckpointPublishError("checkpoint ZIP path is not canonical")
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
    raise CheckpointPublishError("checkpoint ZIP contains an unsupported file type")


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
    if not archive_path.is_file() or not 1 <= archive_path.stat().st_size <= MAX_BUNDLE_BYTES:
        raise CheckpointPublishError("checkpoint ZIP is missing or oversized")
    try:
        archive = zipfile.ZipFile(archive_path)
    except (OSError, zipfile.BadZipFile):
        raise CheckpointPublishError("checkpoint archive is not a valid ZIP") from None
    with archive:
        infos = archive.infolist()
        if not infos or len(infos) > MAX_MEMBERS:
            raise CheckpointPublishError("checkpoint ZIP member count is outside the bounded limit")
        names = [info.filename for info in infos]
        if len(names) != len(set(names)):
            raise CheckpointPublishError("checkpoint ZIP contains duplicate member names")
        total = 0
        entries: list[tuple[zipfile.ZipInfo, PurePosixPath, str, int]] = []
        non_dirs: set[PurePosixPath] = set()
        for info in infos:
            if info.flag_bits & 0x1:
                raise CheckpointPublishError("checkpoint ZIP must not contain encrypted members")
            if info.compress_type not in ALLOWED_COMPRESSION:
                raise CheckpointPublishError("checkpoint ZIP uses unsupported compression")
            if info.file_size < 0 or info.file_size > MAX_MEMBER_BYTES:
                raise CheckpointPublishError("checkpoint ZIP member is oversized")
            total += info.file_size
            if total > MAX_UNCOMPRESSED_BYTES:
                raise CheckpointPublishError("checkpoint ZIP is oversized after decompression")
            path = _safe_member_path(info.filename)
            kind, mode = _member_kind(info)
            if kind != "dir":
                non_dirs.add(path)
            entries.append((info, path, kind, mode))
        for path in non_dirs:
            parent = path.parent
            while parent != PurePosixPath("."):
                if parent in non_dirs:
                    raise CheckpointPublishError("checkpoint ZIP contains a file/directory collision")
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
                    raise CheckpointPublishError("checkpoint symlink target is invalid")
                os.symlink(data, os.fsencode(target))
            else:
                target.write_bytes(data)
                os.chmod(target, 0o755 if mode & 0o111 else 0o644)


def run_git(worktree: Path, *args: str) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(worktree), *args],
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or "").strip()
        raise CheckpointPublishError(f"Git checkpoint operation failed{': ' + detail if detail else ''}") from None
    return (result.stdout or "").strip()


def stage_and_verify_tree(worktree: Path, *, expected_head: str, expected_tree: str) -> str:
    head = run_git(worktree, "rev-parse", "HEAD")
    if head != expected_head:
        raise CheckpointPublishError("checkpoint worktree head does not match expected GitHub head")
    # Force-add is deliberate: a checkpoint represents the complete Git-tracked tree,
    # including paths that are tracked despite matching .gitignore rules.
    run_git(worktree, "add", "-f", "-A", "--", ".")
    tree = run_git(worktree, "write-tree")
    if tree != expected_tree:
        raise CheckpointPublishError("materialized checkpoint tree does not match expected tree SHA")
    base_tree = run_git(worktree, "rev-parse", "HEAD^{tree}")
    if tree == base_tree:
        raise CheckpointPublishError("checkpoint does not change the expected GitHub tree")
    return tree


def _positive_int(value: str) -> int:
    try:
        result = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("must be an integer") from None
    if result < 1:
        raise argparse.ArgumentTypeError("must be positive")
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)

    validate = sub.add_parser("validate")
    for command in (validate,):
        command.add_argument("--repository", required=True)
        command.add_argument("--branch", required=True)
        command.add_argument("--expected-head", required=True)
        command.add_argument("--expected-tree", required=True)
        command.add_argument("--archive-sha256", required=True)
        command.add_argument("--archive-size-bytes", required=True, type=_positive_int)
        command.add_argument("--commit-message", required=True)

    remote = sub.add_parser("classify-remote")
    remote.add_argument("--repository", required=True)
    remote.add_argument("--branch", required=True)
    remote.add_argument("--expected-head", required=True)
    remote.add_argument("--expected-tree", required=True)
    remote.add_argument("--commit-message", required=True)
    remote.add_argument("--api-root", default="https://api.github.com")

    download = sub.add_parser("download-latest")
    download.add_argument("--repository", required=True)
    download.add_argument("--branch", required=True)
    download.add_argument("--archive-sha256", required=True)
    download.add_argument("--archive-size-bytes", required=True, type=_positive_int)
    download.add_argument("--archive", required=True)
    download.add_argument("--api-root", default="https://www.googleapis.com/drive/v3")

    materialize = sub.add_parser("materialize")
    materialize.add_argument("--archive", required=True)
    materialize.add_argument("--worktree", required=True)
    materialize.add_argument("--expected-head", required=True)
    materialize.add_argument("--expected-tree", required=True)

    args = parser.parse_args()
    try:
        if args.command == "validate":
            validate_request(
                args.repository, args.branch, args.expected_head, args.expected_tree,
                args.archive_sha256, args.archive_size_bytes, args.commit_message,
            )
            return 0
        if args.command == "classify-remote":
            token = os.environ.get("TARGET_TOKEN", "")
            if not token:
                raise CheckpointPublishError("TARGET_TOKEN is required")
            state = GitHubClient(token, args.repository, args.api_root).classify_branch(
                branch=args.branch,
                expected_head=args.expected_head,
                expected_tree=args.expected_tree,
                commit_message=args.commit_message,
            )
            print(json.dumps({"action": state.action, "observed_head": state.observed_head}, sort_keys=True, separators=(",", ":")))
            return 0
        if args.command == "download-latest":
            token = os.environ.get("GOOGLE_DRIVE_ACCESS_TOKEN", "")
            root = os.environ.get("GOOGLE_DRIVE_ROOT_FOLDER_ID", "")
            if not token:
                raise CheckpointPublishError("GOOGLE_DRIVE_ACCESS_TOKEN is required")
            checkpoint = load_latest_checkpoint(
                DriveClient(token, args.api_root),
                root_folder_id=root,
                repository=args.repository,
                branch=args.branch,
                expected_sha256=args.archive_sha256,
                expected_size_bytes=args.archive_size_bytes,
            )
            Path(args.archive).write_bytes(checkpoint.archive_bytes)
            print(json.dumps({
                "checkpoint_sequence": checkpoint.sequence,
                "checkpoint_filename": checkpoint.filename,
                "archive_sha256": args.archive_sha256,
                "archive_size_bytes": args.archive_size_bytes,
            }, sort_keys=True, separators=(",", ":")))
            return 0
        if args.command == "materialize":
            materialize_checkpoint(Path(args.archive), Path(args.worktree))
            tree = stage_and_verify_tree(Path(args.worktree), expected_head=args.expected_head, expected_tree=args.expected_tree)
            print(tree)
            return 0
        raise AssertionError(args.command)
    except CheckpointPublishError as exc:
        raise SystemExit(str(exc)) from None


if __name__ == "__main__":
    raise SystemExit(main())
