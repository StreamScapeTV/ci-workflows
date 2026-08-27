#!/usr/bin/env python3
"""Small Agent State INSERT webhook -> Central GitHub workflow relay."""
from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import re
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Mapping, Sequence

CENTRAL_REPOSITORY = "StreamScapeTV/ci-workflows"
CENTRAL_WORKFLOW = ".github/workflows/central-ci-dispatch.yml"
CENTRAL_REF = "main"
MAX_BODY_BYTES = 256 * 1024
HTTP_TIMEOUT_SECONDS = 30
SUPPORTED_WORKFLOWS = frozenset(
    {
        "validation.apple",
        "validation.android",
        "validation.python",
        "validation.node",
        "validation.flutter",
        "validation.gitops",
        "source.snapshot",
    }
)
_REPOSITORY = re.compile(r"[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}\Z")
_WORKFLOW = re.compile(r"[a-z0-9][a-z0-9._-]{0,127}\Z")


class BrokerError(RuntimeError):
    def __init__(self, code: str, status: int = 400) -> None:
        self.code = code
        self.status = status
        super().__init__(code)


def require(condition: bool, code: str, status: int = 400) -> None:
    if not condition:
        raise BrokerError(code, status)


def required(environment: Mapping[str, str], name: str) -> str:
    value = environment.get(name, "")
    require(bool(value), f"missing_{name.lower()}", 500)
    return value


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def json_object(raw: bytes) -> dict[str, Any]:
    try:
        value = json.loads(raw.decode())
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise BrokerError("invalid_json") from None
    require(isinstance(value, dict), "invalid_json")
    return value


def ci_uuid(value: object) -> str:
    require(isinstance(value, str), "invalid_ci_run_id")
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError):
        raise BrokerError("invalid_ci_run_id") from None
    require(str(parsed) == value.lower(), "invalid_ci_run_id")
    return str(parsed)


def repository(value: object) -> str:
    require(isinstance(value, str) and _REPOSITORY.fullmatch(value) is not None, "invalid_repository", 422)
    return value


def human_ref(value: object) -> str:
    require(isinstance(value, str), "invalid_ref", 422)
    value = value.strip()
    require(
        0 < len(value.encode()) <= 512
        and not any(c in value for c in ("\0", "\r", "\n"))
        and not value.startswith(("refs/heads/", "refs/tags/")),
        "invalid_ref",
        422,
    )
    return value


def workflow(value: object) -> str:
    require(isinstance(value, str) and _WORKFLOW.fullmatch(value) is not None, "invalid_workflow_key", 422)
    require(value in SUPPORTED_WORKFLOWS, "unsupported_ci_intent", 422)
    return value


def is_tag(value: object) -> bool:
    require(isinstance(value, bool), "invalid_is_tag", 422)
    return value


def header(headers: Mapping[str, str], name: str) -> str:
    wanted = name.lower()
    return next((value for key, value in headers.items() if key.lower() == wanted), "")


def http_json(request: urllib.request.Request, *, opener: Any = urllib.request.urlopen, expected: tuple[int, ...] = (200,)) -> tuple[int, Any]:
    try:
        with opener(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
            status = int(getattr(response, "status", response.getcode()))
            raw = response.read(MAX_BODY_BYTES + 1)
    except urllib.error.HTTPError as error:
        status = int(error.code)
        if status not in expected:
            raise BrokerError(f"remote_http_{status}", 502) from None
        raw = error.read(MAX_BODY_BYTES + 1)
    except (OSError, urllib.error.URLError, ValueError):
        raise BrokerError("remote_unavailable", 502) from None
    require(status in expected, f"remote_http_{status}", 502)
    require(len(raw) <= MAX_BODY_BYTES, "remote_response_too_large", 502)
    if not raw:
        return status, None
    try:
        return status, json.loads(raw.decode())
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise BrokerError("remote_invalid_json", 502) from None


@dataclass(frozen=True, slots=True)
class Config:
    github_app_id: int
    github_app_private_key: str
    agent_state_url: str
    agent_state_secret_key: str
    webhook_secret: str
    port: int = 8080

    @classmethod
    def from_environment(cls, environment: Mapping[str, str] = os.environ) -> "Config":
        app_id = required(environment, "GITHUB_DISPATCH_APP_ID")
        require(app_id.isdigit() and int(app_id) > 0, "invalid_github_dispatch_app_id", 500)
        url = required(environment, "AGENT_STATE_SUPABASE_URL").rstrip("/")
        parsed = urllib.parse.urlsplit(url)
        require(parsed.scheme == "https" and bool(parsed.netloc) and parsed.path in ("", "/"), "invalid_agent_state_url", 500)
        port = environment.get("CI_BROKER_PORT", "8080")
        require(port.isdigit() and 1 <= int(port) <= 65535, "invalid_ci_broker_port", 500)
        return cls(
            int(app_id),
            required(environment, "GITHUB_DISPATCH_APP_PRIVATE_KEY"),
            url,
            required(environment, "AGENT_STATE_SUPABASE_SECRET_KEY"),
            required(environment, "AGENT_STATE_WEBHOOK_SECRET"),
            int(port),
        )


class AgentState:
    def __init__(self, url: str, secret: str, opener: Any = urllib.request.urlopen) -> None:
        self.url = url.rstrip("/")
        self.secret = secret
        self.opener = opener

    def rpc(self, name: str, payload: Mapping[str, object]) -> dict[str, Any]:
        require(name in {"claim_ci_run", "transition_ci_run"}, "invalid_rpc", 500)
        request = urllib.request.Request(
            f"{self.url}/rest/v1/rpc/{name}",
            data=canonical(dict(payload)),
            method="POST",
            headers={
                "apikey": self.secret,
                "Content-Type": "application/json",
                "Content-Profile": "agent_api",
                "Accept-Profile": "agent_api",
            },
        )
        _status, value = http_json(request, opener=self.opener)
        require(isinstance(value, dict), "agent_state_invalid_response", 502)
        return value

    def claim(self, run_id: str) -> dict[str, Any]:
        return self.rpc("claim_ci_run", {"p_ci_run_id": ci_uuid(run_id)})

    def cancel(self, run_id: str) -> None:
        self.rpc("transition_ci_run", {"p_ci_run_id": ci_uuid(run_id), "p_patch": {"status": "cancelled"}})


def b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()


class GitHub:
    def __init__(self, app_id: int, private_key: str, opener: Any = urllib.request.urlopen) -> None:
        self.app_id = app_id
        self.private_key = private_key
        self.opener = opener

    def jwt(self) -> str:
        now = int(time.time())
        first = b64url(canonical({"alg": "RS256", "typ": "JWT"}))
        second = b64url(canonical({"iat": now - 30, "exp": now + 480, "iss": str(self.app_id)}))
        signing = f"{first}.{second}".encode()
        key = self.private_key.replace("\\n", "\n") if "\n" not in self.private_key else self.private_key
        require("PRIVATE KEY-----" in key, "invalid_github_app_key", 500)
        try:
            with tempfile.NamedTemporaryFile("w", encoding="utf-8") as handle:
                handle.write(key)
                handle.flush()
                os.chmod(handle.name, 0o600)
                completed = subprocess.run(
                    ["openssl", "dgst", "-sha256", "-sign", handle.name],
                    input=signing,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    timeout=10,
                    check=False,
                )
        except (OSError, subprocess.SubprocessError):
            raise BrokerError("github_app_signing_failed", 500) from None
        require(completed.returncode == 0 and bool(completed.stdout), "github_app_signing_failed", 500)
        return f"{first}.{second}.{b64url(completed.stdout)}"

    def request(self, method: str, path: str, *, token: str | None = None, body: Mapping[str, object] | None = None, expected: tuple[int, ...] = (200,)) -> tuple[int, Any]:
        data = None if body is None else canonical(dict(body))
        request = urllib.request.Request(
            "https://api.github.com" + path,
            data=data,
            method=method,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {token or self.jwt()}",
                "X-GitHub-Api-Version": "2022-11-28",
                **({"Content-Type": "application/json"} if data else {}),
            },
        )
        return http_json(request, opener=self.opener, expected=expected)

    def token(self) -> str:
        _status, installation = self.request("GET", f"/repos/{CENTRAL_REPOSITORY}/installation")
        require(isinstance(installation, dict) and isinstance(installation.get("id"), int), "github_installation_missing", 502)
        _status, value = self.request("POST", f"/app/installations/{installation['id']}/access_tokens", body={}, expected=(201,))
        require(isinstance(value, dict) and isinstance(value.get("token"), str) and bool(value["token"]), "github_token_missing", 502)
        return value["token"]

    def dispatch(self, inputs: Mapping[str, str]) -> None:
        require(set(inputs) == {"active_key", "ci_run_id"}, "invalid_dispatch_inputs", 500)
        path = urllib.parse.quote(CENTRAL_WORKFLOW, safe="")
        self.request(
            "POST",
            f"/repos/{CENTRAL_REPOSITORY}/actions/workflows/{path}/dispatches",
            token=self.token(),
            body={"ref": CENTRAL_REF, "inputs": dict(inputs)},
            expected=(204,),
        )


@dataclass(frozen=True, slots=True)
class Request:
    ci_run_id: str
    repository: str
    ref: str
    is_tag: bool
    workflow_key: str
    inputs: Mapping[str, object]

    @classmethod
    def from_claim(cls, value: Mapping[str, object]) -> "Request":
        require(value.get("origin") == "agent_request", "invalid_ci_origin", 422)
        require(value.get("status") == "accepted", "invalid_ci_status", 409)
        raw_inputs = value.get("inputs")
        require(raw_inputs is None or isinstance(raw_inputs, dict), "invalid_ci_inputs", 422)
        return cls(
            ci_uuid(value.get("ci_run_id")),
            repository(value.get("repository")),
            human_ref(value.get("ref")),
            is_tag(value.get("is_tag")),
            workflow(value.get("workflow_key")),
            raw_inputs or {},
        )

    @property
    def active_key(self) -> str:
        return hashlib.sha256(canonical({"repository": self.repository, "ref": self.ref, "is_tag": self.is_tag})).hexdigest()

    def dispatch_inputs(self) -> dict[str, str]:
        return {"active_key": self.active_key, "ci_run_id": self.ci_run_id}


class Relay:
    def __init__(self, config: Config, *, state: AgentState | None = None, github: GitHub | None = None) -> None:
        self.config = config
        self.state = state or AgentState(config.agent_state_url, config.agent_state_secret_key)
        self.github = github or GitHub(config.github_app_id, config.github_app_private_key)

    def webhook(self, raw: bytes, headers: Mapping[str, str]) -> dict[str, object]:
        supplied = header(headers, "X-StreamScape-Webhook-Secret")
        require(bool(supplied) and hmac.compare_digest(supplied, self.config.webhook_secret), "agent_state_webhook_unauthorized", 401)
        payload = json_object(raw)
        if payload.get("type") != "INSERT" or payload.get("schema") != "agent_private" or payload.get("table") != "ci_runs":
            return {"ok": True, "ignored": True}
        record = payload.get("record")
        require(isinstance(record, dict), "agent_state_webhook_invalid")
        if record.get("origin") != "agent_request" or record.get("status") != "requested":
            return {"ok": True, "ignored": True}
        run_id = ci_uuid(record.get("ci_run_id"))
        claim = self.state.claim(run_id)
        run = claim.get("run")
        require(claim.get("ok") is True and isinstance(run, dict), "agent_state_ci_rejected", 409)
        if claim.get("replayed") is True and run.get("status") != "accepted":
            return {"ok": True, "replayed": True}
        try:
            request = Request.from_claim(run)
            self.github.dispatch(request.dispatch_inputs())
        except BrokerError:
            try:
                self.state.cancel(run_id)
            except BrokerError:
                pass
            raise
        return {"ok": True, "dispatched": True}


class HttpServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], relay: Relay) -> None:
        super().__init__(address, Server)
        self.relay = relay


class Server(BaseHTTPRequestHandler):
    server: HttpServer

    def log_message(self, format: str, *args: object) -> None:  # noqa: A003
        return

    def write_json(self, status: int, value: Mapping[str, object]) -> None:
        raw = canonical(dict(value))
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self) -> None:  # noqa: N802
        self.write_json(200, {"ok": True}) if self.path == "/healthz" else self.write_json(404, {"ok": False})

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/hooks/agent-state":
            self.write_json(404, {"ok": False})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            require(0 < length <= MAX_BODY_BYTES, "invalid_content_length", 413)
            result = self.server.relay.webhook(self.rfile.read(length), dict(self.headers.items()))
            self.write_json(200, result)
        except BrokerError as error:
            self.write_json(error.status, {"ok": False, "code": error.code})
        except Exception:
            self.write_json(500, {"ok": False, "code": "internal_error"})


def self_check() -> dict[str, object]:
    request = Request.from_claim(
        {
            "ci_run_id": "00000000-0000-4000-8000-000000000019",
            "origin": "agent_request",
            "status": "accepted",
            "repository": "ExampleOrg/private-app",
            "ref": "develop",
            "is_tag": False,
            "workflow_key": "validation.apple",
            "test_profile": "anything",
            "inputs": {"test_command": "./test.sh"},
        }
    )
    inputs = request.dispatch_inputs()
    require(set(inputs) == {"active_key", "ci_run_id"}, "self_check_failed", 500)
    rendered = json.dumps(inputs)
    require("ExampleOrg/private-app" not in rendered and "./test.sh" not in rendered, "self_check_failed", 500)
    return {"ok": True, "mode": "thin-relay", "routes": ["/healthz", "/hooks/agent-state"]}


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("server", "self-check"))
    args = parser.parse_args(argv)
    try:
        if args.command == "server":
            config = Config.from_environment()
            HttpServer(("0.0.0.0", config.port), Relay(config)).serve_forever(poll_interval=0.5)
        else:
            print(json.dumps(self_check(), sort_keys=True, separators=(",", ":")))
    except BrokerError as error:
        print(error.code, file=os.sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
