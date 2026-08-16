"""Product-neutral HTTP, verified download, and safe archive extraction primitives."""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import tarfile
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Mapping, Protocol, Sequence

from .runtime_primitives import (
    RuntimePrimitiveError,
    create_temporary_workspace,
    finalize_temporary_path,
    secret_environment,
)

_HTTP_SECRET_ENVIRONMENT = "CI_HTTP_AUTH_HEADER_VALUE"
_HEADER_NAME = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]{1,128}$")
_SHA256 = re.compile(r"^[0-9a-fA-F]{64}$")
_REDIRECT_STATUSES = {301, 302, 303, 307, 308}
_RETRY_STATUSES = {408, 425, 429, 500, 502, 503, 504}
_FORBIDDEN_DIRECT_HEADERS = {
    "authorization",
    "proxy-authorization",
    "cookie",
    "set-cookie",
}
_FORBIDDEN_MANAGED_HEADERS = {
    "host",
    "content-length",
    "transfer-encoding",
    "connection",
    "proxy-connection",
    "upgrade",
}
_JSON_TYPES = {"application/json", "text/json"}


class NetworkPrimitiveError(RuntimeError):
    """Fail closed with one stable non-secret network primitive code."""

    def __init__(self, code: str, *, cleanup_failed: bool = False) -> None:
        if re.fullmatch(r"[a-z][a-z0-9_]{2,95}", code) is None:
            raise ValueError("network primitive error code must be a safe identifier")
        self.code = code
        self.cleanup_failed = cleanup_failed
        super().__init__(code)


class NetworkTransportError(RuntimeError):
    """Sanitized injected/default transport failure."""

    def __init__(self) -> None:
        super().__init__("network_transport_failed")


class ResponseStream(Protocol):
    status: int
    url: str
    headers: Mapping[str, str]

    def read(self, size: int = -1) -> bytes: ...

    def close(self) -> None: ...


class HttpTransport(Protocol):
    def open(
        self,
        method: str,
        url: str,
        headers: Mapping[str, str],
        timeout_seconds: float,
    ) -> ResponseStream: ...


@dataclass(frozen=True, slots=True)
class HttpResult:
    method: str
    requested_url: str
    final_url: str
    status: int
    attempts: int
    redirects: int
    content_type: str | None
    content_length: int | None
    etag: str | None
    last_modified: str | None
    body_size: int
    body_sha256: str
    body: bytes = field(repr=False)


@dataclass(frozen=True, slots=True)
class JsonResult:
    response: HttpResult
    value: Any = field(repr=False)


@dataclass(frozen=True, slots=True)
class FileVerification:
    path: str
    size: int
    sha256: str


@dataclass(frozen=True, slots=True)
class DownloadResult:
    requested_url: str
    final_url: str
    status: int
    attempts: int
    redirects: int
    relative_path: str
    size: int
    sha256: str
    content_type: str | None


@dataclass(frozen=True, slots=True)
class ExtractionResult:
    archive_format: str
    destination: str
    file_count: int
    directory_count: int
    total_bytes: int
    archive_sha256: str
    manifest_sha256: str


@dataclass(frozen=True, slots=True)
class _Opened:
    stream: ResponseStream
    requested_url: str
    final_url: str
    status: int
    attempts: int
    redirects: int


@dataclass(frozen=True, slots=True)
class _Member:
    path: PurePosixPath
    size: int
    is_directory: bool
    executable: bool
    source: object


class _UrllibStream:
    def __init__(self, response: Any) -> None:
        self._response = response
        self.status = int(getattr(response, "status", response.getcode()))
        self.url = str(response.geturl())
        self.headers = {str(key): str(value) for key, value in response.headers.items()}

    def read(self, size: int = -1) -> bytes:
        value = self._response.read(size)
        if not isinstance(value, bytes):
            raise NetworkTransportError()
        return value

    def close(self) -> None:
        self._response.close()


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        return None


class UrllibTransport:
    """Default redirect-disabled urllib transport."""

    def __init__(self) -> None:
        self._opener = urllib.request.build_opener(_NoRedirect())

    def open(
        self,
        method: str,
        url: str,
        headers: Mapping[str, str],
        timeout_seconds: float,
    ) -> ResponseStream:
        request = urllib.request.Request(
            url=url,
            headers=dict(headers),
            method=method,
        )
        try:
            response = self._opener.open(request, timeout=timeout_seconds)
        except urllib.error.HTTPError as error:
            response = error
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            raise NetworkTransportError() from error
        return _UrllibStream(response)


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise NetworkPrimitiveError(code)


def _bounded_number(
    value: int | float,
    *,
    minimum: float,
    maximum: float,
    code: str,
) -> float:
    _require(
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and minimum <= float(value) <= maximum,
        code,
    )
    return float(value)


def _bounded_integer(value: int, *, minimum: int, maximum: int, code: str) -> int:
    _require(
        isinstance(value, int)
        and not isinstance(value, bool)
        and minimum <= value <= maximum,
        code,
    )
    return value


def _canonical_url(url: str, *, allow_http: bool) -> str:
    _require(
        isinstance(url, str)
        and 1 <= len(url) <= 8192
        and all(ord(character) >= 32 and character != "\x7f" for character in url),
        "http_url_invalid",
    )
    try:
        parsed = urllib.parse.urlsplit(url)
        _ = parsed.port
    except ValueError as error:
        raise NetworkPrimitiveError("http_url_invalid") from error
    scheme = parsed.scheme.lower()
    _require(scheme in {"https", "http"}, "http_url_scheme_invalid")
    if scheme == "http":
        _require(allow_http, "http_insecure_scheme_rejected")
    _require(bool(parsed.hostname), "http_url_invalid")
    _require(parsed.username is None and parsed.password is None, "http_url_credentials_rejected")
    _require(not parsed.fragment, "http_url_fragment_rejected")
    netloc = parsed.netloc
    canonical = urllib.parse.urlunsplit((scheme, netloc, parsed.path or "/", parsed.query, ""))
    return canonical


def _origin(url: str) -> tuple[str, str, int]:
    parsed = urllib.parse.urlsplit(url)
    assert parsed.hostname is not None
    port = parsed.port
    if port is None:
        port = 443 if parsed.scheme.lower() == "https" else 80
    return parsed.scheme.lower(), parsed.hostname.lower().rstrip("."), port


def _validate_header(name: str, value: str, *, secret: bool) -> tuple[str, str]:
    _require(
        isinstance(name, str)
        and _HEADER_NAME.fullmatch(name) is not None
        and isinstance(value, str)
        and len(value) <= 4096
        and all(ord(character) >= 32 and character != "\x7f" for character in value),
        "http_header_invalid",
    )
    lowered = name.lower()
    _require(lowered not in _FORBIDDEN_MANAGED_HEADERS, "http_header_forbidden")
    if not secret:
        secret_shaped = lowered in _FORBIDDEN_DIRECT_HEADERS or any(
            marker in lowered for marker in ("authorization", "cookie", "token", "api-key", "apikey", "secret")
        )
        _require(not secret_shaped, "http_secret_header_requires_environment")
    return name, value


def _request_headers(
    headers: Mapping[str, str] | None,
    *,
    environment: Mapping[str, str],
    secret_header_name: str | None,
) -> tuple[dict[str, str], str | None]:
    _require(headers is None or isinstance(headers, Mapping), "http_headers_invalid")
    values: dict[str, str] = {}
    seen: set[str] = set()
    total = 0
    for raw_name, raw_value in (headers or {}).items():
        name, value = _validate_header(raw_name, raw_value, secret=False)
        lowered = name.lower()
        _require(lowered not in seen, "http_header_duplicate")
        seen.add(lowered)
        total += len(name) + len(value)
        _require(len(seen) <= 64 and total <= 16384, "http_headers_too_large")
        values[name] = value

    secret_key: str | None = None
    if secret_header_name is not None:
        _require(isinstance(secret_header_name, str), "http_secret_header_invalid")
        try:
            secret_value = secret_environment(
                _HTTP_SECRET_ENVIRONMENT,
                environment=environment,
                required=True,
            )
        except RuntimePrimitiveError as error:
            raise NetworkPrimitiveError("http_secret_required") from error
        name, secret_value = _validate_header(
            secret_header_name,
            secret_value,
            secret=True,
        )
        secret_key = name.lower()
        _require(secret_key not in seen, "http_header_duplicate")
        values[name] = secret_value
    return values, secret_key


def _header(headers: Mapping[str, str], name: str) -> str | None:
    _require(isinstance(headers, Mapping), "http_response_metadata_invalid")
    wanted = name.lower()
    matches: list[str] = []
    for key, value in headers.items():
        _require(
            isinstance(key, str)
            and isinstance(value, str)
            and len(value) <= 4096
            and all(ord(character) >= 32 and character != "\x7f" for character in value),
            "http_response_metadata_invalid",
        )
        if key.lower() == wanted:
            matches.append(value)
    _require(len(matches) <= 1, "http_response_metadata_invalid")
    return matches[0] if matches else None

def _content_type(headers: Mapping[str, str]) -> str | None:
    value = _header(headers, "content-type")
    if value is None:
        return None
    media_type = value.split(";", 1)[0].strip().lower()
    _require(bool(media_type) and len(media_type) <= 255, "http_response_metadata_invalid")
    return media_type


def _content_length(headers: Mapping[str, str]) -> int | None:
    value = _header(headers, "content-length")
    if value is None:
        return None
    _require(value.isdigit(), "http_response_metadata_invalid")
    length = int(value)
    _require(length >= 0, "http_response_metadata_invalid")
    return length


def _safe_metadata(headers: Mapping[str, str], name: str) -> str | None:
    value = _header(headers, name)
    if value is None:
        return None
    _require(len(value) <= 1024, "http_response_metadata_invalid")
    return value


def _retry_delay(attempt: int, *, initial: float, maximum: float) -> float:
    return min(maximum, initial * (2 ** max(0, attempt - 1)))


def _sleep(sleeper: Callable[[float], None], delay: float) -> None:
    if delay <= 0:
        return
    try:
        sleeper(delay)
    except Exception as error:
        raise NetworkPrimitiveError("http_retry_sleep_failed") from error


def _close_response(stream: ResponseStream) -> None:
    try:
        stream.close()
    except Exception as error:
        raise NetworkPrimitiveError("http_response_close_failed") from error


def _open_with_policy(
    method: str,
    url: str,
    *,
    transport: HttpTransport,
    headers: Mapping[str, str] | None,
    environment: Mapping[str, str],
    secret_header_name: str | None,
    timeout_seconds: float,
    max_attempts: int,
    initial_backoff_seconds: float,
    maximum_backoff_seconds: float,
    maximum_redirects: int,
    allow_http: bool,
    allow_cross_origin_redirects: bool,
    sleeper: Callable[[float], None],
) -> _Opened:
    _require(method in {"GET", "HEAD"}, "http_method_invalid")
    _require(isinstance(allow_http, bool), "http_allow_http_invalid")
    _require(
        isinstance(allow_cross_origin_redirects, bool),
        "http_cross_origin_policy_invalid",
    )
    timeout = _bounded_number(
        timeout_seconds,
        minimum=0.1,
        maximum=300.0,
        code="http_timeout_invalid",
    )
    attempts_per_url = _bounded_integer(
        max_attempts,
        minimum=1,
        maximum=5,
        code="http_retry_invalid",
    )
    initial_backoff = _bounded_number(
        initial_backoff_seconds,
        minimum=0.0,
        maximum=30.0,
        code="http_retry_invalid",
    )
    maximum_backoff = _bounded_number(
        maximum_backoff_seconds,
        minimum=0.0,
        maximum=60.0,
        code="http_retry_invalid",
    )
    _require(maximum_backoff >= initial_backoff, "http_retry_invalid")
    redirect_limit = _bounded_integer(
        maximum_redirects,
        minimum=0,
        maximum=10,
        code="http_redirect_limit_invalid",
    )

    requested = _canonical_url(url, allow_http=allow_http)
    current = requested
    current_headers, secret_key = _request_headers(
        headers,
        environment=environment,
        secret_header_name=secret_header_name,
    )
    attempts = 0
    redirects = 0

    while True:
        response: ResponseStream | None = None
        for attempt in range(1, attempts_per_url + 1):
            attempts += 1
            try:
                response = transport.open(method, current, current_headers, timeout)
            except (NetworkTransportError, TimeoutError, OSError) as error:
                if attempt == attempts_per_url:
                    raise NetworkPrimitiveError("http_transport_failed") from error
                _sleep(
                    sleeper,
                    _retry_delay(
                        attempt,
                        initial=initial_backoff,
                        maximum=maximum_backoff,
                    ),
                )
                continue

            if not isinstance(response.headers, Mapping):
                try:
                    _close_response(response)
                finally:
                    response = None
                raise NetworkPrimitiveError("http_response_metadata_invalid")
            if not (
                isinstance(response.status, int)
                and not isinstance(response.status, bool)
                and 100 <= response.status <= 599
            ):
                try:
                    _close_response(response)
                finally:
                    response = None
                raise NetworkPrimitiveError("http_status_invalid")
            if response.status in _RETRY_STATUSES and attempt < attempts_per_url:
                try:
                    _close_response(response)
                finally:
                    response = None
                _sleep(
                    sleeper,
                    _retry_delay(
                        attempt,
                        initial=initial_backoff,
                        maximum=maximum_backoff,
                    ),
                )
                continue
            break

        assert response is not None
        status = response.status
        if status in _REDIRECT_STATUSES:
            try:
                location = _header(response.headers, "location")
            finally:
                try:
                    _close_response(response)
                finally:
                    response = None
            _require(location is not None, "http_redirect_invalid")
            _require(redirects < redirect_limit, "http_redirect_limit")
            next_url = _canonical_url(
                urllib.parse.urljoin(current, location),
                allow_http=allow_http,
            )
            old_origin = _origin(current)
            new_origin = _origin(next_url)
            _require(
                not (old_origin[0] == "https" and new_origin[0] == "http"),
                "http_redirect_downgrade_rejected",
            )
            cross_origin = new_origin != old_origin
            _require(
                not cross_origin or allow_cross_origin_redirects,
                "http_cross_origin_redirect_rejected",
            )
            if cross_origin and secret_key is not None:
                current_headers = {
                    name: value
                    for name, value in current_headers.items()
                    if name.lower() != secret_key
                }
                secret_key = None
            redirects += 1
            current = next_url
            continue

        if not (200 <= status <= 299):
            try:
                _close_response(response)
            finally:
                response = None
            raise NetworkPrimitiveError("http_status_rejected")

        return _Opened(
            stream=response,
            requested_url=requested,
            final_url=current,
            status=status,
            attempts=attempts,
            redirects=redirects,
        )


def _read_bounded(stream: ResponseStream, *, maximum_bytes: int) -> bytes:
    maximum = _bounded_integer(
        maximum_bytes,
        minimum=0,
        maximum=1024 * 1024 * 1024,
        code="http_response_limit_invalid",
    )
    declared = _content_length(stream.headers)
    if declared is not None:
        _require(declared <= maximum, "http_response_too_large")
    chunks: list[bytes] = []
    total = 0
    while True:
        try:
            chunk = stream.read(64 * 1024)
        except Exception as error:
            raise NetworkPrimitiveError("http_response_read_failed") from error
        _require(isinstance(chunk, bytes), "http_response_read_failed")
        if not chunk:
            break
        total += len(chunk)
        _require(total <= maximum, "http_response_too_large")
        chunks.append(chunk)
    if declared is not None:
        _require(total == declared, "http_response_length_mismatch")
    return b"".join(chunks)


def request_http(
    method: str,
    url: str,
    *,
    transport: HttpTransport | None = None,
    headers: Mapping[str, str] | None = None,
    environment: Mapping[str, str] | None = None,
    secret_header_name: str | None = None,
    timeout_seconds: float = 30.0,
    max_attempts: int = 3,
    initial_backoff_seconds: float = 0.25,
    maximum_backoff_seconds: float = 2.0,
    maximum_redirects: int = 5,
    maximum_response_bytes: int = 8 * 1024 * 1024,
    allow_http: bool = False,
    allow_cross_origin_redirects: bool = False,
    sleeper: Callable[[float], None] = time.sleep,
) -> HttpResult:
    """Run one bounded GET/HEAD request and return sanitized structured metadata."""

    opened = _open_with_policy(
        method,
        url,
        transport=transport or UrllibTransport(),
        headers=headers,
        environment=environment or {},
        secret_header_name=secret_header_name,
        timeout_seconds=timeout_seconds,
        max_attempts=max_attempts,
        initial_backoff_seconds=initial_backoff_seconds,
        maximum_backoff_seconds=maximum_backoff_seconds,
        maximum_redirects=maximum_redirects,
        allow_http=allow_http,
        allow_cross_origin_redirects=allow_cross_origin_redirects,
        sleeper=sleeper,
    )
    stream = opened.stream
    body = b""
    try:
        content_type = _content_type(stream.headers)
        content_length = _content_length(stream.headers)
        etag = _safe_metadata(stream.headers, "etag")
        last_modified = _safe_metadata(stream.headers, "last-modified")
        if method == "GET":
            body = _read_bounded(stream, maximum_bytes=maximum_response_bytes)
        else:
            _bounded_integer(
                maximum_response_bytes,
                minimum=0,
                maximum=1024 * 1024 * 1024,
                code="http_response_limit_invalid",
            )
    finally:
        try:
            stream.close()
        except Exception as error:
            raise NetworkPrimitiveError("http_response_close_failed") from error

    return HttpResult(
        method=method,
        requested_url=opened.requested_url,
        final_url=opened.final_url,
        status=opened.status,
        attempts=opened.attempts,
        redirects=opened.redirects,
        content_type=content_type,
        content_length=content_length,
        etag=etag,
        last_modified=last_modified,
        body_size=len(body),
        body_sha256=hashlib.sha256(body).hexdigest(),
        body=body,
    )


def http_get(url: str, **kwargs: Any) -> HttpResult:
    return request_http("GET", url, **kwargs)


def http_head(url: str, **kwargs: Any) -> HttpResult:
    return request_http("HEAD", url, **kwargs)


def http_json(
    url: str,
    *,
    require_json_content_type: bool = True,
    **kwargs: Any,
) -> JsonResult:
    response = request_http("GET", url, **kwargs)
    if require_json_content_type:
        media_type = response.content_type
        _require(
            media_type in _JSON_TYPES
            or (isinstance(media_type, str) and media_type.endswith("+json")),
            "http_json_content_type_invalid",
        )
    try:
        value = json.loads(response.body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise NetworkPrimitiveError("http_json_invalid") from error
    return JsonResult(response=response, value=value)


def _real_directory(path: Path, code: str) -> Path:
    candidate = Path(path)
    _require(candidate.is_absolute() and not candidate.is_symlink(), code)
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as error:
        raise NetworkPrimitiveError(code) from error
    _require(resolved.is_dir(), code)
    return resolved


def _bounded_relative_target(root: Path, relative_path: str, *, code: str) -> Path:
    boundary = _real_directory(root, code)
    _require(
        isinstance(relative_path, str)
        and 1 <= len(relative_path) <= 4096
        and not relative_path.startswith("/")
        and "\\" not in relative_path
        and "\x00" not in relative_path,
        code,
    )
    pure = PurePosixPath(relative_path)
    _require(
        pure.parts
        and ".." not in pure.parts
        and "." not in pure.parts
        and all(part not in {"", ".", ".."} for part in pure.parts),
        code,
    )
    target = boundary.joinpath(*pure.parts)
    parent = boundary
    for part in pure.parts[:-1]:
        parent = parent / part
        _require(parent.exists() and parent.is_dir() and not parent.is_symlink(), code)
    _require(target.parent.resolve(strict=True) == parent.resolve(strict=True), code)
    return target


def _verify_sha256(value: str | None) -> str | None:
    if value is None:
        return None
    _require(isinstance(value, str) and _SHA256.fullmatch(value) is not None, "checksum_invalid")
    return value.lower()


def _verify_expected_size(value: int | None) -> int | None:
    if value is None:
        return None
    return _bounded_integer(
        value,
        minimum=0,
        maximum=8 * 1024 * 1024 * 1024,
        code="expected_size_invalid",
    )


def verify_file(
    path: Path,
    *,
    expected_sha256: str | None = None,
    expected_size: int | None = None,
) -> FileVerification:
    """Verify one existing regular file without following a symlink."""

    candidate = Path(path)
    _require(candidate.is_absolute() and not candidate.is_symlink(), "verification_path_invalid")
    try:
        metadata = candidate.stat(follow_symlinks=False)
    except OSError as error:
        raise NetworkPrimitiveError("verification_path_invalid") from error
    _require(stat.S_ISREG(metadata.st_mode), "verification_path_invalid")
    expected_hash = _verify_sha256(expected_sha256)
    expected_bytes = _verify_expected_size(expected_size)
    if expected_bytes is not None:
        _require(metadata.st_size == expected_bytes, "size_mismatch")

    digest = hashlib.sha256()
    total = 0
    try:
        with candidate.open("rb") as handle:
            while True:
                chunk = handle.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                digest.update(chunk)
    except OSError as error:
        raise NetworkPrimitiveError("verification_read_failed") from error
    _require(total == metadata.st_size, "size_mismatch")
    actual = digest.hexdigest()
    if expected_hash is not None:
        _require(actual == expected_hash, "checksum_mismatch")
    return FileVerification(path=str(candidate), size=total, sha256=actual)


def _partial_file(target: Path) -> tuple[int, Path]:
    prefix = f".{target.name}.ciw-"
    try:
        fd, raw = tempfile.mkstemp(prefix=prefix, suffix=".partial", dir=target.parent)
    except OSError as error:
        raise NetworkPrimitiveError("download_partial_create_failed") from error
    partial = Path(raw)
    try:
        _require(
            partial.parent == target.parent
            and not partial.is_symlink()
            and stat.S_ISREG(os.fstat(fd).st_mode),
            "download_partial_create_failed",
        )
        os.fchmod(fd, 0o600)
    except Exception:
        os.close(fd)
        partial.unlink(missing_ok=True)
        raise
    return fd, partial


def download_file(
    url: str,
    *,
    destination_root: Path,
    relative_path: str,
    transport: HttpTransport | None = None,
    headers: Mapping[str, str] | None = None,
    environment: Mapping[str, str] | None = None,
    secret_header_name: str | None = None,
    expected_sha256: str | None = None,
    expected_size: int | None = None,
    expected_content_type: str | None = None,
    maximum_bytes: int = 512 * 1024 * 1024,
    timeout_seconds: float = 60.0,
    max_attempts: int = 3,
    initial_backoff_seconds: float = 0.25,
    maximum_backoff_seconds: float = 2.0,
    maximum_redirects: int = 5,
    allow_http: bool = False,
    allow_cross_origin_redirects: bool = False,
    sleeper: Callable[[float], None] = time.sleep,
) -> DownloadResult:
    """Stream one HTTP object into an exclusive partial file and atomically finalize it."""

    root = _real_directory(destination_root, "download_root_invalid")
    target = _bounded_relative_target(root, relative_path, code="download_path_invalid")
    _require(not target.exists() and not target.is_symlink(), "download_target_exists")
    expected_hash = _verify_sha256(expected_sha256)
    expected_bytes = _verify_expected_size(expected_size)
    maximum = _bounded_integer(
        maximum_bytes,
        minimum=1,
        maximum=8 * 1024 * 1024 * 1024,
        code="download_limit_invalid",
    )
    if expected_bytes is not None:
        _require(expected_bytes <= maximum, "download_limit_invalid")
    if expected_content_type is not None:
        _require(
            isinstance(expected_content_type, str)
            and 1 <= len(expected_content_type) <= 255
            and expected_content_type == expected_content_type.strip()
            and ";" not in expected_content_type
            and all(ord(character) >= 32 for character in expected_content_type),
            "expected_content_type_invalid",
        )
        expected_content_type = expected_content_type.lower()

    request_headers = dict(headers or {})
    _require(
        all(str(name).lower() != "accept-encoding" for name in request_headers),
        "http_header_forbidden",
    )
    request_headers["Accept-Encoding"] = "identity"
    opened = _open_with_policy(
        "GET",
        url,
        transport=transport or UrllibTransport(),
        headers=request_headers,
        environment=environment or {},
        secret_header_name=secret_header_name,
        timeout_seconds=timeout_seconds,
        max_attempts=max_attempts,
        initial_backoff_seconds=initial_backoff_seconds,
        maximum_backoff_seconds=maximum_backoff_seconds,
        maximum_redirects=maximum_redirects,
        allow_http=allow_http,
        allow_cross_origin_redirects=allow_cross_origin_redirects,
        sleeper=sleeper,
    )
    stream = opened.stream
    fd: int | None = None
    partial: Path | None = None
    finalized = False
    primary: NetworkPrimitiveError | None = None
    try:
        _require(opened.status == 200, "download_status_invalid")
        encoding = _header(stream.headers, "content-encoding")
        _require(
            encoding is None or encoding.strip().lower() in {"", "identity"},
            "download_content_encoding_rejected",
        )
        content_type = _content_type(stream.headers)
        if expected_content_type is not None:
            _require(content_type == expected_content_type, "content_type_mismatch")
        declared = _content_length(stream.headers)
        if declared is not None:
            _require(declared <= maximum, "download_too_large")
            if expected_bytes is not None:
                _require(declared == expected_bytes, "size_mismatch")
        fd, partial = _partial_file(target)
        sha256 = hashlib.sha256()
        total = 0
        with os.fdopen(fd, "wb", closefd=True) as output:
            fd = None
            while True:
                try:
                    chunk = stream.read(1024 * 1024)
                except Exception as error:
                    raise NetworkPrimitiveError("download_read_failed") from error
                _require(isinstance(chunk, bytes), "download_read_failed")
                if not chunk:
                    break
                total += len(chunk)
                _require(total <= maximum, "download_too_large")
                sha256.update(chunk)
                output.write(chunk)
            output.flush()
            os.fsync(output.fileno())
        if declared is not None:
            _require(total == declared, "http_response_length_mismatch")
        if expected_bytes is not None:
            _require(total == expected_bytes, "size_mismatch")
        actual = sha256.hexdigest()
        if expected_hash is not None:
            _require(actual == expected_hash, "checksum_mismatch")
        os.replace(partial, target)
        partial = None
        finalized = True
        return DownloadResult(
            requested_url=opened.requested_url,
            final_url=opened.final_url,
            status=opened.status,
            attempts=opened.attempts,
            redirects=opened.redirects,
            relative_path=relative_path,
            size=total,
            sha256=actual,
            content_type=content_type,
        )
    except NetworkPrimitiveError as error:
        primary = error
        raise
    except OSError as error:
        primary = NetworkPrimitiveError("download_write_failed")
        raise primary from error
    finally:
        cleanup_failed = False
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                cleanup_failed = True
        try:
            stream.close()
        except Exception:
            cleanup_failed = True
        if partial is not None:
            try:
                partial.unlink(missing_ok=True)
            except OSError:
                cleanup_failed = True
        if cleanup_failed and finalized:
            try:
                target.unlink(missing_ok=True)
                finalized = False
            except OSError:
                cleanup_failed = True
        if primary is not None and cleanup_failed:
            primary.cleanup_failed = True
        elif primary is None and cleanup_failed:
            raise NetworkPrimitiveError("download_cleanup_failed", cleanup_failed=True)


def _archive_file(path: Path, *, maximum_archive_bytes: int) -> FileVerification:
    maximum = _bounded_integer(
        maximum_archive_bytes,
        minimum=1,
        maximum=8 * 1024 * 1024 * 1024,
        code="archive_limit_invalid",
    )
    verification = verify_file(path)
    _require(verification.size <= maximum, "archive_too_large")
    return verification


def _safe_member_path(name: str) -> PurePosixPath:
    _require(
        isinstance(name, str)
        and 1 <= len(name) <= 4096
        and "\x00" not in name
        and "\\" not in name
        and not name.startswith("/"),
        "archive_member_path_invalid",
    )
    raw = PurePosixPath(name)
    _require(not raw.is_absolute() and ".." not in raw.parts, "archive_member_path_invalid")
    parts = tuple(part for part in raw.parts if part != ".")
    _require(
        parts
        and len(parts) <= 64
        and all(part not in {"", ".", ".."} for part in parts),
        "archive_member_path_invalid",
    )
    return PurePosixPath(*parts)


def _validate_member_set(
    members: Sequence[_Member],
    *,
    maximum_members: int,
    maximum_total_bytes: int,
) -> tuple[int, int, int]:
    member_limit = _bounded_integer(
        maximum_members,
        minimum=1,
        maximum=100_000,
        code="archive_member_limit_invalid",
    )
    byte_limit = _bounded_integer(
        maximum_total_bytes,
        minimum=0,
        maximum=8 * 1024 * 1024 * 1024,
        code="archive_expansion_limit_invalid",
    )
    _require(len(members) <= member_limit, "archive_too_many_members")
    seen: dict[str, bool] = {}
    file_paths: set[str] = set()
    total_bytes = 0
    files = 0
    directories = 0
    for member in members:
        text = member.path.as_posix()
        _require(text not in seen, "archive_duplicate_member")
        for index in range(1, len(member.path.parts)):
            ancestor = PurePosixPath(*member.path.parts[:index]).as_posix()
            _require(ancestor not in file_paths, "archive_member_conflict")
        if not member.is_directory:
            prefix = text + "/"
            _require(
                not any(existing.startswith(prefix) for existing in seen),
                "archive_member_conflict",
            )
            file_paths.add(text)
            files += 1
            _require(member.size >= 0, "archive_member_size_invalid")
            total_bytes += member.size
            _require(total_bytes <= byte_limit, "archive_expansion_too_large")
        else:
            directories += 1
            _require(member.size == 0, "archive_member_size_invalid")
        seen[text] = member.is_directory
    _require(files > 0, "archive_empty")
    return files, directories, total_bytes


def _zip_members(
    archive: zipfile.ZipFile,
    *,
    maximum_members: int,
    maximum_total_bytes: int,
) -> tuple[tuple[_Member, ...], int, int, int]:
    members: list[_Member] = []
    for info in archive.infolist():
        _require(not (info.flag_bits & 0x1), "archive_encrypted_member_rejected")
        mode = (info.external_attr >> 16) & 0xFFFF
        file_type = stat.S_IFMT(mode)
        is_directory = info.is_dir() or file_type == stat.S_IFDIR
        if is_directory:
            _require(info.file_size == 0, "archive_member_size_invalid")
        if mode:
            _require(
                file_type in {0, stat.S_IFREG, stat.S_IFDIR},
                "archive_special_member_rejected",
            )
        path = _safe_member_path(info.filename)
        members.append(
            _Member(
                path=path,
                size=0 if is_directory else int(info.file_size),
                is_directory=is_directory,
                executable=bool(mode & 0o111),
                source=info,
            )
        )
    counts = _validate_member_set(
        members,
        maximum_members=maximum_members,
        maximum_total_bytes=maximum_total_bytes,
    )
    return tuple(members), *counts


def _tar_members(
    archive: tarfile.TarFile,
    *,
    maximum_members: int,
    maximum_total_bytes: int,
) -> tuple[tuple[_Member, ...], int, int, int]:
    members: list[_Member] = []
    for info in archive.getmembers():
        _require(
            info.isfile() or info.isdir(),
            "archive_special_member_rejected",
        )
        if info.isdir() and info.name in {".", "./"}:
            continue
        if info.isdir():
            _require(info.size == 0, "archive_member_size_invalid")
        path = _safe_member_path(info.name)
        members.append(
            _Member(
                path=path,
                size=0 if info.isdir() else int(info.size),
                is_directory=info.isdir(),
                executable=bool(info.mode & 0o111),
                source=info,
            )
        )
    counts = _validate_member_set(
        members,
        maximum_members=maximum_members,
        maximum_total_bytes=maximum_total_bytes,
    )
    return tuple(members), *counts


def _open_exclusive(path: Path, *, executable: bool) -> int:
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    mode = 0o700 if executable else 0o600
    try:
        return os.open(path, flags, mode)
    except OSError as error:
        raise NetworkPrimitiveError("archive_extract_write_failed") from error


def _copy_member(source: Any, target: Path, *, expected_size: int, executable: bool) -> tuple[int, str]:
    fd = _open_exclusive(target, executable=executable)
    digest = hashlib.sha256()
    total = 0
    try:
        with os.fdopen(fd, "wb", closefd=True) as output:
            fd = -1
            while True:
                chunk = source.read(1024 * 1024)
                _require(isinstance(chunk, bytes), "archive_extract_read_failed")
                if not chunk:
                    break
                total += len(chunk)
                _require(total <= expected_size, "archive_member_size_mismatch")
                digest.update(chunk)
                output.write(chunk)
            output.flush()
            os.fsync(output.fileno())
    except NetworkPrimitiveError:
        raise
    except Exception as error:
        raise NetworkPrimitiveError("archive_extract_write_failed") from error
    finally:
        if fd >= 0:
            try:
                os.close(fd)
            except OSError:
                pass
    _require(total == expected_size, "archive_member_size_mismatch")
    return total, digest.hexdigest()


def _extract_zip(
    archive_path: Path,
    stage: Path,
    *,
    maximum_members: int,
    maximum_total_bytes: int,
) -> tuple[int, int, int, str]:
    try:
        archive = zipfile.ZipFile(archive_path, "r")
    except (OSError, zipfile.BadZipFile) as error:
        raise NetworkPrimitiveError("archive_invalid") from error
    manifest: list[dict[str, object]] = []
    try:
        members, files, directories, expected_total = _zip_members(
            archive,
            maximum_members=maximum_members,
            maximum_total_bytes=maximum_total_bytes,
        )
        actual_total = 0
        for member in sorted(members, key=lambda value: (len(value.path.parts), value.path.as_posix())):
            target = stage.joinpath(*member.path.parts)
            _require(stage in target.parents, "archive_member_path_invalid")
            if member.is_directory:
                target.mkdir(parents=True, exist_ok=False, mode=0o700)
                continue
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            try:
                source = archive.open(member.source, "r")  # type: ignore[arg-type]
            except (OSError, RuntimeError, zipfile.BadZipFile) as error:
                raise NetworkPrimitiveError("archive_extract_read_failed") from error
            with source:
                size, digest = _copy_member(
                    source,
                    target,
                    expected_size=member.size,
                    executable=member.executable,
                )
            actual_total += size
            manifest.append({"path": member.path.as_posix(), "sha256": digest, "size": size})
        _require(actual_total == expected_total, "archive_member_size_mismatch")
        payload = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return files, directories, actual_total, hashlib.sha256(payload).hexdigest()
    finally:
        archive.close()


def _extract_tar(
    archive_path: Path,
    stage: Path,
    *,
    maximum_members: int,
    maximum_total_bytes: int,
) -> tuple[int, int, int, str]:
    try:
        archive = tarfile.open(archive_path, "r:*")
    except (OSError, tarfile.TarError) as error:
        raise NetworkPrimitiveError("archive_invalid") from error
    manifest: list[dict[str, object]] = []
    try:
        members, files, directories, expected_total = _tar_members(
            archive,
            maximum_members=maximum_members,
            maximum_total_bytes=maximum_total_bytes,
        )
        actual_total = 0
        for member in sorted(members, key=lambda value: (len(value.path.parts), value.path.as_posix())):
            target = stage.joinpath(*member.path.parts)
            _require(stage in target.parents, "archive_member_path_invalid")
            if member.is_directory:
                target.mkdir(parents=True, exist_ok=False, mode=0o700)
                continue
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            source = archive.extractfile(member.source)  # type: ignore[arg-type]
            _require(source is not None, "archive_extract_read_failed")
            with source:
                size, digest = _copy_member(
                    source,
                    target,
                    expected_size=member.size,
                    executable=member.executable,
                )
            actual_total += size
            manifest.append({"path": member.path.as_posix(), "sha256": digest, "size": size})
        _require(actual_total == expected_total, "archive_member_size_mismatch")
        payload = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return files, directories, actual_total, hashlib.sha256(payload).hexdigest()
    finally:
        archive.close()


def extract_archive(
    archive_path: Path,
    *,
    archive_format: str,
    destination_root: Path,
    relative_destination: str,
    maximum_archive_bytes: int = 2 * 1024 * 1024 * 1024,
    maximum_members: int = 10_000,
    maximum_total_bytes: int = 2 * 1024 * 1024 * 1024,
) -> ExtractionResult:
    """Safely extract ZIP/TAR content through a sibling staging directory."""

    _require(archive_format in {"zip", "tar"}, "archive_format_invalid")
    archive_verification = _archive_file(
        Path(archive_path),
        maximum_archive_bytes=maximum_archive_bytes,
    )
    root = _real_directory(destination_root, "archive_destination_root_invalid")
    target = _bounded_relative_target(
        root,
        relative_destination,
        code="archive_destination_invalid",
    )
    _require(not target.exists() and not target.is_symlink(), "archive_destination_exists")
    parent = target.parent.resolve(strict=True)
    stage: Path | None = None
    primary: NetworkPrimitiveError | None = None
    try:
        try:
            stage = create_temporary_workspace(parent, prefix="ciw-extract")
        except RuntimePrimitiveError as error:
            raise NetworkPrimitiveError("archive_stage_create_failed") from error
        if archive_format == "zip":
            files, directories, total_bytes, manifest_sha256 = _extract_zip(
                Path(archive_path),
                stage,
                maximum_members=maximum_members,
                maximum_total_bytes=maximum_total_bytes,
            )
        else:
            files, directories, total_bytes, manifest_sha256 = _extract_tar(
                Path(archive_path),
                stage,
                maximum_members=maximum_members,
                maximum_total_bytes=maximum_total_bytes,
            )
        try:
            os.replace(stage, target)
        except OSError as error:
            raise NetworkPrimitiveError("archive_finalize_failed") from error
        stage = None
        return ExtractionResult(
            archive_format=archive_format,
            destination=relative_destination,
            file_count=files,
            directory_count=directories,
            total_bytes=total_bytes,
            archive_sha256=archive_verification.sha256,
            manifest_sha256=manifest_sha256,
        )
    except NetworkPrimitiveError as error:
        primary = error
        raise
    finally:
        if stage is not None:
            cleanup_failed = False
            try:
                finalize_temporary_path(stage, root=parent)
            except RuntimePrimitiveError:
                cleanup_failed = True
            if primary is not None and cleanup_failed:
                primary.cleanup_failed = True
            elif primary is None and cleanup_failed:
                raise NetworkPrimitiveError("archive_cleanup_failed", cleanup_failed=True)
