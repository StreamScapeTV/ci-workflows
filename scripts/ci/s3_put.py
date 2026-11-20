#!/usr/bin/env python3
"""Small standard-library S3 client for Central CI (SigV4, path style).

Central stores logs, screenshots and kept release archives in one bucket of the
owner's S3 API. Layout (the only layouts accepted):

    logs/<project>/<request_id>/<stage>/<seq>.log      (live chunks, then final.log)
    logs/<project>/<request_id>/<file>                  (one-object logs of older workflows)
    screenshots/<project>/<id>/<file>
    releases/<project>/<release version>/<file>

`stream` uploads a growing log as numbered chunk objects (S3 has no append) every
few seconds and writes one merged `final.log` when the stage ends. Secrets are
scrubbed (CI_SECRET_* values and the S3 credentials) before any byte is uploaded.

Credentials come from the environment only (never argv, never logs):
S3_GITHUB_TRANSPORT_ACCESS_KEY and S3_GITHUB_TRANSPORT_SECRET_KEY.

The background log stream never holds them: a trusted step runs `presign` and writes one file of
presigned PUT URLs (one per chunk name plus final.log, bounded expiry), and `stream --presigned-file`
uploads with those URLs only. Branch code running in the same job therefore cannot read a bucket key
from the uploader's environment; the most it can do is overwrite its own request's log objects.

Size decision (20 Nov 2026): a single object is limited to 256 MiB (one PUT). A larger artifact
(evidence zip, release archive) fails only that upload with the explicit message
`object_too_large (limit 256 MiB)`; the callers decide whether that is fatal. Multipart upload is
not implemented.
"""

from __future__ import annotations

import argparse
import base64
import json
import socket
import tempfile
import datetime as dt
import hashlib
import hmac
import os
import re
import signal
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable, Mapping

DEFAULT_ENDPOINT = "https://s3.nas.faruqi.dev"
DEFAULT_BUCKET = "github-transport"
DEFAULT_REGION = "us-east-1"
KINDS = ("logs", "screenshots", "releases")
MAX_OBJECT_BYTES = 256 * 1024 * 1024
MAX_KEY_LENGTH = 512
ATTEMPTS = 3
RETRY_DELAYS_SECONDS = (1, 2)
TIMEOUT_SECONDS = 60
SEGMENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,127}\Z")


class S3Error(RuntimeError):
    def __init__(self, category: str, *, transient: bool = False) -> None:
        super().__init__(category)
        self.category = category
        self.transient = transient


def object_key(kind: str, project: str, object_id: str, file_name: str, stage: str = "") -> str:
    """Build one bounded object key; every segment is validated, never joined raw."""
    if kind not in KINDS:
        raise S3Error("invalid_kind")
    project = project.rsplit("/", 1)[-1]
    parts = [kind, project, object_id, *([stage] if stage else []), file_name]
    for part in parts:
        if SEGMENT.fullmatch(part) is None or part in (".", ".."):
            raise S3Error("invalid_key_segment")
    key = "/".join(parts)
    if len(key) > MAX_KEY_LENGTH:
        raise S3Error("key_too_long")
    return key


def _hmac(key: bytes, message: str) -> bytes:
    return hmac.new(key, message.encode(), hashlib.sha256).digest()


def sign_headers(
    *,
    method: str,
    url: str,
    payload_sha256: str,
    access_key: str,
    secret_key: str,
    region: str,
    now: dt.datetime,
    extra_headers: Mapping[str, str] | None = None,
) -> dict[str, str]:
    parsed = urllib.parse.urlsplit(url)
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    date = now.strftime("%Y%m%d")
    headers = {
        "host": parsed.netloc,
        "x-amz-content-sha256": payload_sha256,
        "x-amz-date": amz_date,
    }
    for name, value in (extra_headers or {}).items():
        headers[name.lower()] = value.strip()
    names = sorted(headers)
    canonical_headers = "".join(f"{name}:{headers[name]}\n" for name in names)
    signed = ";".join(names)
    canonical = "\n".join(
        (method, parsed.path or "/", parsed.query, canonical_headers, signed, payload_sha256)
    )
    scope = f"{date}/{region}/s3/aws4_request"
    to_sign = "\n".join(
        ("AWS4-HMAC-SHA256", amz_date, scope, hashlib.sha256(canonical.encode()).hexdigest())
    )
    signing = _hmac(
        _hmac(_hmac(_hmac(("AWS4" + secret_key).encode(), date), region), "s3"),
        "aws4_request",
    )
    signature = hmac.new(signing, to_sign.encode(), hashlib.sha256).hexdigest()
    headers["authorization"] = (
        f"AWS4-HMAC-SHA256 Credential={access_key}/{scope}, "
        f"SignedHeaders={signed}, Signature={signature}"
    )
    headers.pop("host")
    return headers


class Client:
    def __init__(
        self,
        environ: Mapping[str, str],
        *,
        opener: Callable[..., object] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], dt.datetime] = lambda: dt.datetime.now(dt.timezone.utc),
    ) -> None:
        self.access_key = environ.get("S3_GITHUB_TRANSPORT_ACCESS_KEY", "")
        self.secret_key = environ.get("S3_GITHUB_TRANSPORT_SECRET_KEY", "")
        if not self.access_key or not self.secret_key:
            raise S3Error("missing_credentials")
        self.endpoint = environ.get("S3_ENDPOINT", DEFAULT_ENDPOINT).rstrip("/")
        self.bucket = environ.get("S3_BUCKET", DEFAULT_BUCKET)
        self.region = environ.get("S3_REGION", DEFAULT_REGION)
        if not self.endpoint.startswith("https://"):
            raise S3Error("endpoint_must_be_https")
        self._opener = opener or (lambda *a, **k: urllib.request.urlopen(*a, **k))
        self._sleep = sleep
        self._clock = clock

    def _url(self, key: str) -> str:
        return f"{self.endpoint}/{self.bucket}/{urllib.parse.quote(key, safe='/-_.~')}"

    def _request(
        self,
        method: str,
        key: str,
        data: bytes = b"",
        extra: Mapping[str, str] | None = None,
        *,
        attempts: int = ATTEMPTS,
        timeout: float = TIMEOUT_SECONDS,
    ):
        url = self._url(key)
        digest = hashlib.sha256(data).hexdigest()
        for attempt in range(1, attempts + 1):
            headers = sign_headers(
                method=method,
                url=url,
                payload_sha256=digest,
                access_key=self.access_key,
                secret_key=self.secret_key,
                region=self.region,
                now=self._clock(),
                extra_headers=extra,
            )
            request = urllib.request.Request(
                url, data=data if method == "PUT" else None, method=method, headers=headers
            )
            try:
                with self._opener(request, timeout=timeout) as response:  # type: ignore[operator]
                    return response.status, dict(response.headers)
            except urllib.error.HTTPError as exc:
                if method == "HEAD" and exc.code == 404:
                    return 404, {}
                transient = exc.code in (408, 429) or 500 <= exc.code <= 599
                error = S3Error(f"s3_http_{exc.code}", transient=transient)
            except (OSError, urllib.error.URLError):
                error = S3Error("s3_transport", transient=True)
            if not error.transient or attempt == attempts:
                raise error
            self._sleep(RETRY_DELAYS_SECONDS[attempt - 1])
        raise AssertionError("unreachable")

    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream", *, immutable: bool = False,
            attempts: int = ATTEMPTS, timeout: float = TIMEOUT_SECONDS) -> None:
        if len(data) > MAX_OBJECT_BYTES:
            raise S3Error("object_too_large")
        if immutable:
            status, headers = self._request("HEAD", key)
            if status == 200:
                etag = {k.lower(): v for k, v in headers.items()}.get("etag", "").strip('"')
                if etag != hashlib.md5(data).hexdigest():  # noqa: S324 - S3 ETag, not security
                    raise S3Error("immutable_object_differs")
                return
        self._request("PUT", key, data, {"content-type": content_type}, attempts=attempts, timeout=timeout)

    def reachable(self) -> bool:
        try:
            self._request("HEAD", "logs/.reachability-probe", attempts=1, timeout=10)
        except S3Error as exc:
            return exc.category.startswith("s3_http_")
        return True


MAX_LOG_BYTES = 16 * 1024 * 1024
READ_LIMIT_BYTES = 4 * 1024 * 1024
MAX_CHUNKS = 4000
CHUNK_INTERVAL_SECONDS = 5
FINAL_ATTEMPTS = 2
FINAL_TIMEOUT_SECONDS = 30
PRESIGN_EXPIRES_SECONDS = 6 * 3600
MIN_VARIANT_BYTES = 6


def presign_url(*, method: str, endpoint: str, bucket: str, key: str, access_key: str, secret_key: str,
                region: str, now: dt.datetime, expires: int) -> str:
    """SigV4 query-string presigned URL (host header only, unsigned payload)."""
    quoted = urllib.parse.quote(key, safe="/-_.~")
    path = f"/{bucket}/{quoted}" if bucket else f"/{quoted}"
    host = urllib.parse.urlsplit(endpoint).netloc
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    date = now.strftime("%Y%m%d")
    scope = f"{date}/{region}/s3/aws4_request"
    query = {
        "X-Amz-Algorithm": "AWS4-HMAC-SHA256",
        "X-Amz-Credential": f"{access_key}/{scope}",
        "X-Amz-Date": amz_date,
        "X-Amz-Expires": str(expires),
        "X-Amz-SignedHeaders": "host",
    }
    canonical_query = "&".join(f"{urllib.parse.quote(k, safe='-_.~')}={urllib.parse.quote(v, safe='-_.~')}" for k, v in sorted(query.items()))
    canonical = "\n".join((method, path, canonical_query, f"host:{host}\n", "host", "UNSIGNED-PAYLOAD"))
    to_sign = "\n".join(("AWS4-HMAC-SHA256", amz_date, scope, hashlib.sha256(canonical.encode()).hexdigest()))
    signing = _hmac(_hmac(_hmac(_hmac(("AWS4" + secret_key).encode(), date), region), "s3"), "aws4_request")
    signature = hmac.new(signing, to_sign.encode(), hashlib.sha256).hexdigest()
    return f"{endpoint}{path}?{canonical_query}&X-Amz-Signature={signature}"


class PresignedClient:
    """Uploads with presigned PUT URLs only; holds no credential."""

    def __init__(self, urls: Mapping[str, str], *, project: str, request_id: str, stage: str,
                 opener: Callable[..., object] | None = None, sleep: Callable[[float], None] = time.sleep) -> None:
        self.urls = dict(urls)
        self.prefix = f"logs/{project}/{request_id}/{stage}/"
        self._opener = opener or (lambda *a, **k: urllib.request.urlopen(*a, **k))
        self._sleep = sleep

    def put(self, key: str, data: bytes, content_type: str = "text/plain", *, immutable: bool = False,
            attempts: int = ATTEMPTS, timeout: float = TIMEOUT_SECONDS) -> None:
        if not key.startswith(self.prefix) or key[len(self.prefix):] not in self.urls:
            raise S3Error("no_presigned_url")
        url = self.urls[key[len(self.prefix):]]
        for attempt in range(1, attempts + 1):
            request = urllib.request.Request(url, data=data, method="PUT", headers={"content-type": content_type})
            try:
                with self._opener(request, timeout=timeout):  # type: ignore[operator]
                    return
            except urllib.error.HTTPError as exc:
                error = S3Error(f"s3_http_{exc.code}", transient=exc.code in (408, 429) or 500 <= exc.code <= 599)
            except (OSError, urllib.error.URLError):
                error = S3Error("s3_transport", transient=True)
            if not error.transient or attempt == attempts:
                raise error
            self._sleep(RETRY_DELAYS_SECONDS[min(attempt - 1, len(RETRY_DELAYS_SECONDS) - 1)])


def scrub_values(environ: Mapping[str, str]) -> list[bytes]:
    """CI_SECRET_* values and the S3 credentials, each also as \\n-escaped, JSON-escaped, URL-quoted and base64."""
    values: list[bytes] = []
    for name, value in environ.items():
        if not value or not (
            name.startswith("CI_SECRET_")
            or name in ("S3_GITHUB_TRANSPORT_ACCESS_KEY", "S3_GITHUB_TRANSPORT_SECRET_KEY")
        ):
            continue
        raw = value.encode()
        candidates = [raw, raw.replace(b"\n", b"\\n"), json.dumps(value)[1:-1].encode(),
                      urllib.parse.quote(value, safe="").encode(), base64.b64encode(raw), base64.urlsafe_b64encode(raw)]
        for line in value.splitlines():
            if len(line.strip().encode()) >= MIN_VARIANT_BYTES:
                candidates.append(line.strip().encode())
        for candidate in candidates:
            if candidate and (candidate == raw or len(candidate) >= MIN_VARIANT_BYTES) and candidate not in values:
                values.append(candidate)
    return sorted(values, key=len, reverse=True)


def scrub(data: bytes, values: list[bytes]) -> bytes:
    for value in values:
        data = data.replace(value, b"[REDACTED]")
    return data


class LogStreamer:
    """Upload the scrubbed growing log as numbered chunks, then one merged final.log.

    The log is consumed incrementally by raw offset (bounded reads, so a log of any size is handled
    and chunk/final bytes always equal the scrubbed log). The last max-secret-length minus one
    scrubbed bytes are held back and re-scrubbed together with the next bytes, so a secret split
    across two rounds is never exposed; the held bytes are flushed when the stage ends. Emitted
    bytes are also appended to a local merged file, which becomes final.log (the last 16 MiB when
    the log is larger).
    """

    def __init__(self, client, *, project: str, request_id: str, stage: str, log_path: Path,
                 environ: Mapping[str, str], work_dir: Path | None = None) -> None:
        self.client = client
        self.log_path = log_path
        self.values = scrub_values(environ)
        self.hold = max((len(v) for v in self.values), default=1) - 1
        self.prefix = (project, request_id, stage)
        self.raw_pos = 0
        self.held = b""
        self.sequence = 0
        self.dropped_chunks = 0
        self._tmp = tempfile.TemporaryDirectory(dir=work_dir)
        self.merged = Path(self._tmp.name, "merged.log")
        self.merged.write_bytes(b"")
        # validate the layout once, before any upload
        object_key("logs", project, request_id, "000000.log", stage)

    def _read_new(self, limit: int) -> bytes:
        if self.log_path.is_symlink() or not self.log_path.is_file():
            return b""
        with self.log_path.open("rb") as handle:
            handle.seek(self.raw_pos)
            data = handle.read(limit)
        self.raw_pos += len(data)
        return data

    def _put(self, file_name: str, data: bytes, **options) -> None:
        key = object_key("logs", self.prefix[0], self.prefix[1], file_name, self.prefix[2])
        self.client.put(key, data, "text/plain", **options)

    def _emit(self, data: bytes) -> None:
        with self.merged.open("ab") as handle:
            handle.write(data)
        if self.sequence >= MAX_CHUNKS:
            self.dropped_chunks += 1
            return
        self._put(f"{self.sequence:06d}.log", data)
        self.sequence += 1

    def step(self, *, final: bool = False) -> bool:
        """Upload newly safe bytes; returns True when a chunk was written."""
        wrote = False
        while True:
            new = self._read_new(READ_LIMIT_BYTES)
            at_end = len(new) < READ_LIMIT_BYTES
            if not new and not (final and self.held):
                return wrote
            data = scrub(self.held + new, self.values)
            cut = len(data) if final and at_end else max(0, len(data) - self.hold)
            emit, self.held = data[:cut], data[cut:]
            if emit:
                self._emit(emit)
                wrote = True
            if at_end:
                return wrote

    def finish(self) -> None:
        try:
            self.step(final=True)
        finally:
            size = self.merged.stat().st_size
            if size <= MAX_LOG_BYTES:
                body = self.merged.read_bytes()
            else:
                with self.merged.open("rb") as handle:
                    handle.seek(size - MAX_LOG_BYTES)
                    body = f"[log truncated: first {size - MAX_LOG_BYTES} bytes omitted]\n".encode() + handle.read()
            if self.dropped_chunks:
                body += f"\n[{self.dropped_chunks} later chunk(s) were not uploaded as separate objects]\n".encode()
            # bounded so the stop step is deterministic: at most FINAL_ATTEMPTS x FINAL_TIMEOUT_SECONDS
            self._put("final.log", body, attempts=FINAL_ATTEMPTS, timeout=FINAL_TIMEOUT_SECONDS)
            self._tmp.cleanup()


def run_stream(streamer: LogStreamer, stop: threading.Event, *, interval: float = CHUNK_INTERVAL_SECONDS) -> int:
    failed = False
    while not stop.wait(interval):
        try:
            streamer.step()
        except S3Error as exc:
            print(f"S3 log chunk failed: {exc.category}", file=sys.stderr)
            if not exc.transient:
                failed = True
                break
    try:
        streamer.finish()
    except S3Error as exc:
        print(f"S3 final log failed: {exc.category}", file=sys.stderr)
        return 1
    return 1 if failed else 0


def endpoint_reachable(endpoint: str, timeout: float = 8) -> bool:
    """Unauthenticated TCP reachability of the S3 endpoint (needs no credential)."""
    parsed = urllib.parse.urlsplit(endpoint)
    try:
        with socket.create_connection((parsed.hostname, parsed.port or 443), timeout=timeout):
            return True
    except OSError:
        return False


def write_presigned(path: Path, environ: Mapping[str, str], *, project: str, request_id: str, stage: str,
                    count: int, expires: int, now: dt.datetime | None = None) -> None:
    client = Client(environ)
    now = now or dt.datetime.now(dt.timezone.utc)
    names = [f"{index:06d}.log" for index in range(min(count, MAX_CHUNKS))] + ["final.log"]
    urls = {
        name: presign_url(method="PUT", endpoint=client.endpoint, bucket=client.bucket,
                          key=object_key("logs", project, request_id, name, stage),
                          access_key=client.access_key, secret_key=client.secret_key,
                          region=client.region, now=now, expires=expires)
        for name in names
    }
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump({"urls": urls}, handle)


def main(argv: list[str] | None = None, environ: Mapping[str, str] = os.environ) -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    put = sub.add_parser("put")
    put.add_argument("--kind", required=True, choices=KINDS)
    put.add_argument("--repository", required=True)
    put.add_argument("--id", required=True, dest="object_id")
    put.add_argument("--stage", default="")
    put.add_argument("--file-path", required=True)
    put.add_argument("--file-name", required=True)
    put.add_argument("--content-type", default="application/octet-stream")
    put.add_argument("--immutable", action="store_true")
    presign = sub.add_parser("presign")
    presign.add_argument("--repository", required=True)
    presign.add_argument("--request-id", required=True)
    presign.add_argument("--stage", required=True)
    presign.add_argument("--count", type=int, default=MAX_CHUNKS)
    presign.add_argument("--expires", type=int, default=PRESIGN_EXPIRES_SECONDS)
    presign.add_argument("--out", required=True)
    stream = sub.add_parser("stream")
    stream.add_argument("--repository", required=True)
    stream.add_argument("--request-id", required=True)
    stream.add_argument("--stage", required=True)
    stream.add_argument("--log-path", required=True)
    stream.add_argument("--stop-file", required=True)
    stream.add_argument("--presigned-file", default="", help="presigned PUT URLs (no credential in the environment)")
    stream.add_argument("--interval", type=float, default=CHUNK_INTERVAL_SECONDS)
    sub.add_parser("probe")
    args = parser.parse_args(argv)
    try:
        if args.command == "probe":
            endpoint = environ.get("S3_ENDPOINT", DEFAULT_ENDPOINT).rstrip("/")
            print("true" if endpoint_reachable(endpoint) else "false")
            return 0
        if args.command == "presign":
            project = args.repository.rsplit("/", 1)[-1]
            write_presigned(Path(args.out), environ, project=project, request_id=args.request_id, stage=args.stage,
                            count=args.count, expires=args.expires)
            return 0
        if args.command == "stream":
            project = args.repository.rsplit("/", 1)[-1]
            if args.presigned_file:
                document = json.loads(Path(args.presigned_file).read_text(encoding="utf-8"))
                client = PresignedClient(document["urls"], project=project, request_id=args.request_id, stage=args.stage)
            else:
                client = Client(environ)
            streamer = LogStreamer(
                client, project=project, request_id=args.request_id,
                stage=args.stage, log_path=Path(args.log_path), environ=environ,
            )
            stop = threading.Event()
            stop_file = Path(args.stop_file)

            def watch() -> None:
                while not stop.is_set():
                    if stop_file.exists():
                        stop.set()
                    time.sleep(0.5)

            threading.Thread(target=watch, daemon=True).start()
            signal.signal(signal.SIGTERM, lambda *_: stop.set())
            signal.signal(signal.SIGINT, lambda *_: stop.set())
            return run_stream(streamer, stop, interval=args.interval)
        client = Client(environ)
        path = Path(args.file_path)
        if path.is_symlink() or not path.is_file():
            raise S3Error("invalid_file_path")
        data = path.read_bytes()
        key = object_key(args.kind, args.repository, args.object_id, args.file_name, args.stage)
        client.put(key, data, args.content_type, immutable=args.immutable)
    except S3Error as exc:
        detail = " (limit 256 MiB)" if exc.category == "object_too_large" else ""
        print(f"S3 upload failed: {exc.category}{detail}", file=sys.stderr)
        return 1
    digest = hashlib.sha256(data).hexdigest()
    lines = f"object_key={key}\nfile_sha256={digest}\n"
    output = environ.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as handle:
            handle.write(lines)
    else:
        sys.stdout.write(lines)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
