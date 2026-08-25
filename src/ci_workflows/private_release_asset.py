"""Materialize one immutable private GitHub release asset into a trusted source checkout."""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
from typing import Any, Callable, Mapping, Sequence
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
_REPOSITORY = re.compile(r"[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}\Z")
_SHA = re.compile(r"[0-9a-f]{40}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_DEPENDENCY_ID = re.compile(r"[a-z][a-z0-9-]{1,31}\Z")
_ALLOWED_REDIRECT_HOSTS = {
    "objects.githubusercontent.com",
    "release-assets.githubusercontent.com",
    "github-releases.githubusercontent.com",
}
_SPEC_FIELDS = (
    "repository",
    "tag",
    "commit_sha",
    "asset_name",
    "sha256",
    "archive_subpath",
    "destination",
    "id",
)
_STATE_DIRECTORY = "release-assets"


class PrivateReleaseAssetError(RuntimeError):
    """Stable non-sensitive release-asset failure."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise PrivateReleaseAssetError(code)


def _safe_text(value: object, *, maximum: int, code: str) -> str:
    _require(isinstance(value, str), code)
    text = value
    _require(
        1 <= len(text.encode("utf-8")) <= maximum
        and text.strip() == text
        and "\x00" not in text
        and "\r" not in text
        and "\n" not in text,
        code,
    )
    return text


def _safe_tag(value: object) -> str:
    text = _safe_text(value, maximum=256, code="release_tag_invalid")
    _require(
        "\\" not in text
        and not text.startswith("-")
        and not text.startswith("/")
        and not text.endswith("/")
        and not text.endswith(".")
        and "//" not in text
        and ".." not in text
        and "@{" not in text
        and not any(part.endswith(".lock") for part in text.split("/")),
        "release_tag_invalid",
    )
    return text


def _safe_asset_name(value: object) -> str:
    text = _safe_text(value, maximum=255, code="release_asset_name_invalid")
    _require(
        text.endswith(".zip")
        and "/" not in text
        and "\\" not in text
        and text not in {".", ".."},
        "release_asset_name_invalid",
    )
    return text


def _safe_relative(value: object, code: str) -> PurePosixPath:
    text = _safe_text(value, maximum=1024, code=code)
    _require("\\" not in text, code)
    path = PurePosixPath(text)
    _require(
        not path.is_absolute()
        and text not in {".", ".."}
        and all(part not in {"", ".", ".."} for part in path.parts),
        code,
    )
    return path


@dataclass(frozen=True, slots=True)
class PrivateReleaseAssetSpec:
    repository: str
    tag: str
    commit_sha: str
    asset_name: str
    sha256: str
    archive_subpath: str
    destination: str
    dependency_id: str

    @classmethod
    def parse(cls, value: Mapping[str, object]) -> "PrivateReleaseAssetSpec":
        _require(set(value) == set(_SPEC_FIELDS), "private_release_asset_invalid")
        repository = _safe_text(value.get("repository"), maximum=256, code="release_repository_invalid")
        _require(_REPOSITORY.fullmatch(repository) is not None, "release_repository_invalid")
        commit_sha = _safe_text(value.get("commit_sha"), maximum=40, code="release_commit_invalid")
        digest = _safe_text(value.get("sha256"), maximum=64, code="release_asset_checksum_invalid")
        dependency_id = _safe_text(value.get("id"), maximum=32, code="release_asset_id_invalid")
        _require(_SHA.fullmatch(commit_sha) is not None, "release_commit_invalid")
        _require(_SHA256.fullmatch(digest) is not None, "release_asset_checksum_invalid")
        _require(_DEPENDENCY_ID.fullmatch(dependency_id) is not None, "release_asset_id_invalid")
        archive = _safe_relative(value.get("archive_subpath"), "release_archive_subpath_invalid")
        destination = _safe_relative(value.get("destination"), "release_destination_invalid")
        return cls(
            repository=repository,
            tag=_safe_tag(value.get("tag")),
            commit_sha=commit_sha,
            asset_name=_safe_asset_name(value.get("asset_name")),
            sha256=digest,
            archive_subpath=archive.as_posix(),
            destination=destination.as_posix(),
            dependency_id=dependency_id,
        )

    def as_payload(self) -> dict[str, str]:
        return {
            "repository": self.repository,
            "tag": self.tag,
            "commit_sha": self.commit_sha,
            "asset_name": self.asset_name,
            "sha256": self.sha256,
            "archive_subpath": self.archive_subpath,
            "destination": self.destination,
            "id": self.dependency_id,
        }


def optional_spec(value: object) -> PrivateReleaseAssetSpec | None:
    if value is None:
        return None
    _require(isinstance(value, Mapping), "private_release_asset_invalid")
    return PrivateReleaseAssetSpec.parse(value)


def _repository_path(repository: str) -> str:
    owner, name = repository.split("/", 1)
    return f"/repos/{urllib.parse.quote(owner, safe='')}/{urllib.parse.quote(name, safe='')}"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> None:
        return None


def _no_redirect_open(request: urllib.request.Request, *, timeout: int) -> Any:
    return urllib.request.build_opener(_NoRedirect).open(request, timeout=timeout)


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
    api_opener: Callable[..., Any],
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
    with _open_request(api_opener, request, code="release_metadata") as response:
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


def _redirect_location(error: urllib.error.HTTPError) -> str | None:
    if int(error.code) not in {301, 302, 303, 307, 308}:
        return None
    location = error.headers.get("Location") if error.headers is not None else None
    if not isinstance(location, str) or not location:
        return None
    parsed = urllib.parse.urlparse(location)
    _require(
        parsed.scheme == "https"
        and parsed.hostname in _ALLOWED_REDIRECT_HOSTS
        and parsed.username is None
        and parsed.password is None
        and parsed.port in {None, 443},
        "release_asset_redirect_invalid",
    )
    return location


def _asset_response(
    request: urllib.request.Request,
    *,
    api_opener: Callable[..., Any],
    asset_opener: Callable[..., Any],
) -> Any:
    try:
        return api_opener(request, timeout=30)
    except urllib.error.HTTPError as error:
        location = _redirect_location(error)
        if location is None:
            raise PrivateReleaseAssetError(f"release_asset_download_http_{int(error.code)}") from None
        redirected = urllib.request.Request(
            location,
            headers={"User-Agent": "StreamScapeTV-ci-workflows-release-asset"},
        )
        return _open_request(asset_opener, redirected, code="release_asset_blob")
    except (OSError, urllib.error.URLError, TimeoutError, ValueError):
        raise PrivateReleaseAssetError("release_asset_download_unavailable") from None


def _download_asset(
    *,
    repository: str,
    asset: Mapping[str, Any],
    token: str,
    target: Path,
    expected_sha256: str,
    api_opener: Callable[..., Any],
    asset_opener: Callable[..., Any],
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
    with _asset_response(request, api_opener=api_opener, asset_opener=asset_opener) as response:
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
                _require(kind in {0, stat.S_IFREG, stat.S_IFDIR, stat.S_IFLNK}, "release_archive_special_entry")
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
    source_root: Path
    destination: Path
    release_commit: str
    sha256: str
    downloaded_bytes: int


def materialize_private_release_asset(
    *,
    spec: PrivateReleaseAssetSpec,
    token: str,
    source_root: Path,
    state_root: Path,
    api_opener: Callable[..., Any] = _no_redirect_open,
    asset_opener: Callable[..., Any] = urllib.request.urlopen,
    provider_factory: Callable[[str], GitHubSourceProvider] = GitHubSourceProvider,
    extractor: Callable[[Path, Path], None] = _ditto_extract,
) -> PrivateReleaseAssetResult:
    _require(bool(token), "release_asset_token_missing")
    source_root = source_root.resolve(strict=True)
    state_root = state_root.resolve(strict=True)
    _require(source_root.is_dir() and state_root.is_dir(), "release_materialization_root_invalid")
    destination_relative = _safe_relative(spec.destination, "release_destination_invalid")
    archive_relative = _safe_relative(spec.archive_subpath, "release_archive_subpath_invalid")
    destination_path = (source_root / Path(*destination_relative.parts)).resolve(strict=False)
    _require(source_root in destination_path.parents and not destination_path.exists(), "release_destination_occupied")
    _require_ignored(source_root, destination_relative.as_posix())

    provider = provider_factory(token)
    _tag_object, observed_commit = _resolve_tag(provider, spec.repository, spec.tag)
    _require(observed_commit == spec.commit_sha, "release_tag_commit_mismatch")
    provider.commit(spec.repository, observed_commit)

    release = _release_metadata(
        repository=spec.repository,
        tag=spec.tag,
        token=token,
        api_opener=api_opener,
    )
    asset = _asset_record(release, tag=spec.tag, asset_name=spec.asset_name)

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
            repository=spec.repository,
            asset=asset,
            token=token,
            target=archive_path,
            expected_sha256=spec.sha256,
            api_opener=api_opener,
            asset_opener=asset_opener,
        )
        _inspect_zip(archive_path)
        extractor(archive_path, extract_root)
        lexical_candidate = extract_root / Path(*archive_relative.parts)
        try:
            candidate_metadata = os.lstat(lexical_candidate)
            candidate = lexical_candidate.resolve(strict=True)
        except OSError:
            raise PrivateReleaseAssetError("release_archive_subpath_missing") from None
        _require(
            stat.S_ISDIR(candidate_metadata.st_mode)
            and not stat.S_ISLNK(candidate_metadata.st_mode)
            and extract_root in candidate.parents,
            "release_archive_subpath_missing",
        )
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(candidate), str(destination_path))
        _require(destination_path.is_dir() and not destination_path.is_symlink(), "release_destination_invalid")
        return PrivateReleaseAssetResult(
            source_root=source_root,
            destination=destination_path,
            release_commit=observed_commit,
            sha256=spec.sha256,
            downloaded_bytes=downloaded,
        )
    except Exception as error:
        if destination_path.exists() and not destination_path.is_symlink():
            shutil.rmtree(destination_path, ignore_errors=True)
        if isinstance(error, PrivateReleaseAssetError):
            raise
        raise PrivateReleaseAssetError("release_asset_materialization_failed") from None
    finally:
        if work.exists() and not work.is_symlink():
            shutil.rmtree(work, ignore_errors=True)


def cleanup_private_release_asset(result: PrivateReleaseAssetResult | None) -> None:
    if result is None:
        return
    source_root = result.source_root.resolve(strict=True)
    path = result.destination
    _require(source_root in path.parents and not path.is_symlink(), "release_destination_invalid")
    try:
        if path.exists():
            shutil.rmtree(path)
        parent = path.parent
        while parent != source_root and source_root in parent.parents and parent.exists() and not any(parent.iterdir()):
            parent.rmdir()
            parent = parent.parent
    except OSError:
        raise PrivateReleaseAssetError("release_asset_cleanup_failed") from None


def _environment_spec(environment: Mapping[str, str]) -> PrivateReleaseAssetSpec | None:
    values = {
        "repository": environment.get("INPUT_RELEASE_ASSET_REPOSITORY", ""),
        "tag": environment.get("INPUT_RELEASE_ASSET_TAG", ""),
        "commit_sha": environment.get("INPUT_RELEASE_ASSET_COMMIT_SHA", ""),
        "asset_name": environment.get("INPUT_RELEASE_ASSET_NAME", ""),
        "sha256": environment.get("INPUT_RELEASE_ASSET_SHA256", ""),
        "archive_subpath": environment.get("INPUT_RELEASE_ASSET_ARCHIVE_SUBPATH", ""),
        "destination": environment.get("INPUT_RELEASE_ASSET_DESTINATION", ""),
        "id": environment.get("INPUT_RELEASE_ASSET_ID", ""),
    }
    if not any(values.values()):
        return None
    _require(all(values.values()), "private_release_asset_incomplete")
    return PrivateReleaseAssetSpec.parse(values)


def _required_path(environment: Mapping[str, str], name: str) -> Path:
    value = environment.get(name, "")
    _require(bool(value), f"missing_{name.lower()}")
    path = Path(value)
    if not path.is_absolute():
        path = Path.cwd() / path
    try:
        return path.resolve(strict=True)
    except OSError:
        raise PrivateReleaseAssetError(f"invalid_{name.lower()}") from None


def _state_file(state_root: Path, spec: PrivateReleaseAssetSpec, *, create: bool) -> Path:
    directory = (state_root / _STATE_DIRECTORY).resolve(strict=False)
    _require(state_root == directory.parent, "release_asset_state_invalid")
    if create:
        directory.mkdir(mode=0o700, exist_ok=True)
    return directory / f"{spec.dependency_id}.json"


def _write_state(state_root: Path, spec: PrivateReleaseAssetSpec, result: PrivateReleaseAssetResult) -> None:
    path = _state_file(state_root, spec, create=True)
    _require(not path.exists(), "release_asset_state_occupied")
    payload = json.dumps(
        {
            "destination": spec.destination,
            "release_commit": result.release_commit,
            "sha256": result.sha256,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    temporary = path.with_suffix(".tmp")
    temporary.write_text(payload, encoding="utf-8")
    temporary.chmod(0o600)
    temporary.replace(path)
    path.chmod(0o600)


def _load_state(
    state_root: Path,
    spec: PrivateReleaseAssetSpec,
    source_root: Path,
) -> PrivateReleaseAssetResult:
    path = _state_file(state_root, spec, create=False)
    _require(path.is_file() and not path.is_symlink(), "release_asset_state_missing")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise PrivateReleaseAssetError("release_asset_state_invalid") from None
    _require(
        isinstance(value, dict)
        and value.get("destination") == spec.destination
        and value.get("release_commit") == spec.commit_sha
        and value.get("sha256") == spec.sha256,
        "release_asset_state_invalid",
    )
    destination = (source_root / Path(*PurePosixPath(spec.destination).parts)).resolve(strict=False)
    _require(source_root in destination.parents, "release_destination_invalid")
    return PrivateReleaseAssetResult(
        source_root=source_root,
        destination=destination,
        release_commit=spec.commit_sha,
        sha256=spec.sha256,
        downloaded_bytes=0,
    )


def _write_outputs(environment: Mapping[str, str], values: Mapping[str, str]) -> None:
    output = environment.get("GITHUB_OUTPUT", "")
    if not output:
        return
    path = Path(output)
    _require(path.is_absolute(), "invalid_github_output")
    try:
        with path.open("a", encoding="utf-8") as handle:
            for key, value in values.items():
                _require("\n" not in value and "\r" not in value, "invalid_release_asset_output")
                handle.write(f"{key}={value}\n")
    except OSError:
        raise PrivateReleaseAssetError("github_output_unavailable") from None


def run_phase(phase: str, environment: Mapping[str, str] = os.environ) -> None:
    spec = _environment_spec(environment)
    dependency = environment.get("INPUT_PRIVATE_DEPENDENCY_REPOSITORY", "")
    if phase == "plan":
        _require(not (spec is not None and dependency), "private_dependency_kind_conflict")
        values = {"used": "false"}
        if spec is not None:
            values = {"used": "true", **spec.as_payload()}
        _write_outputs(environment, values)
        return
    if spec is None:
        _write_outputs(environment, {"verified": "false"})
        return
    source_root = _required_path({"SOURCE_ROOT": environment.get("INPUT_SOURCE_ROOT", "source")}, "SOURCE_ROOT")
    state_root = _required_path(environment, "CI_WORKFLOW_ROOT")
    state_path = _state_file(state_root, spec, create=False)
    destination = (source_root / Path(*PurePosixPath(spec.destination).parts)).resolve(strict=False)
    if phase == "execute":
        result = materialize_private_release_asset(
            spec=spec,
            token=environment.get("PRIVATE_RELEASE_ASSET_TOKEN", ""),
            source_root=source_root,
            state_root=state_root,
        )
        _write_state(state_root, spec, result)
        _write_outputs(
            environment,
            {
                "verified": "true",
                "release_commit": result.release_commit,
                "sha256": result.sha256,
                "destination": spec.destination,
            },
        )
        return
    if phase == "cleanup":
        result = _load_state(state_root, spec, source_root)
        cleanup_private_release_asset(result)
        state_path.unlink(missing_ok=False)
        directory = state_path.parent
        if directory.exists() and not any(directory.iterdir()):
            directory.rmdir()
        _write_outputs(environment, {"verified": "true"})
        return
    if phase == "residue":
        _require(not state_path.exists() and not destination.exists(), "release_asset_residue")
        _write_outputs(environment, {"verified": "true"})
        return
    raise PrivateReleaseAssetError("release_asset_phase_invalid")


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("phase", choices=("plan", "execute", "cleanup", "residue"))
    return result


def main(argv: Sequence[str] | None = None, environment: Mapping[str, str] = os.environ) -> int:
    args = parser().parse_args(list(argv) if argv is not None else None)
    try:
        run_phase(args.phase, environment)
    except PrivateReleaseAssetError as error:
        print(error.code, file=sys.stderr)
        return 1
    return 0


__all__ = (
    "PrivateReleaseAssetError",
    "PrivateReleaseAssetResult",
    "PrivateReleaseAssetSpec",
    "cleanup_private_release_asset",
    "main",
    "materialize_private_release_asset",
    "optional_spec",
    "run_phase",
)
