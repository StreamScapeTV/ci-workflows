"""Product-neutral Git tag and GitHub Release primitives."""
from __future__ import annotations

import http.client
import json
import os
import re
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, Sequence

_FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
_REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
_TAG_VERSION = re.compile(
    r"^v?(?P<version>"
    r"(?:0|[1-9][0-9]*)\."
    r"(?:0|[1-9][0-9]*)\."
    r"(?:0|[1-9][0-9]*)"
    r"(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
    r")$"
)
_ASSET_NAME = re.compile(r"^[^/\\\x00-\x1f\x7f]{1,255}$")
_ERROR_CODE = re.compile(r"^[a-z][a-z0-9_]{2,95}$")
_MAX_JSON_RESPONSE = 2_000_000
_MAX_ASSET_BYTES = 2 * 1024 * 1024 * 1024


class ReleasePrimitiveError(RuntimeError):
    """Fail closed with one stable, non-secret release primitive code."""

    def __init__(self, code: str) -> None:
        if _ERROR_CODE.fullmatch(code) is None:
            raise ValueError("release primitive error code must be a safe identifier")
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class VersionTag:
    """Validated Git tag and normalized SemVer."""

    tag: str
    version: str
    ref: str


@dataclass(frozen=True, slots=True)
class GitCommitMetadata:
    """Non-secret commit metadata resolved from one release tag."""

    sha: str
    committed_at: int
    author_name: str
    author_email: str
    subject: str


@dataclass(frozen=True, slots=True)
class GitTagMetadata:
    """Tag object metadata plus the commit reached by the tag."""

    tag: str
    version: str
    ref: str
    object_type: str
    object_sha: str
    annotated: bool
    tagger_name: str | None
    tagger_email: str | None
    tagged_at: int | None
    subject: str
    commit: GitCommitMetadata


@dataclass(frozen=True, slots=True)
class GitHubReleaseRequest:
    """Caller-owned metadata for one ordinary GitHub Release."""

    repository: str
    tag: str
    title: str
    notes: str = ""
    target_commitish: str | None = None
    draft: bool = False
    prerelease: bool = False
    generate_release_notes: bool = False


@dataclass(frozen=True, slots=True)
class GitHubReleaseResult:
    """Structured result from creating or updating a GitHub Release."""

    action: str
    release_id: int
    repository: str
    tag: str
    title: str
    url: str
    upload_url: str
    draft: bool
    prerelease: bool


@dataclass(frozen=True, slots=True)
class GitHubAssetResult:
    """Structured optional release-asset result."""

    present: bool
    uploaded: bool
    name: str
    size: int
    asset_id: int | None
    url: str


@dataclass(frozen=True, slots=True)
class GitProcessResult:
    """Minimal mockable Git command boundary."""

    returncode: int
    stdout: str
    stderr: str


class GitRunner(Protocol):
    def __call__(self, arguments: Sequence[str], cwd: Path) -> GitProcessResult: ...


class AssetUploader(Protocol):
    def __call__(
        self,
        url: str,
        headers: Mapping[str, str],
        path: Path,
        content_type: str,
    ) -> Mapping[str, Any]: ...


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise ReleasePrimitiveError(code)


def _single_line(value: Any, *, code: str, maximum: int) -> str:
    _require(isinstance(value, str), code)
    _require(value == value.strip(), code)
    _require(bool(value) and len(value) <= maximum, code)
    _require(not any(character in value for character in ("\x00", "\r", "\n")), code)
    return value


def _optional_single_line(value: Any, *, code: str, maximum: int) -> str | None:
    if value in {None, ""}:
        return None
    return _single_line(value, code=code, maximum=maximum)


def _repository(value: Any) -> str:
    _require(
        isinstance(value, str) and _REPOSITORY.fullmatch(value) is not None,
        "repository_invalid",
    )
    return value


def _sha(value: Any, *, code: str) -> str:
    _require(isinstance(value, str) and _FULL_SHA.fullmatch(value) is not None, code)
    return value


def derive_version_from_ref(ref_or_tag: str) -> VersionTag:
    """Validate a Git tag/ref and derive its normalized SemVer without a leading ``v``."""

    value = _single_line(ref_or_tag, code="release_ref_invalid", maximum=255)
    if value.startswith("refs/tags/"):
        tag = value.removeprefix("refs/tags/")
    else:
        _require(not value.startswith("refs/"), "release_ref_invalid")
        tag = value
    match = _TAG_VERSION.fullmatch(tag)
    _require(match is not None, "release_version_invalid")
    assert match is not None
    version = match.group("version")
    return VersionTag(tag=tag, version=version, ref=f"refs/tags/{tag}")


def _default_git_runner(arguments: Sequence[str], cwd: Path) -> GitProcessResult:
    try:
        completed = subprocess.run(
            ["git", *arguments],
            cwd=cwd,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ReleasePrimitiveError("git_command_unavailable") from error
    return GitProcessResult(completed.returncode, completed.stdout, completed.stderr)


def _git(
    runner: GitRunner,
    arguments: Sequence[str],
    cwd: Path,
    *,
    code: str,
) -> str:
    result = runner(tuple(arguments), cwd)
    _require(isinstance(result, GitProcessResult), "git_runner_result_invalid")
    _require(result.returncode == 0, code)
    _require(isinstance(result.stdout, str), "git_output_invalid")
    return result.stdout.rstrip("\n")


def _timestamp(value: str, *, code: str) -> int:
    _require(value.isdecimal(), code)
    parsed = int(value)
    _require(parsed >= 0, code)
    return parsed


def _email(value: str) -> str:
    cleaned = value.strip()
    if cleaned.startswith("<") and cleaned.endswith(">"):
        cleaned = cleaned[1:-1]
    return cleaned


def inspect_git_tag(
    repository_root: Path,
    ref_or_tag: str,
    *,
    runner: GitRunner = _default_git_runner,
) -> GitTagMetadata:
    """Inspect one lightweight or annotated SemVer tag and its commit metadata."""

    root = Path(repository_root)
    _require(root.is_absolute(), "git_repository_invalid")
    _require(not root.is_symlink() and root.is_dir(), "git_repository_invalid")
    try:
        root = root.resolve(strict=True)
    except OSError as error:
        raise ReleasePrimitiveError("git_repository_invalid") from error

    version_tag = derive_version_from_ref(ref_or_tag)
    format_value = (
        "%(objecttype)%00%(objectname)%00%(*objectname)%00"
        "%(taggername)%00%(taggeremail)%00%(taggerdate:unix)%00%(subject)"
    )
    tag_line = _git(
        runner,
        (
            "for-each-ref",
            f"--format={format_value}",
            version_tag.ref,
        ),
        root,
        code="git_tag_missing",
    )
    _require(tag_line and "\n" not in tag_line, "git_tag_metadata_invalid")
    tag_fields = tag_line.split("\x00")
    _require(len(tag_fields) == 7, "git_tag_metadata_invalid")
    object_type, object_sha, peeled_sha, tagger_name, tagger_email, tagged_at, subject = tag_fields
    _require(object_type in {"commit", "tag"}, "git_tag_object_type_invalid")
    object_sha = _sha(object_sha, code="git_tag_object_sha_invalid")
    annotated = object_type == "tag"
    commit_sha = (
        _sha(peeled_sha, code="git_tag_commit_sha_invalid")
        if annotated
        else object_sha
    )
    if annotated:
        _require(bool(tagger_name.strip()), "git_tag_metadata_invalid")
        _require(bool(_email(tagger_email)), "git_tag_metadata_invalid")
        tag_timestamp: int | None = _timestamp(tagged_at, code="git_tag_time_invalid")
    else:
        _require(not peeled_sha, "git_tag_metadata_invalid")
        tag_timestamp = None

    commit_line = _git(
        runner,
        ("show", "-s", "--format=%H%x00%ct%x00%an%x00%ae%x00%s", commit_sha),
        root,
        code="git_commit_missing",
    )
    _require(commit_line and "\n" not in commit_line, "git_commit_metadata_invalid")
    commit_fields = commit_line.split("\x00")
    _require(len(commit_fields) == 5, "git_commit_metadata_invalid")
    actual_sha, committed_at, author_name, author_email, commit_subject = commit_fields
    actual_sha = _sha(actual_sha, code="git_commit_sha_invalid")
    _require(actual_sha == commit_sha, "git_commit_sha_mismatch")
    _require(bool(author_name.strip()), "git_commit_metadata_invalid")
    _require(bool(author_email.strip()), "git_commit_metadata_invalid")

    return GitTagMetadata(
        tag=version_tag.tag,
        version=version_tag.version,
        ref=version_tag.ref,
        object_type=object_type,
        object_sha=object_sha,
        annotated=annotated,
        tagger_name=tagger_name.strip() if annotated else None,
        tagger_email=_email(tagger_email) if annotated else None,
        tagged_at=tag_timestamp,
        subject=subject.strip(),
        commit=GitCommitMetadata(
            sha=actual_sha,
            committed_at=_timestamp(committed_at, code="git_commit_time_invalid"),
            author_name=author_name.strip(),
            author_email=author_email.strip(),
            subject=commit_subject.strip(),
        ),
    )


def _github_token(environment: Mapping[str, str], token_environment: str) -> str:
    _require(
        isinstance(token_environment, str)
        and _ENV_NAME.fullmatch(token_environment) is not None,
        "github_token_environment_invalid",
    )
    token = environment.get(token_environment, "")
    _require(isinstance(token, str) and bool(token) and "\x00" not in token, "github_token_required")
    return token


def _github_api_url(environment: Mapping[str, str], api_url: str | None) -> str:
    value = api_url or environment.get("GITHUB_API_URL", "https://api.github.com")
    _require(isinstance(value, str), "github_api_url_invalid")
    value = value.rstrip("/")
    parsed = urllib.parse.urlsplit(value)
    _require(
        parsed.scheme == "https"
        and parsed.hostname == "api.github.com"
        and parsed.username is None
        and parsed.password is None
        and parsed.port is None
        and parsed.path == ""
        and parsed.query == ""
        and parsed.fragment == "",
        "github_api_url_invalid",
    )
    return value


def _github_headers(token: str) -> dict[str, str]:
    return {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _read_json_body(response: Any) -> Mapping[str, Any]:
    try:
        raw = response.read(_MAX_JSON_RESPONSE + 1)
    except OSError as error:
        raise ReleasePrimitiveError("github_response_unavailable") from error
    _require(len(raw) <= _MAX_JSON_RESPONSE, "github_response_too_large")
    try:
        payload = json.loads(raw.decode("utf-8")) if raw else {}
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ReleasePrimitiveError("github_response_invalid") from error
    _require(isinstance(payload, Mapping), "github_response_invalid")
    return payload


def _github_json_request(
    method: str,
    url: str,
    *,
    headers: Mapping[str, str],
    payload: Mapping[str, Any] | None,
    opener: Callable[..., Any],
    missing_ok: bool = False,
) -> Mapping[str, Any] | None:
    data = None
    if payload is not None:
        data = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    request_headers = dict(headers)
    if data is not None:
        request_headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=request_headers, method=method)
    try:
        with opener(request, timeout=30) as response:
            status = int(getattr(response, "status", response.getcode()))
            _require(200 <= status < 300, f"github_http_{status}")
            return _read_json_body(response)
    except urllib.error.HTTPError as error:
        if missing_ok and error.code == 404:
            return None
        raise ReleasePrimitiveError(f"github_http_{error.code}") from None
    except ReleasePrimitiveError:
        raise
    except (OSError, ValueError):
        raise ReleasePrimitiveError("github_request_failed") from None


def _release_request(request: GitHubReleaseRequest) -> tuple[GitHubReleaseRequest, VersionTag]:
    _require(isinstance(request, GitHubReleaseRequest), "github_release_request_invalid")
    repository = _repository(request.repository)
    version_tag = derive_version_from_ref(request.tag)
    title = _single_line(request.title, code="github_release_title_invalid", maximum=256)
    _require(isinstance(request.notes, str) and len(request.notes) <= 125_000, "github_release_notes_invalid")
    _require("\x00" not in request.notes, "github_release_notes_invalid")
    target = _optional_single_line(
        request.target_commitish,
        code="github_release_target_invalid",
        maximum=255,
    )
    _require(type(request.draft) is bool, "github_release_flags_invalid")
    _require(type(request.prerelease) is bool, "github_release_flags_invalid")
    _require(type(request.generate_release_notes) is bool, "github_release_flags_invalid")
    return (
        GitHubReleaseRequest(
            repository=repository,
            tag=version_tag.tag,
            title=title,
            notes=request.notes,
            target_commitish=target,
            draft=request.draft,
            prerelease=request.prerelease,
            generate_release_notes=request.generate_release_notes,
        ),
        version_tag,
    )


def _release_result(
    payload: Mapping[str, Any],
    *,
    request: GitHubReleaseRequest,
    action: str,
) -> GitHubReleaseResult:
    release_id = payload.get("id")
    _require(type(release_id) is int and release_id > 0, "github_release_response_invalid")
    _require(payload.get("tag_name") == request.tag, "github_release_tag_mismatch")
    title = payload.get("name")
    _require(isinstance(title, str), "github_release_response_invalid")
    url = payload.get("html_url")
    upload_url = payload.get("upload_url")
    _require(isinstance(url, str) and url.startswith("https://github.com/"), "github_release_url_invalid")
    _require(isinstance(upload_url, str), "github_release_upload_url_invalid")
    upload_base = upload_url.split("{", 1)[0]
    parsed_upload = urllib.parse.urlsplit(upload_base)
    _require(
        parsed_upload.scheme == "https"
        and parsed_upload.hostname == "uploads.github.com"
        and parsed_upload.username is None
        and parsed_upload.password is None
        and parsed_upload.fragment == "",
        "github_release_upload_url_invalid",
    )
    return GitHubReleaseResult(
        action=action,
        release_id=release_id,
        repository=request.repository,
        tag=request.tag,
        title=title,
        url=url,
        upload_url=upload_base,
        draft=bool(payload.get("draft", False)),
        prerelease=bool(payload.get("prerelease", False)),
    )


def create_or_update_github_release(
    request: GitHubReleaseRequest,
    *,
    environment: Mapping[str, str] = os.environ,
    token_environment: str = "GITHUB_TOKEN",
    api_url: str | None = None,
    opener: Callable[..., Any] = urllib.request.urlopen,
) -> GitHubReleaseResult:
    """Create or update one GitHub Release using only a named token environment variable."""

    request, _version_tag = _release_request(request)
    token = _github_token(environment, token_environment)
    api = _github_api_url(environment, api_url)
    owner, name = request.repository.split("/", 1)
    repository_path = (
        f"/repos/{urllib.parse.quote(owner, safe='')}/"
        f"{urllib.parse.quote(name, safe='')}"
    )
    tag_path = urllib.parse.quote(request.tag, safe="")
    headers = _github_headers(token)
    existing = _github_json_request(
        "GET",
        f"{api}{repository_path}/releases/tags/{tag_path}",
        headers=headers,
        payload=None,
        opener=opener,
        missing_ok=True,
    )
    payload: dict[str, Any] = {
        "tag_name": request.tag,
        "name": request.title,
        "body": request.notes,
        "draft": request.draft,
        "prerelease": request.prerelease,
    }
    if request.target_commitish is not None:
        payload["target_commitish"] = request.target_commitish
    if existing is None:
        payload["generate_release_notes"] = request.generate_release_notes
        result = _github_json_request(
            "POST",
            f"{api}{repository_path}/releases",
            headers=headers,
            payload=payload,
            opener=opener,
        )
        _require(result is not None, "github_release_response_invalid")
        assert result is not None
        return _release_result(result, request=request, action="created")

    release_id = existing.get("id")
    _require(type(release_id) is int and release_id > 0, "github_release_response_invalid")
    result = _github_json_request(
        "PATCH",
        f"{api}{repository_path}/releases/{release_id}",
        headers=headers,
        payload=payload,
        opener=opener,
    )
    _require(result is not None, "github_release_response_invalid")
    assert result is not None
    return _release_result(result, request=request, action="updated")


def _asset_path(asset_path: Path | None) -> Path | None:
    if asset_path is None:
        return None
    path = Path(asset_path)
    _require(not path.is_symlink() and path.is_file(), "release_asset_path_invalid")
    try:
        resolved = path.resolve(strict=True)
    except OSError as error:
        raise ReleasePrimitiveError("release_asset_path_invalid") from error
    _require(resolved.is_file() and not resolved.is_symlink(), "release_asset_path_invalid")
    size = resolved.stat().st_size
    _require(0 <= size <= _MAX_ASSET_BYTES, "release_asset_size_invalid")
    return resolved


def _default_asset_uploader(
    url: str,
    headers: Mapping[str, str],
    path: Path,
    content_type: str,
) -> Mapping[str, Any]:
    parsed = urllib.parse.urlsplit(url)
    _require(
        parsed.scheme == "https"
        and parsed.hostname == "uploads.github.com"
        and parsed.username is None
        and parsed.password is None
        and parsed.fragment == "",
        "github_asset_url_invalid",
    )
    target = urllib.parse.urlunsplit(("", "", parsed.path, parsed.query, ""))
    connection = http.client.HTTPSConnection(parsed.hostname, timeout=120)
    try:
        connection.putrequest("POST", target)
        for name, value in headers.items():
            connection.putheader(name, value)
        connection.putheader("Content-Type", content_type)
        connection.putheader("Content-Length", str(path.stat().st_size))
        connection.endheaders()
        with path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                connection.send(chunk)
        response = connection.getresponse()
        status = int(response.status)
        _require(200 <= status < 300, f"github_http_{status}")
        payload = _read_json_body(response)
    except ReleasePrimitiveError:
        raise
    except OSError as error:
        raise ReleasePrimitiveError("github_asset_upload_failed") from error
    finally:
        connection.close()
    return payload


def upload_github_release_asset(
    release: GitHubReleaseResult,
    asset_path: Path | None,
    *,
    environment: Mapping[str, str] = os.environ,
    token_environment: str = "GITHUB_TOKEN",
    asset_name: str | None = None,
    content_type: str = "application/octet-stream",
    uploader: AssetUploader = _default_asset_uploader,
) -> GitHubAssetResult:
    """Optionally upload one caller-selected release asset with no temporary auth state."""

    _require(isinstance(release, GitHubReleaseResult), "github_release_result_invalid")
    path = _asset_path(asset_path)
    if path is None:
        return GitHubAssetResult(
            present=False,
            uploaded=False,
            name="",
            size=0,
            asset_id=None,
            url="",
        )
    name = asset_name if asset_name is not None else path.name
    _require(isinstance(name, str) and _ASSET_NAME.fullmatch(name) is not None, "release_asset_name_invalid")
    media_type = _single_line(content_type, code="release_asset_content_type_invalid", maximum=127)
    token = _github_token(environment, token_environment)
    base = release.upload_url.split("{", 1)[0]
    parsed = urllib.parse.urlsplit(base)
    _require(
        parsed.scheme == "https"
        and parsed.hostname == "uploads.github.com"
        and parsed.username is None
        and parsed.password is None
        and parsed.fragment == "",
        "github_release_upload_url_invalid",
    )
    query = urllib.parse.urlencode({"name": name})
    upload_url = urllib.parse.urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path, query, "")
    )
    payload = uploader(
        upload_url,
        _github_headers(token),
        path,
        media_type,
    )
    _require(isinstance(payload, Mapping), "github_asset_response_invalid")
    asset_id = payload.get("id")
    _require(type(asset_id) is int and asset_id > 0, "github_asset_response_invalid")
    _require(payload.get("name") == name, "github_asset_name_mismatch")
    size = payload.get("size")
    _require(type(size) is int and size == path.stat().st_size, "github_asset_size_mismatch")
    url = payload.get("browser_download_url")
    _require(isinstance(url, str) and url.startswith("https://github.com/"), "github_asset_url_invalid")
    return GitHubAssetResult(
        present=True,
        uploaded=True,
        name=name,
        size=size,
        asset_id=asset_id,
        url=url,
    )
