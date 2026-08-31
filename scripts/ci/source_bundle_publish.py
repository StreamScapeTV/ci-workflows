#!/usr/bin/env python3
"""Validate and publish one exact reviewed source bundle to one GitHub branch."""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

REPOSITORY = re.compile(r"StreamScapeTV/[A-Za-z0-9_.-]{1,100}\Z")
SHA40 = re.compile(r"[0-9a-f]{40}\Z")
SHA64 = re.compile(r"[0-9a-f]{64}\Z")
DRIVE_FILE_ID = re.compile(r"[A-Za-z0-9_-]{10,200}\Z")
ALLOWED_MODES = {"100644", "100755"}
MAX_FILES = 1000
MAX_PATH_BYTES = 512
MAX_FILE_BYTES = 100 * 1024 * 1024
MAX_UNCOMPRESSED_BYTES = 512 * 1024 * 1024
MAX_BUNDLE_BYTES = 512 * 1024 * 1024
ALLOWED_COMPRESSION = {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}


class BundlePublishError(RuntimeError):
    pass


def validate_request(repository: str, branch: str, expected_head: str, drive_file_id: str, bundle_sha256: str) -> None:
    if not REPOSITORY.fullmatch(repository):
        raise BundlePublishError("source bundle repository must be one bounded StreamScapeTV owner/name")
    if branch in {"main", "develop"}:
        raise BundlePublishError("source bundle publication refuses protected integration branch names")
    if not branch or branch.startswith("refs/") or "\x00" in branch or "\r" in branch or "\n" in branch:
        raise BundlePublishError("source bundle branch name is invalid")
    if len(branch.encode("utf-8")) > 255:
        raise BundlePublishError("source bundle branch name is too long")
    if not SHA40.fullmatch(expected_head):
        raise BundlePublishError("source bundle expected head must be one lowercase 40-character Git SHA")
    if not DRIVE_FILE_ID.fullmatch(drive_file_id):
        raise BundlePublishError("source bundle Drive file ID is invalid")
    if not SHA64.fullmatch(bundle_sha256):
        raise BundlePublishError("source bundle SHA-256 must be one lowercase 64-character digest")


def git_blob_digest(data: bytes, algorithm: str) -> str:
    header = f"blob {len(data)}\0".encode("ascii")
    digest = hashlib.new(algorithm)
    digest.update(header)
    digest.update(data)
    return digest.hexdigest()


def _json_no_duplicates(raw: bytes) -> Any:
    def pairs(values: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in values:
            if key in result:
                raise BundlePublishError(f"source bundle manifest contains duplicate key {key!r}")
            result[key] = value
        return result

    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=pairs)
    except BundlePublishError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise BundlePublishError("source bundle manifest is invalid UTF-8 JSON") from None


def _safe_path(value: Any) -> str:
    if (
        not isinstance(value, str)
        or not value
        or "\\" in value
        or "\x00" in value
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise BundlePublishError("source bundle path is invalid")
    if len(value.encode("utf-8")) > MAX_PATH_BYTES or value.startswith("/") or value.endswith("/"):
        raise BundlePublishError("source bundle path is invalid")
    pure = PurePosixPath(value)
    if pure.is_absolute() or any(part in {"", ".", "..", ".git"} for part in pure.parts):
        raise BundlePublishError("source bundle path is unsafe")
    canonical = pure.as_posix()
    if canonical != value or canonical == "manifest.json":
        raise BundlePublishError("source bundle path is not canonical")
    return canonical


def _zip_member_is_regular(info: zipfile.ZipInfo) -> bool:
    # ZIP creators commonly leave external attributes at zero. When a Unix type
    # is present, allow only regular files; explicitly reject symlinks/devices.
    mode = (info.external_attr >> 16) & 0xFFFF
    file_type = mode & 0o170000
    return file_type in (0, 0o100000) and not info.is_dir()


@dataclass(frozen=True, slots=True)
class BundleFile:
    path: str
    mode: str
    data: bytes
    git_sha1: str
    declared_algorithm: str
    declared_digest: str


@dataclass(frozen=True, slots=True)
class SourceBundle:
    files: tuple[BundleFile, ...]


def load_bundle(path: Path, expected_bundle_sha256: str) -> SourceBundle:
    try:
        size = path.stat().st_size
    except OSError:
        raise BundlePublishError("source bundle ZIP is missing") from None
    if size <= 0 or size > MAX_BUNDLE_BYTES:
        raise BundlePublishError("source bundle ZIP size is outside the bounded limit")
    digest = hashlib.sha256()
    try:
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError:
        raise BundlePublishError("source bundle ZIP could not be read") from None
    actual_bundle_digest = digest.hexdigest()
    if actual_bundle_digest != expected_bundle_sha256:
        raise BundlePublishError("source bundle ZIP SHA-256 does not match the reviewed digest")

    try:
        archive = zipfile.ZipFile(path)
    except (OSError, zipfile.BadZipFile):
        raise BundlePublishError("source bundle is not a valid ZIP archive") from None

    with archive:
        infos = archive.infolist()
        names = [info.filename for info in infos]
        if len(names) != len(set(names)):
            raise BundlePublishError("source bundle ZIP contains duplicate member names")
        if "manifest.json" not in names:
            raise BundlePublishError("source bundle ZIP requires root manifest.json")
        if len(infos) > MAX_FILES + 1:
            raise BundlePublishError("source bundle ZIP contains too many members")
        total_uncompressed = 0
        for info in infos:
            if info.flag_bits & 0x1:
                raise BundlePublishError("source bundle ZIP must not contain encrypted entries")
            if info.compress_type not in ALLOWED_COMPRESSION:
                raise BundlePublishError("source bundle ZIP uses an unsupported compression method")
            if not _zip_member_is_regular(info):
                raise BundlePublishError("source bundle ZIP must contain regular files only")
            if info.file_size < 0 or info.file_size > MAX_FILE_BYTES:
                raise BundlePublishError("source bundle ZIP member exceeds the bounded file-size limit")
            total_uncompressed += info.file_size
            if total_uncompressed > MAX_UNCOMPRESSED_BYTES:
                raise BundlePublishError("source bundle ZIP exceeds the bounded uncompressed-size limit")

        manifest = _json_no_duplicates(archive.read("manifest.json"))
        if not isinstance(manifest, dict) or set(manifest) != {"version", "files"} or manifest.get("version") != 1:
            raise BundlePublishError("source bundle manifest must contain exactly version=1 and files")
        entries = manifest.get("files")
        if not isinstance(entries, list) or not 1 <= len(entries) <= MAX_FILES:
            raise BundlePublishError("source bundle manifest files must be one bounded non-empty list")

        expected_members = {"manifest.json"}
        seen_paths: set[str] = set()
        bundle_files: list[BundleFile] = []
        for entry in entries:
            if not isinstance(entry, dict):
                raise BundlePublishError("source bundle manifest file entry must be one JSON object")
            keys = set(entry)
            has_sha1 = "blob_sha1" in entry
            has_sha256 = "blob_sha256" in entry
            digest_key = "blob_sha1" if has_sha1 else "blob_sha256" if has_sha256 else ""
            if has_sha1 == has_sha256 or keys != {"path", "mode", digest_key}:
                raise BundlePublishError(
                    "source bundle manifest file entry requires exactly path, mode, and one blob_sha1/blob_sha256"
                )
            file_path = _safe_path(entry.get("path"))
            if file_path in seen_paths:
                raise BundlePublishError("source bundle manifest contains duplicate repository paths")
            seen_paths.add(file_path)
            mode = entry.get("mode")
            if mode not in ALLOWED_MODES:
                raise BundlePublishError("source bundle file mode must be 100644 or 100755")
            algorithm = "sha1" if digest_key == "blob_sha1" else "sha256"
            declared = entry.get(digest_key)
            matcher = SHA40 if algorithm == "sha1" else SHA64
            if not isinstance(declared, str) or matcher.fullmatch(declared) is None:
                raise BundlePublishError(f"source bundle {digest_key} is invalid")
            member_name = f"payload/{file_path}"
            expected_members.add(member_name)
            try:
                data = archive.read(member_name)
            except KeyError:
                raise BundlePublishError(f"source bundle payload is missing for {file_path}") from None
            calculated = git_blob_digest(data, algorithm)
            if calculated != declared:
                raise BundlePublishError(f"source bundle payload digest mismatch for {file_path}")
            bundle_files.append(
                BundleFile(
                    path=file_path,
                    mode=mode,
                    data=data,
                    git_sha1=git_blob_digest(data, "sha1"),
                    declared_algorithm=algorithm,
                    declared_digest=declared,
                )
            )

        if set(names) != expected_members:
            raise BundlePublishError("source bundle ZIP contains members not declared by the manifest")
        ordered_paths = sorted(seen_paths)
        for left, right in zip(ordered_paths, ordered_paths[1:]):
            if right.startswith(left + "/"):
                raise BundlePublishError("source bundle repository paths contain a file/directory collision")
        return SourceBundle(tuple(bundle_files))


class GitHubClient:
    def __init__(self, token: str, repository: str, *, api_root: str = "https://api.github.com") -> None:
        if not token:
            raise BundlePublishError("TARGET_TOKEN is required")
        self.token = token
        self.repository = repository
        self.api_root = api_root.rstrip("/")
        owner, name = repository.split("/", 1)
        self.repo_path = f"/repos/{urllib.parse.quote(owner, safe='')}/{urllib.parse.quote(name, safe='')}"

    def _request(
        self,
        path: str,
        *,
        method: str = "GET",
        body: dict[str, Any] | None = None,
        expected: Iterable[int] = (200,),
    ) -> Any:
        data = None if body is None else json.dumps(body, separators=(",", ":")).encode("utf-8")
        request = urllib.request.Request(
            self.api_root + path,
            method=method,
            data=data,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self.token}",
                "X-GitHub-Api-Version": "2022-11-28",
                **({"Content-Type": "application/json"} if data is not None else {}),
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                status = int(getattr(response, "status", response.getcode()))
                raw = response.read()
        except urllib.error.HTTPError as exc:
            raise BundlePublishError(f"GitHub source bundle request was refused with HTTP {exc.code}") from None
        except urllib.error.URLError:
            raise BundlePublishError("GitHub source bundle request failed") from None
        if status not in set(expected):
            raise BundlePublishError(f"GitHub source bundle request returned unexpected HTTP {status}")
        if not raw:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            raise BundlePublishError("GitHub source bundle request returned invalid JSON") from None

    def repository_metadata(self) -> dict[str, Any]:
        value = self._request(self.repo_path)
        if not isinstance(value, dict):
            raise BundlePublishError("GitHub source bundle repository metadata is invalid")
        return value

    def branch_metadata(self, branch: str) -> dict[str, Any]:
        value = self._request(f"{self.repo_path}/branches/{urllib.parse.quote(branch, safe='')}")
        if not isinstance(value, dict):
            raise BundlePublishError("GitHub source bundle branch metadata is invalid")
        return value

    def commit_metadata(self, sha: str) -> dict[str, Any]:
        value = self._request(f"{self.repo_path}/git/commits/{sha}")
        if not isinstance(value, dict):
            raise BundlePublishError("GitHub source bundle commit metadata is invalid")
        return value

    def create_blob(self, data: bytes) -> str:
        value = self._request(
            f"{self.repo_path}/git/blobs",
            method="POST",
            body={"content": base64.b64encode(data).decode("ascii"), "encoding": "base64"},
            expected=(201,),
        )
        sha = value.get("sha") if isinstance(value, dict) else None
        if not isinstance(sha, str) or SHA40.fullmatch(sha) is None:
            raise BundlePublishError("GitHub source bundle blob creation returned invalid SHA")
        return sha

    def create_tree(self, base_tree: str, files: list[tuple[BundleFile, str]]) -> str:
        value = self._request(
            f"{self.repo_path}/git/trees",
            method="POST",
            body={
                "base_tree": base_tree,
                "tree": [
                    {"path": file.path, "mode": file.mode, "type": "blob", "sha": blob_sha}
                    for file, blob_sha in files
                ],
            },
            expected=(201,),
        )
        sha = value.get("sha") if isinstance(value, dict) else None
        if not isinstance(sha, str) or SHA40.fullmatch(sha) is None:
            raise BundlePublishError("GitHub source bundle tree creation returned invalid SHA")
        return sha

    def create_commit(self, tree_sha: str, parent_sha: str) -> str:
        value = self._request(
            f"{self.repo_path}/git/commits",
            method="POST",
            body={
                "message": "Publish reviewed source bundle via Central CI",
                "tree": tree_sha,
                "parents": [parent_sha],
            },
            expected=(201,),
        )
        sha = value.get("sha") if isinstance(value, dict) else None
        returned_tree = value.get("tree", {}).get("sha") if isinstance(value, dict) and isinstance(value.get("tree"), dict) else None
        returned_parents = value.get("parents") if isinstance(value, dict) else None
        if not isinstance(sha, str) or SHA40.fullmatch(sha) is None:
            raise BundlePublishError("GitHub source bundle commit creation returned invalid SHA")
        if returned_tree != tree_sha or not isinstance(returned_parents, list) or len(returned_parents) != 1:
            raise BundlePublishError("GitHub source bundle commit creation returned unexpected tree/parent metadata")
        parent_value = returned_parents[0]
        if not isinstance(parent_value, dict) or parent_value.get("sha") != parent_sha:
            raise BundlePublishError("GitHub source bundle commit creation returned unexpected parent")
        return sha

    def update_branch(self, branch: str, sha: str) -> None:
        value = self._request(
            f"{self.repo_path}/git/refs/heads/{urllib.parse.quote(branch, safe='/')}",
            method="PATCH",
            body={"sha": sha, "force": False},
            expected=(200,),
        )
        expected_ref = f"refs/heads/{branch}"
        observed_ref = value.get("ref") if isinstance(value, dict) else None
        observed_sha = value.get("object", {}).get("sha") if isinstance(value, dict) and isinstance(value.get("object"), dict) else None
        if observed_ref != expected_ref or observed_sha != sha:
            raise BundlePublishError("GitHub source bundle ref update returned unexpected ref/SHA")


def _live_head(client: Any, repository: str, branch: str, expected_head: str) -> tuple[dict[str, Any], str]:
    repository_value = client.repository_metadata()
    if repository_value.get("full_name") != repository:
        raise BundlePublishError("GitHub source bundle repository identity mismatch")
    if repository_value.get("default_branch") == branch:
        raise BundlePublishError("source bundle publication refuses the live default branch")
    branch_value = client.branch_metadata(branch)
    if branch_value.get("name") != branch:
        raise BundlePublishError("GitHub source bundle branch identity mismatch")
    if branch_value.get("protected") is not False:
        raise BundlePublishError("source bundle publication refuses a protected branch")
    head = branch_value.get("commit", {}).get("sha") if isinstance(branch_value.get("commit"), dict) else None
    if not isinstance(head, str) or SHA40.fullmatch(head) is None:
        raise BundlePublishError("GitHub source bundle branch returned invalid head")
    if head != expected_head:
        raise BundlePublishError("source bundle expected head is stale")
    return repository_value, head


def publish_bundle(
    client: Any,
    *,
    repository: str,
    branch: str,
    expected_head: str,
    bundle: SourceBundle,
) -> dict[str, Any]:
    # First fence is immediately before the first repository object write.
    _live_head(client, repository, branch, expected_head)
    commit = client.commit_metadata(expected_head)
    tree_value = commit.get("tree") if isinstance(commit, dict) else None
    base_tree = tree_value.get("sha") if isinstance(tree_value, dict) else None
    if not isinstance(base_tree, str) or SHA40.fullmatch(base_tree) is None:
        raise BundlePublishError("GitHub source bundle base commit returned invalid tree")

    created: list[tuple[BundleFile, str]] = []
    for file in bundle.files:
        blob_sha = client.create_blob(file.data)
        if blob_sha != file.git_sha1:
            raise BundlePublishError(f"GitHub source bundle blob identity mismatch for {file.path}")
        created.append((file, blob_sha))
    tree_sha = client.create_tree(base_tree, created)
    commit_sha = client.create_commit(tree_sha, expected_head)

    # Final exact-head fence is immediately before the only ref mutation.
    _live_head(client, repository, branch, expected_head)
    client.update_branch(branch, commit_sha)
    branch_value = client.branch_metadata(branch)
    observed = branch_value.get("commit", {}).get("sha") if isinstance(branch_value.get("commit"), dict) else None
    if observed != commit_sha:
        raise BundlePublishError("GitHub source bundle branch did not advance to the created commit")
    return {"commit_sha": commit_sha, "tree_sha": tree_sha, "files": len(bundle.files)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository", required=True)
    parser.add_argument("--branch", required=True)
    parser.add_argument("--expected-head", required=True)
    parser.add_argument("--drive-file-id", required=True)
    parser.add_argument("--bundle-sha256", required=True)
    parser.add_argument("--bundle", required=True, type=Path)
    parser.add_argument("--api-root", default="https://api.github.com")
    args = parser.parse_args()

    validate_request(args.repository, args.branch, args.expected_head, args.drive_file_id, args.bundle_sha256)
    try:
        bundle = load_bundle(args.bundle, args.bundle_sha256)
        result = publish_bundle(
            GitHubClient(os.environ.get("TARGET_TOKEN", ""), args.repository, api_root=args.api_root),
            repository=args.repository,
            branch=args.branch,
            expected_head=args.expected_head,
            bundle=bundle,
        )
    except BundlePublishError as exc:
        raise SystemExit(str(exc)) from None
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
