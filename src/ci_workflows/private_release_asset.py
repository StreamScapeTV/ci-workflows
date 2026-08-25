"""Materialize one immutable private GitHub release asset into a trusted source checkout."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
from typing import Any, Callable, Mapping, Protocol
import urllib.error
import urllib.parse
import urllib.request
import zipfile

from .source_admission import _resolve_tag
from .source_github import GitHubSourceProvider

_MAX_METADATA_BYTES = 1024 * 1024
_MAX_ASSET_BYTES = 512 * 1024 * 1024
_MAX_UNCOMPRESSED_BYTES = 4 * 1024 * 1024 * 1024
_MAX_ARCHIVE_MEMBERS = 10000
_CHUNK_BYTES = 1024 * 1024
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


class PrivateReleaseAssetError(RuntimeError):
    """Stable non-sensitive release-asset failure."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise PrivateReleaseAssetError(code)


def _safe_relative(value: str, code: str) -> PurePosixPath:
    _require(
        bool(value)
        and len(value.encode("utf-8")) <= 1024
        and "\\" not in value
        and "\x00" not in value
        and "\r" not in value
        and "\n" not in value,
        code,
    )
    path = PurePosixPath(value)
    _require(
        not path.is_absolute()
        and value not in {".", ".."}
        and all(part not in {"", ".", ".."} for part in path.parts),
        code,
    )
    return path


def _repository_path(repository: str) -> str:
    owner, name = repository.split("/", 1)
    return f"/repos/{urllib.parse.quote(owner, safe='')}/{urllib.parse.quote(name, safe='')}"


def _read_json_response(response: Any) -> Mapping[str, Any]:
    raw = response.read(_MAX_METADATA_BYTES + 1)
    _require(len(raw) <= _MAX_METADATA_BYTES, "release_metadata_too_large")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise PrivateReleaseAssetError("release_metadata_invalid") from None
    _require(isinstance(value, Mapping), "release_metadata_invalid")
    return value


def _open_request(
    opener: Callable[..., Any],
    request: urllib.request.Request,
    *,
    code: str,
) -> Any:
    try:
        return opener(request, timeout=30)
    except urllib.error.HTTPError as error:
        raise PrivateReleaseAssetError(f"{code}_http_{int(error.code)}") from None
    except (OSError, urllib.error.URLError, TimeoutError, ValueError):
        raise PrivateReleaseAssetError(f"{code}_unavailable") from None


def _release_metadata(
    *,
    repository: str,
    tag: str,
    token: str,
    opener: Callable[..., Any],
) -> Mapping[str, Any]:
    tag_path = urllib.parse.quote(tag, safe="")
    request = urllib.request.Request(
        "https://api.github.com" + _repository_path(repository) + f"/releases/tags/{tag_path}",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "User-Agent": "StreamScapeTV-ci-workflows-release-asset",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with _open_request(opener, request, code="release_metadata") as response:
        return _read_json_response(response)


def _asset_record(release: Mapping[str, Any], *, tag: str, asset_name: str) -> Mapping[str, Any]:
    _require(release.get("tag_name") == tag and release.get("draft") is False, "release_metadata_mismatch")
    assets = release.get("assets")
    _require(isinstance(assets, list), "release_metadata_invalid")
    matches = [item for item in assets if isinstance(item, Mapping) and item.get("name") == asset_name]
    _require(len(matches) == 1, "release_asset_missing")
    asset = matches[0]
    asset_id = asset.get("id")
    size = asset.get("size")
    _require(isinstance(asset_id, int) and asset_id > 0, "release_asset_invalid")
    _require(isinstance(size, int) and 0 < size <= _MAX_ASSET_BYTES, "release_asset_size_invalid")
    _require(asset.get("state") == "uploaded", "release_asset_invalid")
    return asset


def _download_asset(
    *,
    repository: str,
    asset: Mapping[str, Any],
    token: str,
    target: Path,
    expected_sha256: str,
    opener: Callable[..., Any],
) -> int:
    asset_id = int(asset["id"])
    expected_size = int(asset["size"])
    request = urllib.request.Request(
        "https://api.github.com" + _repository_path(repository) + f"/releases/assets/{asset_id}",
        headers={
            "Accept": "application/octet-stream",
            "Authorization": f"Bearer {token}",
            "User-Agent": "StreamScapeTV-ci-workflows-release-asset",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    digest = hashlib.sha256()
    total = 0
    with _open_request(opener, request, code="release_asset_download") as response:
        try:
            with target.open("xb") as handle:
                while True:
                    chunk = response.read(_CHUNK_BYTES)
                    if not chunk:
                        break
                    total += len(chunk)
                    _require(total <= _MAX_ASSET_BYTES, "release_asset_size_invalid")
                    digest.update(chunk)
                    handle.write(chunk)
        except OSError:
            raise PrivateReleaseAssetError("release_asset_write_failed") from None
    _require(total == expected_size, "release_asset_size_mismatch")
    _require(digest.hexdigest() == expected_sha256, "release_asset_checksum_mismatch")
    return total


def _normalized_link_target(entry: PurePosixPath, target: str) -> PurePosixPath:
    _require(
        bool(target)
        and len(target.encode("utf-8")) <= 4096
        and "\\" not in target
        and "\x00" not in target
        and "\r" not in target
        and "\n" not in target,
        "release_archive_symlink_invalid",
    )
    raw = PurePosixPath(target)
    _require(not raw.is_absolute(), "release_archive_symlink_invalid")
    parts = list(entry.parent.parts)
    for part in raw.parts:
        if part in {"", "."}:
            continue
        if part == "..":
            _require(bool(parts), "release_archive_symlink_escape")
            parts.pop()
        else:
            parts.append(part)
    _require(bool(parts), "release_archive_symlink_escape")
    return PurePosixPath(*parts)


def _inspect_zip(path: Path) -> None:
    try:
        with zipfile.ZipFile(path) as archive:
            members = archive.infolist()
            _require(1 <= len(members) <= _MAX_ARCHIVE_MEMBERS, "release_archive_member_count")
            total = 0
            for info in members:
                name = info.filename
                _require(
                    bool(name)
                    and len(name.encode("utf-8")) <= 4096
                    and "\\" not in name
                    and "\x00" not in name
                    and "\r" not in name
                    and "\n" not in name,
                    "release_archive_path_invalid",
                )
                entry = PurePosixPath(name.rstrip("/"))
                _require(
                    not entry.is_absolute()
                    and all(part not in {"", ".", ".."} for part in entry.parts),
                    "release_archive_path_invalid",
                )
                total += int(info.file_size)
                _require(total <= _MAX_UNCOMPRESSED_BYTES, "release_archive_uncompressed_too_large")
                mode = (info.external_attr >> 16) & 0xFFFF
                kind = stat.S_IFMT(mode) if mode else 0
                _require(
                    kind in {0, stat.S_IFREG, stat.S_IFDIR, stat.S_IFLNK},
                    "release_archive_special_entry",
                )
                if kind == stat.S_IFLNK:
                    try:
                        target = archive.read(info).decode("utf-8")
                    except (OSError, UnicodeDecodeError, RuntimeError):
                        raise PrivateReleaseAssetError("release_archive_symlink_invalid") from None
                    _normalized_link_target(entry, target)
    except (OSError, zipfile.BadZipFile, zipfile.LargeZipFile):
        raise PrivateReleaseAssetError("release_archive_invalid") from None


def _ditto_extract(archive: Path, destination: Path) -> None:
    ditto = Path("/usr/bin/ditto")
    _require(ditto.is_file() and os.access(ditto, os.X_OK), "release_asset_extractor_unavailable")
    try:
        completed = subprocess.run(
            [str(ditto), "-x", "-k", str(archive), str(destination)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=180,
        )
    except (OSError, subprocess.SubprocessError):
        raise PrivateReleaseAssetError("release_asset_extract_failed") from None
    _require(completed.returncode == 0, "release_asset_extract_failed")


def _require_ignored(source_root: Path, destination: str) -> None:
    try:
        completed = subprocess.run(
            ["git", "check-ignore", "--no-index", "--quiet", "--", destination],
            cwd=source_root,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        raise PrivateReleaseAssetError("release_destination_ignore_check_failed") from None
    _require(completed.returncode == 0, "release_destination_not_ignored")


@dataclass(frozen=True, slots=True)
class PrivateReleaseAssetResult:
    destination: Path
    release_commit: str
    sha256: str
    downloaded_bytes: int


def materialize_private_release_asset(
    *,
    repository: str,
    tag: str,
    commit_sha: str,
    asset_name: str,
    sha256: str,
    archive_subpath: str,
    destination: str,
    token: str,
    source_root: Path,
    state_root: Path,
    opener: Callable[..., Any] = urllib.request.urlopen,
    provider_factory: Callable[[str], GitHubSourceProvider] = GitHubSourceProvider,
    extractor: Callable[[Path, Path], None] = _ditto_extract,
) -> PrivateReleaseAssetResult:
    """Verify and place one release archive subpath into an ignored source destination."""
    _require(bool(token), "release_asset_token_missing")
    _require(_SHA256.fullmatch(sha256) is not None, "release_asset_checksum_invalid")
    archive_relative = _safe_relative(archive_subpath, "release_archive_subpath_invalid")
    destination_relative = _safe_relative(destination, "release_destination_invalid")
    _require(asset_name.endswith(".zip") and "/" not in asset_name, "release_asset_name_invalid")

    source_root = source_root.resolve(strict=True)
    state_root = state_root.resolve(strict=True)
    _require(source_root.is_dir() and state_root.is_dir(), "release_materialization_root_invalid")
    destination_path = (source_root / Path(*destination_relative.parts)).resolve(strict=False)
    _require(source_root in destination_path.parents and not destination_path.exists(), "release_destination_occupied")
    _require_ignored(source_root, destination_relative.as_posix())

    provider = provider_factory(token)
    _tag_object, observed_commit = _resolve_tag(provider, repository, tag)
    _require(observed_commit == commit_sha, "release_tag_commit_mismatch")
    provider.commit(repository, observed_commit)

    release = _release_metadata(repository=repository, tag=tag, token=token, opener=opener)
    asset = _asset_record(release, tag=tag, asset_name=asset_name)

    work = state_root / "release-asset"
    if work.exists() or work.is_symlink():
        if work.is_symlink() or work.is_file():
            work.unlink(missing_ok=True)
        else:
            shutil.rmtree(work)
    work.mkdir(mode=0o700)
    archive_path = work / "asset.zip"
    extract_root = work / "extracted"
    extract_root.mkdir(mode=0o700)
    try:
        downloaded = _download_asset(
            repository=repository,
            asset=asset,
            token=token,
            target=archive_path,
            expected_sha256=sha256,
            opener=opener,
        )
        _inspect_zip(archive_path)
        extractor(archive_path, extract_root)
        candidate = (extract_root / Path(*archive_relative.parts)).resolve(strict=True)
        _require(extract_root in candidate.parents and candidate.is_dir() and not candidate.is_symlink(), "release_archive_subpath_missing")
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(candidate), str(destination_path))
        _require(destination_path.is_dir() and not destination_path.is_symlink(), "release_destination_invalid")
        return PrivateReleaseAssetResult(
            destination=destination_path,
            release_commit=observed_commit,
            sha256=sha256,
            downloaded_bytes=downloaded,
        )
    except (OSError, RuntimeError) as error:
        if isinstance(error, PrivateReleaseAssetError):
            raise
        raise PrivateReleaseAssetError("release_asset_materialization_failed") from None
    finally:
        if work.exists() and not work.is_symlink():
            shutil.rmtree(work, ignore_errors=True)


def cleanup_private_release_asset(result: PrivateReleaseAssetResult | None) -> None:
    if result is None:
        return
    path = result.destination
    _require(not path.is_symlink(), "release_destination_invalid")
    try:
        if path.exists():
            shutil.rmtree(path)
        parent = path.parent
        while parent.name and parent.exists() and not any(parent.iterdir()):
            parent.rmdir()
            parent = parent.parent
    except OSError:
        raise PrivateReleaseAssetError("release_asset_cleanup_failed") from None


__all__ = (
    "PrivateReleaseAssetError",
    "PrivateReleaseAssetResult",
    "cleanup_private_release_asset",
    "materialize_private_release_asset",
)
