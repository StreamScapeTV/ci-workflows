"""Minimal HTTP runtime for the Agent State to Central CI relay."""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from typing import Mapping

from .ci_broker import BrokerError, CENTRAL_REPOSITORY, CENTRAL_WORKFLOW
from .ci_relay import RelayConfig, RelayRequest, ThinCiRelay

_MAX_BODY_BYTES = 256 * 1024


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode(
        "utf-8"
    )


class RelayHttpServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], relay: ThinCiRelay) -> None:
        super().__init__(address, RelayHttpHandler)
        self.relay = relay


class RelayHttpHandler(BaseHTTPRequestHandler):
    server: RelayHttpServer

    def log_message(self, format: str, *args: object) -> None:  # noqa: A003
        return

    def _write(self, status: int, value: Mapping[str, object]) -> None:
        raw = _canonical(dict(value))
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def _error(self, error: BrokerError) -> None:
        self._write(error.status, {"ok": False, "code": error.code})

    def _body(self) -> bytes:
        raw_length = self.headers.get("Content-Length", "")
        if not raw_length.isdigit():
            raise BrokerError("content_length_required", 411)
        length = int(raw_length)
        if length < 1 or length > _MAX_BODY_BYTES:
            raise BrokerError("request_body_too_large", 413)
        raw = self.rfile.read(length)
        if len(raw) != length:
            raise BrokerError("request_body_incomplete", 400)
        return raw

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/healthz":
            self._write(200, {"ok": True})
            return
        self._write(404, {"ok": False, "code": "not_found"})

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/hooks/agent-state":
            self._write(404, {"ok": False, "code": "not_found"})
            return
        try:
            raw = self._body()
            result = self.server.relay.handle_agent_state_webhook(
                raw,
                {key: value for key, value in self.headers.items()},
            )
        except BrokerError as error:
            self._error(error)
            return
        self._write(200, result)


def serve(config: RelayConfig) -> None:
    relay = ThinCiRelay(config)
    server = RelayHttpServer(("0.0.0.0", config.port), relay)
    server.serve_forever(poll_interval=0.5)


def self_check() -> dict[str, object]:
    request = RelayRequest.from_claimed_run(
        {
            "ci_run_id": "00000000-0000-4000-8000-000000000019",
            "project_key": "synthetic-project",
            "origin": "agent_request",
            "status": "accepted",
            "repository": "ExampleOrg/private-app",
            "ref": "develop",
            "is_tag": False,
            "workflow_key": "validation.apple",
            "test_profile": "host",
            "inputs": {},
            "requested_source_sha": None,
        }
    )
    inputs = request.workflow_inputs()
    if set(inputs) != {
        "active_key",
        "ci_run_id",
        "project_key",
        "repository",
        "ref",
        "is_tag",
        "workflow_key",
        "profile",
        "inputs_json",
    }:
        raise BrokerError("relay_self_check_failed", 500)
    if inputs["ref"] != "develop" or inputs["is_tag"] != "false":
        raise BrokerError("relay_self_check_failed", 500)
    if len(inputs["active_key"]) != 64:
        raise BrokerError("relay_self_check_failed", 500)
    if any("sha" in key or "dispatch_token" in key for key in inputs):
        raise BrokerError("relay_self_check_failed", 500)
    return {
        "ok": True,
        "mode": "thin-relay",
        "central_repository": CENTRAL_REPOSITORY,
        "central_workflow": CENTRAL_WORKFLOW,
        "routes": ["/healthz", "/hooks/agent-state"],
    }


__all__ = (
    "RelayHttpHandler",
    "RelayHttpServer",
    "self_check",
    "serve",
)
