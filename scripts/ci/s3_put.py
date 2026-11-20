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
"""

from __future__ import annotations

import argparse
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

    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream", *, immutable: bool = False) -> None:
        if len(data) > MAX_OBJECT_BYTES:
            raise S3Error("object_too_large")
        if immutable:
            status, headers = self._request("HEAD", key)
            if status == 200:
                etag = {k.lower(): v for k, v in headers.items()}.get("etag", "").strip('"')
                if etag != hashlib.md5(data).hexdigest():  # noqa: S324 - S3 ETag, not security
                    raise S3Error("immutable_object_differs")
                return
        self._request("PUT", key, data, {"content-type": content_type})

    def reachable(self) -> bool:
        try:
            self._request("HEAD", "logs/.reachability-probe", attempts=1, timeout=10)
        except S3Error as exc:
            return exc.category.startswith("s3_http_")
        return True


MAX_LOG_BYTES = 16 * 1024 * 1024
CHUNK_INTERVAL_SECONDS = 5


def scrub_values(environ: Mapping[str, str]) -> list[bytes]:
    values: list[bytes] = []
    for name, value in environ.items():
        if not value or not (
            name.startswith("CI_SECRET_")
            or name in ("S3_GITHUB_TRANSPORT_ACCESS_KEY", "S3_GITHUB_TRANSPORT_SECRET_KEY")
        ):
            continue
        raw = value.encode()
        for candidate in (raw, raw.replace(b"\n", b"\\n")):
            if candidate and candidate not in values:
                values.append(candidate)
    return values


def scrub(data: bytes, values: list[bytes]) -> bytes:
    for value in values:
        data = data.replace(value, b"[REDACTED]")
    return data


class LogStreamer:
    """Upload the scrubbed growing log as numbered chunks, then one merged final.log.

    The whole raw log is scrubbed on every round and only the scrubbed bytes that
    cannot still be the start of a configured secret (all but the last
    max-secret-length minus one) are uploaded, so a secret split across two rounds
    is never exposed. The remainder is flushed when the stage ends.
    """

    def __init__(self, client: Client, *, project: str, request_id: str, stage: str, log_path: Path, environ: Mapping[str, str]) -> None:
        self.client = client
        self.log_path = log_path
        self.values = scrub_values(environ)
        self.hold = max((len(v) for v in self.values), default=1) - 1
        self.prefix = (project, request_id, stage)
        self.uploaded = 0
        self.sequence = 0
        # validate the layout once, before any upload
        object_key("logs", project, request_id, "000000.log", stage)

    def _scrubbed(self) -> bytes:
        if self.log_path.is_symlink() or not self.log_path.is_file():
            return b""
        raw = self.log_path.read_bytes()[-MAX_LOG_BYTES:]
        return scrub(raw, self.values)

    def _put(self, file_name: str, data: bytes) -> None:
        key = object_key("logs", self.prefix[0], self.prefix[1], file_name, self.prefix[2])
        self.client.put(key, data, "text/plain")

    def step(self, *, final: bool = False) -> bool:
        """Upload newly safe bytes; returns True when a chunk was written."""
        data = self._scrubbed()
        end = len(data) if final else max(self.uploaded, len(data) - self.hold)
        if end <= self.uploaded:
            return False
        self._put(f"{self.sequence:06d}.log", data[self.uploaded:end])
        self.uploaded = end
        self.sequence += 1
        return True

    def finish(self) -> None:
        self.step(final=True)
        self._put("final.log", self._scrubbed())


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
    stream = sub.add_parser("stream")
    stream.add_argument("--repository", required=True)
    stream.add_argument("--request-id", required=True)
    stream.add_argument("--stage", required=True)
    stream.add_argument("--log-path", required=True)
    stream.add_argument("--stop-file", required=True)
    stream.add_argument("--interval", type=float, default=CHUNK_INTERVAL_SECONDS)
    sub.add_parser("probe")
    args = parser.parse_args(argv)
    try:
        client = Client(environ)
        if args.command == "probe":
            print("true" if client.reachable() else "false")
            return 0
        if args.command == "stream":
            streamer = LogStreamer(
                client, project=args.repository.rsplit("/", 1)[-1], request_id=args.request_id,
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
            return run_stream(streamer, stop, interval=args.interval)
        path = Path(args.file_path)
        if path.is_symlink() or not path.is_file():
            raise S3Error("invalid_file_path")
        data = path.read_bytes()
        key = object_key(args.kind, args.repository, args.object_id, args.file_name, args.stage)
        client.put(key, data, args.content_type, immutable=args.immutable)
    except S3Error as exc:
        print(f"S3 upload failed: {exc.category}", file=sys.stderr)
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
