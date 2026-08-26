#!/usr/bin/env python3
"""Standalone transport-only Agent State -> Central CI relay."""
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

_PROJECT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_PROFILE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_WORKFLOW = re.compile(r"[a-z0-9][a-z0-9._-]{0,127}\Z")
_REPOSITORY = re.compile(r"[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}\Z")
_SUPPORTED_INTENTS = frozenset(
    {
        ("validation.apple", "host"),
        ("validation.android", "host"),
        ("validation.python", "host"),
    }
)


class BrokerError(RuntimeError):
    """Stable, non-sensitive broker failure."""

    def __init__(self, code: str, status: int = 400) -> None:
        self.code = code
        self.status = status
        super().__init__(code)


def _require(condition: bool, code: str, status: int = 400) -> None:
    if not condition:
        raise BrokerError(code, status)


def _required(environment: Mapping[str, str], name: str) -> str:
    value = environment.get(name, "")
    _require(bool(value), f"missing_{name.lower()}", 500)
    return value


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")


def _json_object(raw: bytes) -> dict[str, Any]:
    def hook(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            _require(key not in result, "invalid_json")
            result[key] = value
        return result

    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=hook)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise BrokerError("invalid_json") from None
    _require(isinstance(value, dict), "invalid_json")
    return value


def _uuid(value: object) -> str:
    _require(isinstance(value, str), "invalid_ci_run_id")
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError):
        raise BrokerError("invalid_ci_run_id") from None
    _require(str(parsed) == value.lower(), "invalid_ci_run_id")
    return str(parsed)


def _safe(value: object, pattern: re.Pattern[str], code: str) -> str:
    _require(isinstance(value, str) and pattern.fullmatch(value) is not None, code)
    return value


def _safe_project(value: object) -> str:
    return _safe(value, _PROJECT, "invalid_project_key")


def _safe_profile(value: object) -> str:
    return _safe(value, _PROFILE, "invalid_test_profile")


def _safe_workflow(value: object) -> str:
    return _safe(value, _WORKFLOW, "invalid_workflow_key")


def _safe_repository(value: object) -> str:
    return _safe(value, _REPOSITORY, "invalid_repository")


def _human_ref(value: object) -> str:
    _require(isinstance(value, str), "invalid_ref", 422)
    text = value.strip()
    _require(
        0 < len(text.encode("utf-8")) <= 512
        and not any(character in text for character in ("\x00", "\r", "\n"))
        and not text.startswith(("refs/heads/", "refs/tags/")),
        "invalid_ref",
        422,
    )
    return text


def _is_tag(value: object) -> bool:
    _require(isinstance(value, bool), "invalid_is_tag", 422)
    return value


def _header(headers: Mapping[str, str], name: str) -> str:
    wanted = name.lower()
    for key, value in headers.items():
        if key.lower() == wanted:
            return value
    return ""


def _http_json(
    request: urllib.request.Request,
    *,
    opener: Any = urllib.request.urlopen,
    expected: tuple[int, ...] = (200,),
) -> tuple[int, Any]:
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

    _require(status in expected, f"remote_http_{status}", 502)
    _require(len(raw) <= MAX_BODY_BYTES, "remote_response_too_large", 502)
    if not raw:
        return status, None
    try:
        return status, json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise BrokerError("remote_invalid_json", 502) from None


@dataclass(frozen=True, slots=True)
class RelayConfig:
    dispatch_app_id: int
    dispatch_app_private_key: str
    agent_state_url: str
    agent_state_secret_key: str
    agent_state_webhook_secret: str
    port: int = 8080

    @classmethod
    def from_environment(
        cls, environment: Mapping[str, str] = os.environ
    ) -> "RelayConfig":
        app_id = _required(environment, "GITHUB_DISPATCH_APP_ID")
        _require(app_id.isdigit() and int(app_id) > 0, "invalid_github_dispatch_app_id", 500)
        url = _required(environment, "AGENT_STATE_SUPABASE_URL").rstrip("/")
        parsed = urllib.parse.urlsplit(url)
        _require(
            parsed.scheme == "https"
            and bool(parsed.netloc)
            and parsed.path in ("", "/")
            and not parsed.query
            and not parsed.fragment,
            "invalid_agent_state_url",
            500,
        )
        port_text = environment.get("CI_BROKER_PORT", "8080")
        _require(
            port_text.isdigit() and 1 <= int(port_text) <= 65535,
            "invalid_ci_broker_port",
            500,
        )
        return cls(
            dispatch_app_id=int(app_id),
            dispatch_app_private_key=_required(
                environment, "GITHUB_DISPATCH_APP_PRIVATE_KEY"
            ),
            agent_state_url=url,
            agent_state_secret_key=_required(
                environment, "AGENT_STATE_SUPABASE_SECRET_KEY"
            ),
            agent_state_webhook_secret=_required(
                environment, "AGENT_STATE_WEBHOOK_SECRET"
            ),
            port=int(port_text),
        )


class AgentStateClient:
    """The relay may only claim a request and terminalize a rejected claim."""

    def __init__(
        self, url: str, secret_key: str, opener: Any = urllib.request.urlopen
    ) -> None:
        self._url = url.rstrip("/")
        self._key = secret_key
        self._opener = opener

    def _rpc(self, name: str, args: Mapping[str, object]) -> dict[str, Any]:
        _require(name in {"claim_ci_run", "transition_ci_run"}, "invalid_rpc", 500)
        request = urllib.request.Request(
            f"{self._url}/rest/v1/rpc/{name}",
            data=_canonical(dict(args)),
            method="POST",
            headers={
                "apikey": self._key,
                "Content-Type": "application/json",
                "Content-Profile": "agent_api",
                "Accept-Profile": "agent_api",
            },
        )
        _status, value = _http_json(request, opener=self._opener)
        _require(isinstance(value, dict), "agent_state_invalid_response", 502)
        return value

    def claim(self, ci_run_id: str) -> dict[str, Any]:
        return self._rpc("claim_ci_run", {"p_ci_run_id": _uuid(ci_run_id)})

    def transition(
        self, ci_run_id: str, patch: Mapping[str, object]
    ) -> dict[str, Any]:
        return self._rpc(
            "transition_ci_run",
            {"p_ci_run_id": _uuid(ci_run_id), "p_patch": dict(patch)},
        )


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


class GitHubAppClient:
    """Minimal GitHub App client for one fixed workflow dispatch."""

    def __init__(
        self, app_id: int, private_key: str, opener: Any = urllib.request.urlopen
    ) -> None:
        self._app_id = app_id
        self._private_key = private_key
        self._opener = opener

    def _jwt(self, now: int | None = None) -> str:
        current = int(time.time()) if now is None else now
        header = _b64url(_canonical({"alg": "RS256", "typ": "JWT"}))
        payload = _b64url(
            _canonical(
                {"iat": current - 30, "exp": current + 8 * 60, "iss": str(self._app_id)}
            )
        )
        signing_input = f"{header}.{payload}".encode("ascii")
        key = self._private_key.replace("\\n", "\n") if "\n" not in self._private_key else self._private_key
        _require("PRIVATE KEY-----" in key, "invalid_github_app_key", 500)
        try:
            with tempfile.NamedTemporaryFile(
                "w", encoding="utf-8", delete=True
            ) as handle:
                handle.write(key)
                handle.flush()
                os.chmod(handle.name, 0o600)
                completed = subprocess.run(
                    ["openssl", "dgst", "-sha256", "-sign", handle.name],
                    input=signing_input,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    check=False,
                    timeout=10,
                )
        except (OSError, subprocess.SubprocessError):
            raise BrokerError("github_app_signing_unavailable", 500) from None
        _require(
            completed.returncode == 0 and bool(completed.stdout),
            "github_app_signing_failed",
            500,
        )
        return f"{header}.{payload}.{_b64url(completed.stdout)}"

    def _request(
        self,
        method: str,
        path: str,
        *,
        token: str | None = None,
        body: Mapping[str, object] | None = None,
        expected: tuple[int, ...] = (200,),
    ) -> tuple[int, Any]:
        _require(path.startswith("/"), "invalid_github_path", 500)
        data = None if body is None else _canonical(dict(body))
        authorization = token if token is not None else self._jwt()
        request = urllib.request.Request(
            "https://api.github.com" + path,
            data=data,
            method=method,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {authorization}",
                "X-GitHub-Api-Version": "2022-11-28",
                **({"Content-Type": "application/json"} if data is not None else {}),
            },
        )
        return _http_json(request, opener=self._opener, expected=expected)

    def repository_token(self, repository: str) -> str:
        repository = _safe_repository(repository)
        _status, installation = self._request(
            "GET", f"/repos/{repository}/installation"
        )
        _require(
            isinstance(installation, dict)
            and isinstance(installation.get("id"), int)
            and installation["id"] > 0,
            "github_installation_missing",
            502,
        )
        _status, token = self._request(
            "POST",
            f"/app/installations/{installation['id']}/access_tokens",
            body={},
            expected=(201,),
        )
        _require(
            isinstance(token, dict)
            and isinstance(token.get("token"), str)
            and bool(token["token"]),
            "github_token_missing",
            502,
        )
        return token["token"]

    def dispatch_relay(
        self,
        *,
        repository: str,
        workflow: str,
        ref: str,
        inputs: Mapping[str, str],
        token: str,
    ) -> None:
        _require(repository == CENTRAL_REPOSITORY, "invalid_central_repository", 500)
        _require(workflow == CENTRAL_WORKFLOW, "invalid_central_workflow", 500)
        _require(ref == CENTRAL_REF, "invalid_central_ref", 500)
        _require(set(inputs) == {"active_key", "ci_run_id"}, "invalid_dispatch_inputs", 500)
        workflow_path = urllib.parse.quote(workflow, safe="")
        self._request(
            "POST",
            f"/repos/{repository}/actions/workflows/{workflow_path}/dispatches",
            token=token,
            body={"ref": CENTRAL_REF, "inputs": dict(inputs)},
            expected=(204,),
        )


def active_identity_key(
    *,
    repository: object,
    ref: object,
    is_tag: object,
    workflow_key: object,
    profile: object,
) -> str:
    raw = _canonical(
        {
            "is_tag": _is_tag(is_tag),
            "profile": _safe_profile(profile),
            "ref": _human_ref(ref),
            "repository": _safe_repository(repository),
            "workflow_key": _safe_workflow(workflow_key),
        }
    )
    return hashlib.sha256(raw).hexdigest()


@dataclass(frozen=True, slots=True)
class RelayRequest:
    ci_run_id: str
    project_key: str
    repository: str
    ref: str
    is_tag: bool
    workflow_key: str
    profile: str

    @classmethod
    def from_claimed_run(cls, value: Mapping[str, object]) -> "RelayRequest":
        _require(value.get("origin") == "agent_request", "invalid_ci_origin", 422)
        _require(value.get("status") == "accepted", "invalid_ci_status", 409)
        _require(
            value.get("requested_source_sha") in (None, ""),
            "requested_source_sha_unsupported",
            422,
        )
        workflow_key = _safe_workflow(value.get("workflow_key"))
        profile = _safe_profile(value.get("test_profile"))
        _require(
            (workflow_key, profile) in _SUPPORTED_INTENTS,
            "unsupported_ci_intent",
            422,
        )
        _require(value.get("inputs") in (None, {}), "unsupported_ci_inputs", 422)
        return cls(
            ci_run_id=_uuid(value.get("ci_run_id")),
            project_key=_safe_project(value.get("project_key")),
            repository=_safe_repository(value.get("repository")),
            ref=_human_ref(value.get("ref")),
            is_tag=_is_tag(value.get("is_tag")),
            workflow_key=workflow_key,
            profile=profile,
        )

    @property
    def active_key(self) -> str:
        return active_identity_key(
            repository=self.repository,
            ref=self.ref,
            is_tag=self.is_tag,
            workflow_key=self.workflow_key,
            profile=self.profile,
        )

    def workflow_inputs(self) -> dict[str, str]:
        return {"active_key": self.active_key, "ci_run_id": self.ci_run_id}


class ThinCiRelay:
    def __init__(
        self,
        config: RelayConfig,
        *,
        agent_state: AgentStateClient | None = None,
        dispatch_github: GitHubAppClient | None = None,
    ) -> None:
        self.config = config
        self.agent_state = agent_state or AgentStateClient(
            config.agent_state_url, config.agent_state_secret_key
        )
        self.dispatch_github = dispatch_github or GitHubAppClient(
            config.dispatch_app_id, config.dispatch_app_private_key
        )

    @staticmethod
    def _run_from_result(value: Mapping[str, object]) -> dict[str, Any]:
        run = value.get("run")
        _require(
            value.get("ok") is True and isinstance(run, dict),
            "agent_state_ci_rejected",
            409,
        )
        return run

    def _cancel(self, ci_run_id: str, code: str) -> None:
        try:
            self.agent_state.transition(
                ci_run_id, {"status": "cancelled", "error_summary": code[:128]}
            )
        except BrokerError:
            pass

    def handle_agent_state_webhook(
        self, raw: bytes, headers: Mapping[str, str]
    ) -> dict[str, object]:
        supplied = _header(headers, "X-StreamScape-Webhook-Secret")
        _require(
            bool(supplied)
            and hmac.compare_digest(
                supplied.encode("utf-8"),
                self.config.agent_state_webhook_secret.encode("utf-8"),
            ),
            "agent_state_webhook_unauthorized",
            401,
        )
        payload = _json_object(raw)
        _require(
            payload.get("type") == "INSERT",
            "agent_state_webhook_ignored",
            202,
        )
        _require(
            payload.get("schema") == "agent_private"
            and payload.get("table") == "ci_runs",
            "agent_state_webhook_ignored",
            202,
        )
        record = payload.get("record")
        _require(isinstance(record, dict), "agent_state_webhook_invalid")
        if record.get("origin") != "agent_request" or record.get("status") != "requested":
            return {"ok": True, "ignored": True}

        ci_run_id = _uuid(record.get("ci_run_id"))
        claim = self.agent_state.claim(ci_run_id)
        run = self._run_from_result(claim)
        if claim.get("replayed") is True and run.get("status") != "accepted":
            return {"ok": True, "replayed": True}

        try:
            request = RelayRequest.from_claimed_run(run)
            token = self.dispatch_github.repository_token(CENTRAL_REPOSITORY)
            self.dispatch_github.dispatch_relay(
                repository=CENTRAL_REPOSITORY,
                workflow=CENTRAL_WORKFLOW,
                ref=CENTRAL_REF,
                inputs=request.workflow_inputs(),
                token=token,
            )
        except BrokerError as error:
            self._cancel(ci_run_id, error.code)
            raise

        return {
            "ok": True,
            "dispatched": True,
            "recovered": claim.get("replayed") is True,
        }


class BrokerHttpServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], relay: ThinCiRelay) -> None:
        super().__init__(address, BrokerHttpHandler)
        self.relay = relay


class BrokerHttpHandler(BaseHTTPRequestHandler):
    server: BrokerHttpServer

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

    def _body(self) -> bytes:
        raw_length = self.headers.get("Content-Length", "")
        _require(raw_length.isdigit(), "content_length_required", 411)
        length = int(raw_length)
        _require(1 <= length <= MAX_BODY_BYTES, "request_body_too_large", 413)
        raw = self.rfile.read(length)
        _require(len(raw) == length, "request_body_incomplete")
        return raw

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/healthz":
            self._write(200, {"ok": True})
        else:
            self._write(404, {"ok": False, "code": "not_found"})

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/hooks/agent-state":
            self._write(404, {"ok": False, "code": "not_found"})
            return
        try:
            value = self.server.relay.handle_agent_state_webhook(
                self._body(), {key: value for key, value in self.headers.items()}
            )
        except BrokerError as error:
            if error.code == "agent_state_webhook_ignored":
                self._write(202, {"ok": True, "ignored": True})
            else:
                self._write(error.status, {"ok": False, "code": error.code})
            return
        except Exception:
            self._write(500, {"ok": False, "code": "internal_error"})
            return
        self._write(200, value)


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
    _require(set(inputs) == {"active_key", "ci_run_id"}, "self_check_failed", 500)
    rendered = json.dumps(inputs, sort_keys=True)
    for private in (
        "ExampleOrg/private-app",
        "develop",
        "synthetic-project",
        "validation.apple",
        "host",
    ):
        _require(private not in rendered, "self_check_failed", 500)
    return {
        "ok": True,
        "mode": "thin-relay",
        "routes": ["/healthz", "/hooks/agent-state"],
    }


def serve(config: RelayConfig) -> None:
    BrokerHttpServer(("0.0.0.0", config.port), ThinCiRelay(config)).serve_forever(
        poll_interval=0.5
    )


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Thin Central CI webhook relay")
    result.add_argument("command", choices=("server", "self-check"))
    return result


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "server":
            serve(RelayConfig.from_environment())
        else:
            print(json.dumps(self_check(), sort_keys=True, separators=(",", ":")))
    except BrokerError as error:
        print(error.code, file=os.sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
