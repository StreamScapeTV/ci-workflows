"""Upload or retrieve one bounded private CI diagnostic through Cloudflare R2."""
from __future__ import annotations

import gzip
import hashlib
import hmac
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

MAX_RAW_BYTES = 50 * 1024 * 1024
MAX_COMPRESSED_BYTES = 20 * 1024 * 1024
OBJECT_PREFIX = "ci-diagnostics"
STORAGE_CLASS = "STANDARD"
_TIMEOUT_SECONDS = 30

_ACCOUNT_ID = re.compile(r"[0-9a-f]{32}")
_BUCKET = re.compile(r"[a-z0-9][a-z0-9-]{1,61}[a-z0-9]")
_REQUEST_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_DIGEST = re.compile(r"[0-9a-f]{64}")
_OBJECT_KEY = re.compile(
    r"ci-diagnostics/[A-Za-z0-9][A-Za-z0-9._-]{0,127}/[1-9][0-9]{0,18}-[1-9][0-9]{0,3}[.]log[.]gz"
)


class R2DiagnosticError(RuntimeError):
    """Fail-closed error whose code contains no remote configuration or content."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class R2DiagnosticResult:
    object_key: str
    sha256: str
    compressed_bytes: int


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise R2DiagnosticError(code)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sign(key: bytes, value: str) -> bytes:
    return hmac.new(key, value.encode("utf-8"), hashlib.sha256).digest()


def _authorization_headers(
    *,
    method: str,
    url: str,
    payload_hash: str,
    access_key_id: str,
    secret_access_key: str,
    when: datetime,
    storage_class: str | None = None,
) -> dict[str, str]:
    instant = when.astimezone(timezone.utc)
    amz_date = instant.strftime("%Y%m%dT%H%M%SZ")
    date_stamp = instant.strftime("%Y%m%d")
    parsed = urllib.parse.urlsplit(url)
    canonical_uri = urllib.parse.quote(parsed.path or "/", safe="/-_.~")
    canonical_query = parsed.query
    canonical_values = {
        "host": parsed.netloc,
        "x-amz-content-sha256": payload_hash,
        "x-amz-date": amz_date,
    }
    if storage_class is not None:
        canonical_values["x-amz-storage-class"] = storage_class
    signed_names = sorted(canonical_values)
    canonical_headers = "".join(
        f"{name}:{canonical_values[name].strip()}\n" for name in signed_names
    )
    signed_headers = ";".join(signed_names)
    canonical_request = "\n".join(
        (
            method,
            canonical_uri,
            canonical_query,
            canonical_headers,
            signed_headers,
            payload_hash,
        )
    )
    scope = f"{date_stamp}/auto/s3/aws4_request"
    string_to_sign = "\n".join(
        (
            "AWS4-HMAC-SHA256",
            amz_date,
            scope,
            _sha256(canonical_request.encode("utf-8")),
        )
    )
    date_key = _sign(("AWS4" + secret_access_key).encode("utf-8"), date_stamp)
    region_key = _sign(date_key, "auto")
    service_key = _sign(region_key, "s3")
    signing_key = _sign(service_key, "aws4_request")
    signature = hmac.new(
        signing_key,
        string_to_sign.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    headers = {
        "Authorization": (
            "AWS4-HMAC-SHA256 "
            f"Credential={access_key_id}/{scope}, "
            f"SignedHeaders={signed_headers}, "
            f"Signature={signature}"
        ),
        "Host": parsed.netloc,
        "X-Amz-Content-Sha256": payload_hash,
        "X-Amz-Date": amz_date,
    }
    if storage_class is not None:
        headers["X-Amz-Storage-Class"] = storage_class
    return headers


def _r2_url(account_id: str, bucket: str, object_key: str) -> str:
    _require(
        isinstance(account_id, str) and _ACCOUNT_ID.fullmatch(account_id) is not None,
        "invalid_r2_account",
    )
    _require(
        isinstance(bucket, str) and _BUCKET.fullmatch(bucket) is not None,
        "invalid_r2_bucket",
    )
    path = "/" + "/".join(
        urllib.parse.quote(part, safe="-_.~")
        for part in (bucket, *object_key.split("/"))
    )
    return f"https://{account_id}.r2.cloudflarestorage.com{path}"


def _credentials(access_key_id: str, secret_access_key: str) -> None:
    _require(
        isinstance(access_key_id, str) and bool(access_key_id),
        "r2_access_key_required",
    )
    _require(
        isinstance(secret_access_key, str) and bool(secret_access_key),
        "r2_secret_key_required",
    )


def _validated_object_key(request_id: str, run_id: int, attempt: int) -> str:
    _require(
        isinstance(request_id, str) and _REQUEST_ID.fullmatch(request_id) is not None,
        "invalid_request_id",
    )
    _require(isinstance(run_id, int) and 0 < run_id <= 9223372036854775807, "invalid_run_id")
    _require(
        isinstance(attempt, int) and 1 <= attempt <= 1000,
        "invalid_run_attempt",
    )
    return f"{OBJECT_PREFIX}/{request_id}/{run_id}-{attempt}.log.gz"


def _validated_existing_object_key(object_key: str) -> str:
    _require(
        isinstance(object_key, str) and _OBJECT_KEY.fullmatch(object_key) is not None,
        "invalid_object_key",
    )
    tail = object_key.rsplit("/", 1)[1].removesuffix(".log.gz")
    run_text, attempt_text = tail.split("-", 1)
    _require(int(run_text) <= 9223372036854775807, "invalid_object_key")
    _require(1 <= int(attempt_text) <= 1000, "invalid_object_key")
    return object_key


def _read_diagnostic(path: Path) -> bytes:
    _require(path.exists() and path.is_file(), "diagnostic_file_required")
    _require(not path.is_symlink(), "diagnostic_symlink_forbidden")
    try:
        initial_size = path.stat().st_size
        _require(initial_size <= MAX_RAW_BYTES, "diagnostic_raw_too_large")
        with path.open("rb") as handle:
            raw = handle.read(MAX_RAW_BYTES + 1)
        _require(len(raw) <= MAX_RAW_BYTES, "diagnostic_raw_too_large")
        _require(path.stat().st_size == len(raw), "diagnostic_file_changed")
        return raw
    except R2DiagnosticError:
        raise
    except OSError:
        raise R2DiagnosticError("diagnostic_file_unavailable") from None


def download_private_diagnostic(
    *,
    object_key: str,
    expected_sha256: str,
    account_id: str,
    bucket: str,
    access_key_id: str,
    secret_access_key: str,
    opener: Any = urllib.request.urlopen,
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> bytes:
    """GET one exact generated object and verify its bounded compressed digest."""

    object_key = _validated_existing_object_key(object_key)
    _require(
        isinstance(expected_sha256, str)
        and _DIGEST.fullmatch(expected_sha256) is not None,
        "invalid_expected_digest",
    )
    _credentials(access_key_id, secret_access_key)
    url = _r2_url(account_id, bucket, object_key)
    headers = _authorization_headers(
        method="GET",
        url=url,
        payload_hash=_sha256(b""),
        access_key_id=access_key_id,
        secret_access_key=secret_access_key,
        when=clock(),
    )
    request = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with opener(request, timeout=_TIMEOUT_SECONDS) as response:
            compressed = response.read(MAX_COMPRESSED_BYTES + 1)
    except urllib.error.HTTPError as error:
        raise R2DiagnosticError(f"r2_download_http_{error.code}") from None
    except (OSError, urllib.error.URLError, ValueError):
        raise R2DiagnosticError("r2_download_unavailable") from None

    _require(len(compressed) <= MAX_COMPRESSED_BYTES, "r2_download_too_large")
    _require(
        hmac.compare_digest(_sha256(compressed), expected_sha256),
        "r2_download_digest_mismatch",
    )
    return compressed


def _as_readback_error(error: R2DiagnosticError) -> R2DiagnosticError:
    code = error.code
    if code.startswith("r2_download_http_"):
        return R2DiagnosticError("r2_readback_http_" + code.removeprefix("r2_download_http_"))
    if code == "r2_download_unavailable":
        return R2DiagnosticError("r2_readback_unavailable")
    if code == "r2_download_too_large":
        return R2DiagnosticError("r2_readback_too_large")
    if code == "r2_download_digest_mismatch":
        return R2DiagnosticError("r2_readback_digest_mismatch")
    return error


def upload_private_diagnostic(
    *,
    diagnostic_path: Path,
    request_id: str,
    run_id: int,
    attempt: int,
    account_id: str,
    bucket: str,
    access_key_id: str,
    secret_access_key: str,
    opener: Any = urllib.request.urlopen,
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> R2DiagnosticResult:
    """Compress, PUT, GET, and digest-verify exactly one private diagnostic object."""

    _credentials(access_key_id, secret_access_key)
    object_key = _validated_object_key(request_id, run_id, attempt)
    raw = _read_diagnostic(diagnostic_path)
    compressed = gzip.compress(raw, compresslevel=9, mtime=0)
    _require(
        len(compressed) <= MAX_COMPRESSED_BYTES,
        "diagnostic_compressed_too_large",
    )
    digest = _sha256(compressed)

    url = _r2_url(account_id, bucket, object_key)
    when = clock()

    put_headers = _authorization_headers(
        method="PUT",
        url=url,
        payload_hash=digest,
        access_key_id=access_key_id,
        secret_access_key=secret_access_key,
        when=when,
        storage_class=STORAGE_CLASS,
    )
    put_headers["Content-Type"] = "application/gzip"
    put_request = urllib.request.Request(
        url,
        data=compressed,
        headers=put_headers,
        method="PUT",
    )
    try:
        with opener(put_request, timeout=_TIMEOUT_SECONDS) as response:
            response.read(1)
    except urllib.error.HTTPError as error:
        raise R2DiagnosticError(f"r2_upload_http_{error.code}") from None
    except (OSError, urllib.error.URLError, ValueError):
        raise R2DiagnosticError("r2_upload_unavailable") from None

    try:
        readback = download_private_diagnostic(
            object_key=object_key,
            expected_sha256=digest,
            account_id=account_id,
            bucket=bucket,
            access_key_id=access_key_id,
            secret_access_key=secret_access_key,
            opener=opener,
            clock=clock,
        )
    except R2DiagnosticError as error:
        raise _as_readback_error(error) from None
    _require(len(readback) == len(compressed), "r2_readback_size_mismatch")
    return R2DiagnosticResult(
        object_key=object_key,
        sha256=digest,
        compressed_bytes=len(compressed),
    )


__all__ = (
    "MAX_COMPRESSED_BYTES",
    "MAX_RAW_BYTES",
    "OBJECT_PREFIX",
    "R2DiagnosticError",
    "R2DiagnosticResult",
    "STORAGE_CLASS",
    "download_private_diagnostic",
    "upload_private_diagnostic",
)
