"""Receipt-bound read-only retrieval for private Central CI diagnostics."""
from __future__ import annotations

import argparse
import base64
from dataclasses import dataclass
import gzip
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import re
import sys
from typing import Any, Mapping, Sequence
import urllib.parse

from .r2_diagnostics import (
    MAX_RAW_BYTES,
    R2DiagnosticError,
    download_private_diagnostic,
)

_MAX_RECEIPT_BYTES = 512
_MAX_CAPABILITY_CHARS = 1024
_CAPABILITY = re.compile(r"[A-Za-z0-9_-]{16,1024}\Z")
_RECEIPT = re.compile(
    r"r2:(ci-diagnostics/[A-Za-z0-9][A-Za-z0-9._-]{0,127}/"
    r"[1-9][0-9]{0,18}-[1-9][0-9]{0,3}[.]log[.]gz)"
    r"#sha256=([0-9a-f]{64})\Z"
)


class DiagnosticReadError(RuntimeError):
    """Stable public-safe retrieval error."""

    def __init__(self, code: str, status: int = 404) -> None:
        self.code = code
        self.status = status
        super().__init__(code)


def _require(condition: bool, code: str, status: int = 404) -> None:
    if not condition:
        raise DiagnosticReadError(code, status)


def _required(environment: Mapping[str, str], name: str) -> str:
    value = environment.get(name, "")
    _require(bool(value), f"missing_{name.lower()}", 500)
    return value


@dataclass(frozen=True, slots=True)
class DiagnosticReceipt:
    object_key: str
    sha256: str

    @classmethod
    def parse(cls, value: object) -> "DiagnosticReceipt":
        _require(isinstance(value, str), "diagnostic_not_found")
        _require(0 < len(value.encode("utf-8")) <= _MAX_RECEIPT_BYTES, "diagnostic_not_found")
        match = _RECEIPT.fullmatch(value)
        _require(match is not None, "diagnostic_not_found")
        assert match is not None
        return cls(object_key=match.group(1), sha256=match.group(2))

    def render(self) -> str:
        return f"r2:{self.object_key}#sha256={self.sha256}"


def encode_receipt_capability(receipt: object) -> str:
    """Encode the exact existing Agent State receipt as a URL-safe bearer capability."""
    value = DiagnosticReceipt.parse(receipt).render().encode("utf-8")
    token = base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")
    _require(len(token) <= _MAX_CAPABILITY_CHARS, "diagnostic_not_found")
    return token


def decode_receipt_capability(token: object) -> DiagnosticReceipt:
    _require(isinstance(token, str) and _CAPABILITY.fullmatch(token) is not None, "diagnostic_not_found")
    assert isinstance(token, str)
    padding = "=" * (-len(token) % 4)
    try:
        raw = base64.b64decode(token + padding, altchars=b"-_", validate=True)
        value = raw.decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        raise DiagnosticReadError("diagnostic_not_found", 404) from None
    _require(len(raw) <= _MAX_RECEIPT_BYTES, "diagnostic_not_found")
    receipt = DiagnosticReceipt.parse(value)
    _require(encode_receipt_capability(receipt.render()) == token, "diagnostic_not_found")
    return receipt


@dataclass(frozen=True, slots=True)
class DiagnosticReadConfig:
    account_id: str
    bucket: str
    access_key_id: str
    secret_access_key: str
    port: int = 8081

    @classmethod
    def from_environment(
        cls,
        environment: Mapping[str, str] = os.environ,
    ) -> "DiagnosticReadConfig":
        port_text = environment.get("CI_DIAGNOSTICS_PORT", "8081")
        _require(port_text.isdigit() and 1 <= int(port_text) <= 65535, "invalid_ci_diagnostics_port", 500)
        return cls(
            account_id=_required(environment, "R2_ACCOUNT_ID"),
            bucket=_required(environment, "R2_BUCKET"),
            access_key_id=_required(environment, "R2_READ_ACCESS_KEY_ID"),
            secret_access_key=_required(environment, "R2_READ_SECRET_ACCESS_KEY"),
            port=int(port_text),
        )


class DiagnosticReader:
    """Download exactly one receipt-selected R2 object with read-only credentials."""

    def __init__(self, config: DiagnosticReadConfig) -> None:
        self.config = config

    def retrieve(self, capability: object) -> bytes:
        receipt = decode_receipt_capability(capability)
        try:
            compressed = download_private_diagnostic(
                object_key=receipt.object_key,
                expected_sha256=receipt.sha256,
                account_id=self.config.account_id,
                bucket=self.config.bucket,
                access_key_id=self.config.access_key_id,
                secret_access_key=self.config.secret_access_key,
            )
        except R2DiagnosticError as error:
            if error.code in {"r2_download_http_404", "r2_download_digest_mismatch"}:
                raise DiagnosticReadError("diagnostic_not_found", 404) from None
            raise DiagnosticReadError("diagnostic_unavailable", 502) from None
        try:
            raw = gzip.decompress(compressed)
        except (OSError, EOFError):
            raise DiagnosticReadError("diagnostic_unavailable", 502) from None
        _require(len(raw) <= MAX_RAW_BYTES, "diagnostic_unavailable", 502)
        try:
            raw.decode("utf-8")
        except UnicodeDecodeError:
            raise DiagnosticReadError("diagnostic_unavailable", 502) from None
        return raw


class DiagnosticHttpServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], reader: DiagnosticReader) -> None:
        super().__init__(address, DiagnosticHttpHandler)
        self.reader = reader


class DiagnosticHttpHandler(BaseHTTPRequestHandler):
    server: DiagnosticHttpServer
    server_version = "central-ci-diagnostics"
    sys_version = ""

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def _security_headers(self) -> None:
        self.send_header("Cache-Control", "private, no-store")
        self.send_header("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")

    def _json(self, status: int, value: Mapping[str, object]) -> None:
        raw = json.dumps(dict(value), sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self._security_headers()
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _text(self, raw: bytes) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Disposition", "inline")
        self._security_headers()
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlsplit(self.path)
        if parsed.query or parsed.fragment:
            self._json(404, {"ok": False, "code": "not_found"})
            return
        if parsed.path == "/healthz":
            self._json(200, {"ok": True})
            return
        parts = parsed.path.split("/")
        if len(parts) == 3 and parts[1] == "diagnostics" and parts[2]:
            try:
                raw = self.server.reader.retrieve(parts[2])
            except DiagnosticReadError as error:
                self._json(error.status, {"ok": False, "code": error.code})
                return
            self._text(raw)
            return
        self._json(404, {"ok": False, "code": "not_found"})

    def do_HEAD(self) -> None:  # noqa: N802
        self._json(405, {"ok": False, "code": "method_not_allowed"})

    def do_POST(self) -> None:  # noqa: N802
        self._json(405, {"ok": False, "code": "method_not_allowed"})


def serve(config: DiagnosticReadConfig | None = None) -> None:
    selected = config or DiagnosticReadConfig.from_environment()
    reader = DiagnosticReader(selected)
    server = DiagnosticHttpServer(("0.0.0.0", selected.port), reader)
    server.serve_forever(poll_interval=0.5)


def self_check() -> dict[str, object]:
    receipt = "r2:ci-diagnostics/00000000-0000-4000-8000-000000000019/32860000001-1.log.gz#sha256=" + "a" * 64
    token = encode_receipt_capability(receipt)
    parsed = decode_receipt_capability(token)
    _require(parsed.render() == receipt, "diagnostic_self_check_failed", 500)
    _require("/" not in token and "#" not in token and "=" not in token, "diagnostic_self_check_failed", 500)
    return {
        "ok": True,
        "mode": "receipt-bound-r2-reader",
        "routes": ["/healthz", "/diagnostics/<capability>"],
    }


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Read-only Central CI R2 diagnostics service")
    result.add_argument("command", choices=("server", "self-check"))
    return result


def main(
    argv: Sequence[str] | None = None,
    environment: Mapping[str, str] = os.environ,
) -> int:
    args = parser().parse_args(list(argv) if argv is not None else None)
    try:
        if args.command == "server":
            serve(DiagnosticReadConfig.from_environment(environment))
            return 0
        print(json.dumps(self_check(), sort_keys=True, separators=(",", ":")))
        return 0
    except DiagnosticReadError as error:
        print(error.code, file=sys.stderr)
        return 1


__all__ = (
    "DiagnosticHttpHandler",
    "DiagnosticHttpServer",
    "DiagnosticReadConfig",
    "DiagnosticReadError",
    "DiagnosticReader",
    "DiagnosticReceipt",
    "decode_receipt_capability",
    "encode_receipt_capability",
    "main",
    "self_check",
    "serve",
)
