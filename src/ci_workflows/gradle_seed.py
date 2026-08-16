"""Trusted Gradle dependency-seed delta framing and OIDC upload client."""
from __future__ import annotations

import hashlib
import http.client
import json
import os
import re
import stat
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator, Mapping, Protocol
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

MAGIC = b"GRADLE-SEED-V1\n"
CONTENT_TYPE = "application/vnd.faruqi.gradle-seed-v1"
OIDC_AUDIENCE = "streamscapetv-gradle-seed-v1"
FLUX_HOST = "arc-gradle-seed-promoter.github-actions-runners.svc.cluster.local"
FLUX_PORT = 8080
FLUX_PATH = "/v1/gradle-seed"

EXPECTED_REPOSITORY = "StreamScapeTV/iptv-android"
EXPECTED_REPOSITORY_ID = "1310373430"
EXPECTED_REF = "refs/heads/develop"
EXPECTED_REF_TYPE = "branch"
EXPECTED_EVENT = "push"
EXPECTED_WORKFLOW_REF = (
    "StreamScapeTV/iptv-android/.github/workflows/android-ci.yml@refs/heads/develop"
)

MAX_HEADER_BYTES = 2048
MAX_FILE_BYTES = 512 * 1024 * 1024
MAX_FILE_COUNT = 50_000
MAX_UPLOAD_BYTES = 4 * 1024 * 1024 * 1024
MAX_RESPONSE_BYTES = 64 * 1024
READ_CHUNK_BYTES = 1024 * 1024
HTTP_TIMEOUT_SECONDS = 30

_SHA40 = re.compile(r"^[a-f0-9]{40}$")
_GENERATION = re.compile(r"^sha256-[a-f0-9]{64}$")
_MODULE_CACHE = re.compile(r"^modules-[A-Za-z0-9._-]+$")
_OIDC_HOST = re.compile(r"^(?:[A-Za-z0-9-]+\.)*actions\.githubusercontent\.com$")


class GradleSeedError(RuntimeError):
    """Fail-closed Gradle seed client error with a stable non-secret code."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class SeedFile:
    relative_path: str
    source_path: Path
    size: int
    sha256: str
    device: int
    inode: int
    mtime_ns: int

    def header_bytes(self) -> bytes:
        payload = json.dumps(
            {
                "path": self.relative_path,
                "sha256": self.sha256,
                "size": self.size,
            },
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii")
        if len(payload) > MAX_HEADER_BYTES:
            raise GradleSeedError("gradle_seed_header_too_large")
        return payload


@dataclass(frozen=True)
class UploadResponse:
    status: int
    content_type: str
    body: bytes


@dataclass(frozen=True)
class GradleSeedResult:
    source_sha: str
    generation: str
    file_count: int
    total_bytes: int
    evidence_id: str

    def output_values(self) -> dict[str, str]:
        return {
            "result": "promoted",
            "source_sha": self.source_sha,
            "generation": self.generation,
            "file_count": str(self.file_count),
            "total_bytes": str(self.total_bytes),
            "evidence_id": self.evidence_id,
            "cleanup_result": "clean",
        }


class OidcRequester(Protocol):
    def request_token(self, environment: Mapping[str, str]) -> str: ...


class SeedUploader(Protocol):
    def upload(
        self,
        *,
        token: str,
        source_sha: str,
        body: Iterable[bytes],
    ) -> UploadResponse: ...


def _require_exact_source_sha(value: str) -> str:
    if _SHA40.fullmatch(value) is None:
        raise GradleSeedError("gradle_seed_source_sha_invalid")
    return value


def _require_context(environment: Mapping[str, str], source_sha: str) -> None:
    expected = {
        "GITHUB_REPOSITORY": EXPECTED_REPOSITORY,
        "GITHUB_REPOSITORY_ID": EXPECTED_REPOSITORY_ID,
        "GITHUB_REF": EXPECTED_REF,
        "GITHUB_REF_TYPE": EXPECTED_REF_TYPE,
        "GITHUB_EVENT_NAME": EXPECTED_EVENT,
        "GITHUB_WORKFLOW_REF": EXPECTED_WORKFLOW_REF,
        "GITHUB_SHA": source_sha,
    }
    for name, value in expected.items():
        if environment.get(name, "") != value:
            raise GradleSeedError("gradle_seed_context_rejected")


def _safe_relative_path(parts: tuple[str, ...]) -> str:
    if not parts or _MODULE_CACHE.fullmatch(parts[0]) is None:
        raise GradleSeedError("gradle_seed_path_rejected")
    for part in parts:
        if not part or part in {".", ".."} or "\\" in part or "\x00" in part:
            raise GradleSeedError("gradle_seed_path_rejected")
    relative = "/".join(parts)
    if len(relative.encode("utf-8")) > 2048:
        raise GradleSeedError("gradle_seed_path_rejected")
    return relative


def _open_regular_nofollow(path: Path) -> tuple[int, os.stat_result]:
    flags = os.O_RDONLY
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except OSError as error:
        raise GradleSeedError("gradle_seed_file_rejected") from error
    try:
        current = os.fstat(fd)
        if not stat.S_ISREG(current.st_mode):
            raise GradleSeedError("gradle_seed_file_rejected")
        return fd, current
    except BaseException:
        os.close(fd)
        raise


def _hash_regular_file(path: Path, expected: os.stat_result) -> str:
    fd, current = _open_regular_nofollow(path)
    try:
        if (
            current.st_dev != expected.st_dev
            or current.st_ino != expected.st_ino
            or current.st_size != expected.st_size
            or current.st_mtime_ns != expected.st_mtime_ns
        ):
            raise GradleSeedError("gradle_seed_file_changed")
        digest = hashlib.sha256()
        total = 0
        while True:
            chunk = os.read(fd, READ_CHUNK_BYTES)
            if not chunk:
                break
            digest.update(chunk)
            total += len(chunk)
        final = os.fstat(fd)
        if (
            total != expected.st_size
            or final.st_dev != expected.st_dev
            or final.st_ino != expected.st_ino
            or final.st_size != expected.st_size
            or final.st_mtime_ns != expected.st_mtime_ns
        ):
            raise GradleSeedError("gradle_seed_file_changed")
        return digest.hexdigest()
    finally:
        os.close(fd)


def collect_seed_files(gradle_user_home: Path) -> tuple[SeedFile, ...]:
    """Collect the deterministic writable ``modules-*`` delta without following links."""

    home = gradle_user_home
    if not home.is_absolute():
        raise GradleSeedError("gradle_seed_home_rejected")
    try:
        home_stat = os.lstat(home)
    except OSError as error:
        raise GradleSeedError("gradle_seed_home_rejected") from error
    if not stat.S_ISDIR(home_stat.st_mode) or stat.S_ISLNK(home_stat.st_mode):
        raise GradleSeedError("gradle_seed_home_rejected")

    caches = home / "caches"
    try:
        caches_stat = os.lstat(caches)
    except OSError as error:
        raise GradleSeedError("gradle_seed_cache_missing") from error
    if not stat.S_ISDIR(caches_stat.st_mode) or stat.S_ISLNK(caches_stat.st_mode):
        raise GradleSeedError("gradle_seed_cache_rejected")

    module_roots: list[Path] = []
    try:
        top_entries = sorted(os.scandir(caches), key=lambda entry: entry.name)
    except OSError as error:
        raise GradleSeedError("gradle_seed_cache_rejected") from error
    for entry in top_entries:
        if _MODULE_CACHE.fullmatch(entry.name) is None:
            continue
        try:
            entry_stat = entry.stat(follow_symlinks=False)
        except OSError as error:
            raise GradleSeedError("gradle_seed_path_rejected") from error
        if stat.S_ISLNK(entry_stat.st_mode) or not stat.S_ISDIR(entry_stat.st_mode):
            raise GradleSeedError("gradle_seed_path_rejected")
        module_roots.append(Path(entry.path))

    if not module_roots:
        raise GradleSeedError("gradle_seed_delta_empty")

    selected: list[SeedFile] = []
    total_bytes = 0

    def visit(directory: Path, prefix: tuple[str, ...]) -> None:
        nonlocal total_bytes
        try:
            entries = sorted(os.scandir(directory), key=lambda entry: entry.name)
        except OSError as error:
            raise GradleSeedError("gradle_seed_path_rejected") from error
        for entry in entries:
            parts = (*prefix, entry.name)
            relative = _safe_relative_path(parts)
            try:
                entry_stat = entry.stat(follow_symlinks=False)
            except OSError as error:
                raise GradleSeedError("gradle_seed_path_rejected") from error
            if stat.S_ISLNK(entry_stat.st_mode):
                raise GradleSeedError("gradle_seed_symlink_rejected")
            if stat.S_ISDIR(entry_stat.st_mode):
                visit(Path(entry.path), parts)
                continue
            if not stat.S_ISREG(entry_stat.st_mode):
                raise GradleSeedError("gradle_seed_entry_unsupported")
            if entry.name == "gc.properties" or entry.name.endswith(".lock"):
                continue
            if entry_stat.st_size > MAX_FILE_BYTES:
                raise GradleSeedError("gradle_seed_file_too_large")
            if len(selected) >= MAX_FILE_COUNT:
                raise GradleSeedError("gradle_seed_file_count_exceeded")
            total_bytes += entry_stat.st_size
            if total_bytes > MAX_UPLOAD_BYTES:
                raise GradleSeedError("gradle_seed_payload_too_large")
            sha256 = _hash_regular_file(Path(entry.path), entry_stat)
            selected.append(
                SeedFile(
                    relative_path=relative,
                    source_path=Path(entry.path),
                    size=entry_stat.st_size,
                    sha256=sha256,
                    device=entry_stat.st_dev,
                    inode=entry_stat.st_ino,
                    mtime_ns=entry_stat.st_mtime_ns,
                )
            )

    for module_root in module_roots:
        visit(module_root, (module_root.name,))

    if not selected:
        raise GradleSeedError("gradle_seed_delta_empty")
    return tuple(selected)


def _open_selected_file(seed_file: SeedFile) -> int:
    fd, current = _open_regular_nofollow(seed_file.source_path)
    if (
        current.st_dev != seed_file.device
        or current.st_ino != seed_file.inode
        or current.st_size != seed_file.size
        or current.st_mtime_ns != seed_file.mtime_ns
    ):
        os.close(fd)
        raise GradleSeedError("gradle_seed_file_changed")
    return fd


def framed_seed_stream(files: tuple[SeedFile, ...]) -> Iterator[bytes]:
    """Yield the upload protocol without materializing an archive or candidate copy."""

    yield MAGIC
    for seed_file in files:
        header = seed_file.header_bytes()
        yield struct.pack(">I", len(header))
        yield header

        fd = _open_selected_file(seed_file)
        digest = hashlib.sha256()
        total = 0
        try:
            while True:
                chunk = os.read(fd, READ_CHUNK_BYTES)
                if not chunk:
                    break
                total += len(chunk)
                digest.update(chunk)
                yield chunk
            final = os.fstat(fd)
        finally:
            os.close(fd)
        if (
            total != seed_file.size
            or digest.hexdigest() != seed_file.sha256
            or final.st_dev != seed_file.device
            or final.st_ino != seed_file.inode
            or final.st_size != seed_file.size
            or final.st_mtime_ns != seed_file.mtime_ns
        ):
            raise GradleSeedError("gradle_seed_file_changed")
    yield struct.pack(">I", 0)


def _manifest_sha256(files: tuple[SeedFile, ...]) -> str:
    digest = hashlib.sha256()
    for seed_file in files:
        header = seed_file.header_bytes()
        digest.update(struct.pack(">I", len(header)))
        digest.update(header)
    return digest.hexdigest()


class GithubOidcRequester:
    """Request only the short-lived GitHub OIDC token for the reviewed audience."""

    def __init__(self, *, timeout_seconds: int = HTTP_TIMEOUT_SECONDS) -> None:
        self.timeout_seconds = timeout_seconds

    @staticmethod
    def _request_target(raw_url: str) -> tuple[str, int, str]:
        try:
            parsed = urlsplit(raw_url)
            port = parsed.port
        except ValueError as error:
            raise GradleSeedError("gradle_seed_oidc_url_rejected") from error
        hostname = parsed.hostname or ""
        if (
            parsed.scheme != "https"
            or not hostname
            or _OIDC_HOST.fullmatch(hostname) is None
            or parsed.username is not None
            or parsed.password is not None
            or parsed.fragment
            or port not in {None, 443}
        ):
            raise GradleSeedError("gradle_seed_oidc_url_rejected")
        query = [
            (key, value)
            for key, value in parse_qsl(parsed.query, keep_blank_values=True)
            if key != "audience"
        ]
        query.append(("audience", OIDC_AUDIENCE))
        target = urlunsplit(("", "", parsed.path or "/", urlencode(query), ""))
        return hostname, port or 443, target

    def request_token(self, environment: Mapping[str, str]) -> str:
        raw_url = environment.get("ACTIONS_ID_TOKEN_REQUEST_URL", "")
        capability = environment.get("ACTIONS_ID_TOKEN_REQUEST_TOKEN", "")
        if not raw_url or not capability:
            raise GradleSeedError("gradle_seed_oidc_capability_missing")
        host, port, target = self._request_target(raw_url)
        connection = http.client.HTTPSConnection(
            host,
            port,
            timeout=self.timeout_seconds,
        )
        try:
            connection.request(
                "GET",
                target,
                headers={
                    "Accept": "application/json",
                    "Authorization": f"Bearer {capability}",
                },
            )
            response = connection.getresponse()
            body = response.read(MAX_RESPONSE_BYTES + 1)
            if len(body) > MAX_RESPONSE_BYTES:
                raise GradleSeedError("gradle_seed_oidc_response_too_large")
            if response.status != 200:
                raise GradleSeedError("gradle_seed_oidc_request_failed")
            content_type = response.getheader("content-type", "")
            if content_type.split(";", 1)[0].strip().lower() != "application/json":
                raise GradleSeedError("gradle_seed_oidc_response_invalid")
            try:
                payload = json.loads(body.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise GradleSeedError("gradle_seed_oidc_response_invalid") from error
            token = payload.get("value") if isinstance(payload, dict) else None
            if not isinstance(token, str) or token.count(".") != 2 or len(token) > 16_384:
                raise GradleSeedError("gradle_seed_oidc_response_invalid")
            return token
        except GradleSeedError:
            raise
        except (OSError, http.client.HTTPException) as error:
            raise GradleSeedError("gradle_seed_oidc_request_failed") from error
        finally:
            connection.close()


class FluxSeedUploader:
    """Stream one complete framed delta to the fixed internal Flux promoter."""

    def __init__(self, *, timeout_seconds: int = HTTP_TIMEOUT_SECONDS) -> None:
        self.timeout_seconds = timeout_seconds

    def upload(
        self,
        *,
        token: str,
        source_sha: str,
        body: Iterable[bytes],
    ) -> UploadResponse:
        connection = http.client.HTTPConnection(
            FLUX_HOST,
            FLUX_PORT,
            timeout=self.timeout_seconds,
        )
        try:
            connection.request(
                "POST",
                FLUX_PATH,
                body=body,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": CONTENT_TYPE,
                    "X-Gradle-Source-Sha": source_sha,
                },
                encode_chunked=True,
            )
            response = connection.getresponse()
            response_body = response.read(MAX_RESPONSE_BYTES + 1)
            if len(response_body) > MAX_RESPONSE_BYTES:
                raise GradleSeedError("gradle_seed_response_too_large")
            return UploadResponse(
                status=response.status,
                content_type=response.getheader("content-type", ""),
                body=response_body,
            )
        except GradleSeedError:
            raise
        except (OSError, http.client.HTTPException) as error:
            raise GradleSeedError("gradle_seed_upload_failed") from error
        finally:
            connection.close()


def _validated_response(
    response: UploadResponse,
    *,
    source_sha: str,
    files: tuple[SeedFile, ...],
) -> tuple[str, int]:
    if response.status != 200:
        raise GradleSeedError("gradle_seed_promotion_rejected")
    if response.content_type.split(";", 1)[0].strip().lower() != "application/json":
        raise GradleSeedError("gradle_seed_response_invalid")
    try:
        payload = json.loads(response.body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise GradleSeedError("gradle_seed_response_invalid") from error
    if not isinstance(payload, dict) or payload.get("status") != "promoted":
        raise GradleSeedError("gradle_seed_response_invalid")
    if payload.get("sourceSha") != source_sha:
        raise GradleSeedError("gradle_seed_response_source_mismatch")
    generation = payload.get("generation")
    if not isinstance(generation, str) or _GENERATION.fullmatch(generation) is None:
        raise GradleSeedError("gradle_seed_response_generation_invalid")
    expected_bytes = sum(item.size for item in files)
    if payload.get("fileCount") != len(files) or payload.get("totalBytes") != expected_bytes:
        raise GradleSeedError("gradle_seed_response_counts_mismatch")
    return generation, expected_bytes


def promote_gradle_seed(
    *,
    source_sha: str,
    environment: Mapping[str, str],
    oidc_requester: OidcRequester | None = None,
    uploader: SeedUploader | None = None,
) -> GradleSeedResult:
    """Validate, frame, upload, and verify one protected Android Gradle delta."""

    source = _require_exact_source_sha(source_sha)
    _require_context(environment, source)
    raw_home = environment.get("GRADLE_USER_HOME", "")
    if not raw_home:
        raise GradleSeedError("gradle_seed_home_required")
    files = collect_seed_files(Path(raw_home))
    manifest_sha256 = _manifest_sha256(files)
    requester = oidc_requester or GithubOidcRequester()
    transport = uploader or FluxSeedUploader()

    token = ""
    try:
        try:
            token = requester.request_token(environment)
        except GradleSeedError:
            raise
        except Exception as error:
            raise GradleSeedError("gradle_seed_oidc_request_failed") from error
        if not token:
            raise GradleSeedError("gradle_seed_oidc_response_invalid")
        try:
            response = transport.upload(
                token=token,
                source_sha=source,
                body=framed_seed_stream(files),
            )
        except GradleSeedError:
            raise
        except Exception as error:
            raise GradleSeedError("gradle_seed_upload_failed") from error
    finally:
        token = ""

    generation, total_bytes = _validated_response(
        response,
        source_sha=source,
        files=files,
    )
    evidence_payload = json.dumps(
        {
            "fileCount": len(files),
            "generation": generation,
            "manifestSha256": manifest_sha256,
            "protocol": "gradle-seed-v1",
            "sourceSha": source,
            "totalBytes": total_bytes,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")
    evidence_id = hashlib.sha256(evidence_payload).hexdigest()
    return GradleSeedResult(
        source_sha=source,
        generation=generation,
        file_count=len(files),
        total_bytes=total_bytes,
        evidence_id=evidence_id,
    )
