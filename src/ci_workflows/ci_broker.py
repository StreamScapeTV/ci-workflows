"""Event-driven Central CI broker with opaque public dispatch inputs.

The broker is deployed outside GitHub Actions.  It owns no durable state: Agent
State records CI lifecycle, GitHub owns execution, and R2 owns short-lived raw
diagnostics.  Public Actions runs receive only an opaque dispatch id and an
authenticated opaque envelope.
"""
from __future__ import annotations

import base64
import binascii
import gzip
import hashlib
import hmac
import json
import os
import re
import secrets
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import PurePosixPath
from typing import Any, Mapping

from .r2_diagnostics import (
    MAX_COMPRESSED_BYTES,
    R2DiagnosticError,
    download_private_diagnostic,
)

CENTRAL_REPOSITORY = "StreamScapeTV/ci-workflows"
CENTRAL_WORKFLOW = ".github/workflows/central-ci-dispatch.yml"
CENTRAL_REF = "main"
CENTRAL_WORKFLOW_REF = (
    "StreamScapeTV/ci-workflows/.github/workflows/central-ci-dispatch.yml@refs/heads/main"
)
OIDC_AUDIENCE = "streamscape-ci-broker"
PRIVATE_CONFIG_PATH = ".github/central-ci.json"
MAX_BODY_BYTES = 256 * 1024
MAX_CONFIG_BYTES = 64 * 1024
TOKEN_TTL_SECONDS = 15 * 60
_HTTP_TIMEOUT_SECONDS = 30
_SHA = re.compile(r"[0-9a-f]{40}")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_PROJECT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_PROFILE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_WORKFLOW_KEY = re.compile(r"[a-z0-9][a-z0-9._-]{0,127}")
_REPOSITORY = re.compile(r"[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}")
_SAFE_SCALAR = re.compile(r"[^\r\n\x00]{1,512}")
_SKIP_MARKERS = ("[skip ci]", "[ci skip]")
_TERMINAL = {"succeeded", "failed", "cancelled", "timed_out"}
_SUPPORTED_CAPABILITIES = {"apple-host-test"}


class BrokerError(RuntimeError):
    """Stable, non-sensitive broker failure."""

    def __init__(self, code: str, status: int = 400) -> None:
        self.code = code
        self.status = status
        super().__init__(code)


def _require(condition: bool, code: str, status: int = 400) -> None:
    if not condition:
        raise BrokerError(code, status)


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(value: str) -> bytes:
    _require(isinstance(value, str) and 0 < len(value) <= 200_000, "invalid_token")
    try:
        return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except (ValueError, binascii.Error):
        raise BrokerError("invalid_token") from None


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode(
        "utf-8"
    )


def _json_object(raw: bytes, code: str = "invalid_json") -> dict[str, Any]:
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise BrokerError(code) from None
    _require(isinstance(value, dict), code)
    return value


def _safe_repository(value: object) -> str:
    _require(isinstance(value, str) and _REPOSITORY.fullmatch(value) is not None, "invalid_repository")
    return value


def _safe_sha(value: object) -> str:
    _require(isinstance(value, str) and _SHA.fullmatch(value) is not None, "invalid_source_sha")
    return value


def _safe_project(value: object) -> str:
    _require(isinstance(value, str) and _PROJECT.fullmatch(value) is not None, "invalid_project_key")
    return value


def _safe_profile(value: object) -> str:
    _require(isinstance(value, str) and _PROFILE.fullmatch(value) is not None, "invalid_test_profile")
    return value


def _safe_workflow_key(value: object) -> str:
    _require(
        isinstance(value, str) and _WORKFLOW_KEY.fullmatch(value) is not None,
        "invalid_workflow_key",
    )
    return value


def _safe_ref(value: object) -> str:
    _require(isinstance(value, str) and 1 <= len(value) <= 512, "invalid_ref")
    _require("\n" not in value and "\r" not in value and "\x00" not in value, "invalid_ref")
    return value


def _safe_product_scalar(value: object, code: str) -> str:
    _require(isinstance(value, str) and _SAFE_SCALAR.fullmatch(value) is not None, code)
    return value


def _safe_workspace(value: object) -> str:
    text = _safe_product_scalar(value, "invalid_workspace")
    path = PurePosixPath(text)
    _require(not path.is_absolute() and ".." not in path.parts and text not in (".", ""), "invalid_workspace")
    _require(text.endswith(".xcworkspace"), "invalid_workspace")
    return text


def _uuid(value: object) -> str:
    _require(isinstance(value, str), "invalid_ci_run_id")
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError):
        raise BrokerError("invalid_ci_run_id") from None
    _require(str(parsed) == value.lower(), "invalid_ci_run_id")
    return str(parsed)


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
        with opener(request, timeout=_HTTP_TIMEOUT_SECONDS) as response:
            status = int(getattr(response, "status", response.getcode()))
            raw = response.read(MAX_BODY_BYTES + 1)
    except urllib.error.HTTPError as error:
        status = int(error.code)
        if status in expected:
            raw = error.read(MAX_BODY_BYTES + 1)
        else:
            raise BrokerError(f"remote_http_{status}", 502) from None
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


@dataclass(frozen=True)
class BrokerConfig:
    source_app_id: int
    source_app_private_key: str
    source_webhook_secret: str
    dispatch_app_id: int
    dispatch_app_private_key: str
    agent_state_url: str
    agent_state_secret_key: str
    agent_state_webhook_secret: str
    r2_account_id: str
    r2_bucket: str
    r2_read_access_key_id: str
    r2_read_secret_access_key: str
    port: int = 8080

    @classmethod
    def from_environment(cls, env: Mapping[str, str] = os.environ) -> "BrokerConfig":
        def required(name: str) -> str:
            value = env.get(name, "")
            _require(bool(value), f"missing_{name.lower()}", 500)
            return value

        def app_id(name: str) -> int:
            value = required(name)
            _require(value.isdigit() and int(value) > 0, f"invalid_{name.lower()}", 500)
            return int(value)

        url = required("AGENT_STATE_SUPABASE_URL").rstrip("/")
        parsed = urllib.parse.urlsplit(url)
        _require(parsed.scheme == "https" and bool(parsed.netloc) and not parsed.query, "invalid_agent_state_url", 500)
        port_text = env.get("CI_BROKER_PORT", "8080")
        _require(port_text.isdigit() and 1 <= int(port_text) <= 65535, "invalid_ci_broker_port", 500)
        return cls(
            source_app_id=app_id("GITHUB_SOURCE_APP_ID"),
            source_app_private_key=required("GITHUB_SOURCE_APP_PRIVATE_KEY"),
            source_webhook_secret=required("GITHUB_SOURCE_WEBHOOK_SECRET"),
            dispatch_app_id=app_id("GITHUB_DISPATCH_APP_ID"),
            dispatch_app_private_key=required("GITHUB_DISPATCH_APP_PRIVATE_KEY"),
            agent_state_url=url,
            agent_state_secret_key=required("AGENT_STATE_SUPABASE_SECRET_KEY"),
            agent_state_webhook_secret=required("AGENT_STATE_WEBHOOK_SECRET"),
            r2_account_id=required("R2_ACCOUNT_ID"),
            r2_bucket=required("R2_BUCKET"),
            r2_read_access_key_id=required("R2_READ_ACCESS_KEY_ID"),
            r2_read_secret_access_key=required("R2_READ_SECRET_ACCESS_KEY"),
            port=int(port_text),
        )


class OpaqueEnvelope:
    """Authenticated opaque envelope using HMAC-SHA256-derived stream and MAC keys.

    This transport is intentionally small and dependency-free.  Confidentiality is
    supplied by an HMAC PRF keystream with a random nonce; integrity is a separate
    HMAC over version, nonce, ciphertext and dispatch id.  Keys are domain-separated.
    """

    def __init__(self, secret: str) -> None:
        _require(bool(secret), "dispatch_secret_required", 500)
        root = hashlib.sha256(secret.encode("utf-8")).digest()
        self._enc = hmac.new(root, b"ci-broker-envelope-encryption-v1", hashlib.sha256).digest()
        self._mac = hmac.new(root, b"ci-broker-envelope-authentication-v1", hashlib.sha256).digest()
        self._id = hmac.new(root, b"ci-broker-dispatch-id-v1", hashlib.sha256).digest()

    def dispatch_id(self, dedupe: Mapping[str, object]) -> str:
        return _b64url(hmac.new(self._id, _canonical(dict(dedupe)), hashlib.sha256).digest())

    def _stream(self, nonce: bytes, length: int) -> bytes:
        result = bytearray()
        counter = 0
        while len(result) < length:
            result.extend(
                hmac.new(
                    self._enc,
                    nonce + counter.to_bytes(4, "big"),
                    hashlib.sha256,
                ).digest()
            )
            counter += 1
        return bytes(result[:length])

    def seal(self, dispatch_id: str, payload: Mapping[str, object]) -> str:
        plain = _canonical(dict(payload))
        _require(len(plain) <= MAX_CONFIG_BYTES, "dispatch_payload_too_large", 500)
        nonce = secrets.token_bytes(16)
        stream = self._stream(nonce, len(plain))
        cipher = bytes(left ^ right for left, right in zip(plain, stream, strict=True))
        body = b"\x01" + nonce + cipher
        tag = hmac.new(
            self._mac,
            dispatch_id.encode("ascii") + b"\x00" + body,
            hashlib.sha256,
        ).digest()
        return _b64url(body + tag)

    def open(self, dispatch_id: str, token: str, now: int | None = None) -> dict[str, Any]:
        raw = _b64url_decode(token)
        _require(len(raw) >= 1 + 16 + 32 and raw[0] == 1, "invalid_dispatch_token")
        body, supplied = raw[:-32], raw[-32:]
        expected = hmac.new(
            self._mac,
            dispatch_id.encode("ascii") + b"\x00" + body,
            hashlib.sha256,
        ).digest()
        _require(hmac.compare_digest(supplied, expected), "invalid_dispatch_token", 403)
        nonce, cipher = body[1:17], body[17:]
        stream = self._stream(nonce, len(cipher))
        plain = bytes(left ^ right for left, right in zip(cipher, stream, strict=True))
        payload = _json_object(plain, "invalid_dispatch_token")
        current = int(time.time()) if now is None else now
        exp = payload.get("exp")
        _require(isinstance(exp, int) and current <= exp <= current + TOKEN_TTL_SECONDS + 60, "dispatch_token_expired", 403)
        dedupe = payload.get("dedupe")
        _require(isinstance(dedupe, dict), "invalid_dispatch_token")
        _require(hmac.compare_digest(self.dispatch_id(dedupe), dispatch_id), "invalid_dispatch_id", 403)
        return payload


class AgentStateClient:
    def __init__(self, url: str, secret_key: str, opener: Any = urllib.request.urlopen) -> None:
        self._url = url.rstrip("/")
        self._key = secret_key
        self._opener = opener

    def _rpc(self, name: str, args: Mapping[str, object]) -> dict[str, Any]:
        _require(re.fullmatch(r"[a-z_]{3,64}", name) is not None, "invalid_rpc", 500)
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

    def register(self, registration: Mapping[str, object]) -> dict[str, Any]:
        return self._rpc("register_ci_run", {"p_registration": dict(registration)})

    def transition(self, ci_run_id: str, transition: Mapping[str, object]) -> dict[str, Any]:
        return self._rpc(
            "transition_ci_run",
            {"p_ci_run_id": _uuid(ci_run_id), "p_transition": dict(transition)},
        )

    def get(self, project_key: str, ci_run_id: str) -> dict[str, Any]:
        return self._rpc(
            "get_ci_run",
            {"p_project_key": _safe_project(project_key), "p_ci_run_id": _uuid(ci_run_id)},
        )

    def list(self, project_key: str, limit: int = 100) -> dict[str, Any]:
        _require(isinstance(limit, int) and 1 <= limit <= 100, "invalid_list_limit")
        return self._rpc(
            "list_ci_runs",
            {"p_project_key": _safe_project(project_key), "p_limit": limit},
        )


class GitHubAppClient:
    def __init__(self, app_id: int, private_key: str, opener: Any = urllib.request.urlopen) -> None:
        self._app_id = app_id
        self._private_key = private_key
        self._opener = opener

    def _jwt(self, now: int | None = None) -> str:
        current = int(time.time()) if now is None else now
        header = _b64url(_canonical({"alg": "RS256", "typ": "JWT"}))
        payload = _b64url(
            _canonical({"iat": current - 30, "exp": current + 8 * 60, "iss": str(self._app_id)})
        )
        signing_input = f"{header}.{payload}".encode("ascii")
        key = self._private_key
        if "\n" not in key and "\\n" in key:
            key = key.replace("\\n", "\n")
        _require("PRIVATE KEY-----" in key, "invalid_github_app_key", 500)
        try:
            with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=True) as handle:
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
        _require(completed.returncode == 0 and bool(completed.stdout), "github_app_signing_failed", 500)
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
        authorization = token if token is not None else self._jwt()
        data = None if body is None else _canonical(dict(body))
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

    def installation_for_repository(self, repository: str) -> int:
        repository = _safe_repository(repository)
        _status, value = self._request("GET", f"/repos/{repository}/installation")
        _require(isinstance(value, dict) and isinstance(value.get("id"), int), "github_installation_missing", 502)
        return int(value["id"])

    def installation_token(self, installation_id: int) -> str:
        _require(isinstance(installation_id, int) and installation_id > 0, "invalid_installation", 500)
        _status, value = self._request(
            "POST",
            f"/app/installations/{installation_id}/access_tokens",
            body={},
            expected=(201,),
        )
        _require(isinstance(value, dict) and isinstance(value.get("token"), str), "github_token_missing", 502)
        return value["token"]

    def repository_token(self, repository: str) -> str:
        return self.installation_token(self.installation_for_repository(repository))

    def get_commit(self, repository: str, ref: str, token: str) -> dict[str, Any]:
        repository = _safe_repository(repository)
        ref = _safe_ref(ref)
        encoded = urllib.parse.quote(ref, safe="")
        _status, value = self._request("GET", f"/repos/{repository}/commits/{encoded}", token=token)
        _require(isinstance(value, dict), "github_commit_missing", 502)
        return value

    def get_private_config(self, repository: str, sha: str, token: str) -> dict[str, Any]:
        repository = _safe_repository(repository)
        sha = _safe_sha(sha)
        path = urllib.parse.quote(PRIVATE_CONFIG_PATH, safe="/")
        query = urllib.parse.urlencode({"ref": sha})
        _status, value = self._request(
            "GET", f"/repos/{repository}/contents/{path}?{query}", token=token
        )
        _require(isinstance(value, dict), "private_ci_config_missing", 422)
        encoded = value.get("content")
        _require(isinstance(encoded, str), "private_ci_config_invalid", 422)
        try:
            raw = base64.b64decode(encoded, validate=False)
        except (ValueError, binascii.Error):
            raise BrokerError("private_ci_config_invalid", 422) from None
        _require(len(raw) <= MAX_CONFIG_BYTES, "private_ci_config_too_large", 422)
        return _json_object(raw, "private_ci_config_invalid")

    def workflow(self, repository: str, workflow: str, token: str) -> dict[str, Any]:
        repository = _safe_repository(repository)
        workflow_path = urllib.parse.quote(workflow, safe="")
        _status, value = self._request(
            "GET", f"/repos/{repository}/actions/workflows/{workflow_path}", token=token
        )
        _require(isinstance(value, dict) and isinstance(value.get("id"), int), "github_workflow_missing", 502)
        return value

    def dispatch(
        self,
        *,
        repository: str,
        workflow: str,
        ref: str,
        inputs: Mapping[str, str],
        token: str,
    ) -> dict[str, Any] | None:
        repository = _safe_repository(repository)
        workflow_path = urllib.parse.quote(workflow, safe="")
        query = urllib.parse.urlencode({"return_run_details": "true"})
        status, value = self._request(
            "POST",
            f"/repos/{repository}/actions/workflows/{workflow_path}/dispatches?{query}",
            token=token,
            body={"ref": ref, "inputs": dict(inputs)},
            expected=(200, 201, 204),
        )
        if status == 204 or value is None:
            return None
        _require(isinstance(value, dict), "github_dispatch_invalid", 502)
        return value


@dataclass(frozen=True)
class ProductProfile:
    name: str
    workflow_key: str
    capability: str
    workspace: str
    scheme: str
    test_target: str

    def as_payload(self) -> dict[str, str]:
        return {
            "name": self.name,
            "workflow_key": self.workflow_key,
            "capability": self.capability,
            "workspace": self.workspace,
            "scheme": self.scheme,
            "test_target": self.test_target,
        }


@dataclass(frozen=True)
class ProductConfig:
    project_key: str
    profiles: dict[str, ProductProfile]
    automatic: dict[str, str]

    @classmethod
    def parse(cls, value: Mapping[str, object]) -> "ProductConfig":
        allowed = {"schema_version", "project_key", "profiles", "automatic"}
        _require(set(value).issubset(allowed), "private_ci_config_unsupported", 422)
        _require(value.get("schema_version") == 1, "private_ci_config_version", 422)
        project_key = _safe_project(value.get("project_key"))
        raw_profiles = value.get("profiles")
        _require(isinstance(raw_profiles, dict) and 1 <= len(raw_profiles) <= 16, "private_ci_profiles_invalid", 422)
        profiles: dict[str, ProductProfile] = {}
        for raw_name, raw_profile in raw_profiles.items():
            name = _safe_profile(raw_name)
            _require(isinstance(raw_profile, dict), "private_ci_profile_invalid", 422)
            _require(
                set(raw_profile) == {"workflow_key", "capability", "workspace", "scheme", "test_target"},
                "private_ci_profile_invalid",
                422,
            )
            capability = raw_profile.get("capability")
            _require(capability in _SUPPORTED_CAPABILITIES, "private_ci_capability_unsupported", 422)
            profiles[name] = ProductProfile(
                name=name,
                workflow_key=_safe_workflow_key(raw_profile.get("workflow_key")),
                capability=str(capability),
                workspace=_safe_workspace(raw_profile.get("workspace")),
                scheme=_safe_product_scalar(raw_profile.get("scheme"), "invalid_scheme"),
                test_target=_safe_product_scalar(raw_profile.get("test_target"), "invalid_test_target"),
            )
        raw_automatic = value.get("automatic", {})
        _require(isinstance(raw_automatic, dict), "private_ci_automatic_invalid", 422)
        _require(set(raw_automatic).issubset({"push", "tag"}), "private_ci_automatic_invalid", 422)
        automatic: dict[str, str] = {}
        for event, profile_name in raw_automatic.items():
            profile_name = _safe_profile(profile_name)
            _require(profile_name in profiles, "private_ci_automatic_profile_missing", 422)
            automatic[event] = profile_name
        return cls(project_key=project_key, profiles=profiles, automatic=automatic)

    def profile(self, name: str, workflow_key: str | None = None) -> ProductProfile:
        name = _safe_profile(name)
        _require(name in self.profiles, "private_ci_profile_missing", 422)
        profile = self.profiles[name]
        if workflow_key is not None:
            _require(profile.workflow_key == _safe_workflow_key(workflow_key), "workflow_profile_mismatch", 422)
        return profile


class GithubOidcVerifier:
    _DIGEST_INFO_SHA256 = bytes.fromhex("3031300d060960864801650304020105000420")

    def __init__(self, opener: Any = urllib.request.urlopen) -> None:
        self._opener = opener
        self._lock = threading.Lock()
        self._jwks: tuple[float, dict[str, Any]] | None = None

    def _keys(self) -> dict[str, Any]:
        now = time.time()
        with self._lock:
            if self._jwks is not None and self._jwks[0] > now:
                return self._jwks[1]
            request = urllib.request.Request(
                "https://token.actions.githubusercontent.com/.well-known/jwks",
                headers={"Accept": "application/json"},
            )
            _status, value = _http_json(request, opener=self._opener)
            _require(isinstance(value, dict) and isinstance(value.get("keys"), list), "oidc_jwks_invalid", 502)
            by_id = {
                item.get("kid"): item
                for item in value["keys"]
                if isinstance(item, dict) and isinstance(item.get("kid"), str)
            }
            _require(bool(by_id), "oidc_jwks_invalid", 502)
            self._jwks = (now + 10 * 60, by_id)
            return by_id

    @classmethod
    def _verify_rs256(cls, signing_input: bytes, signature: bytes, jwk: Mapping[str, object]) -> bool:
        try:
            n = int.from_bytes(_b64url_decode(str(jwk["n"])), "big")
            e = int.from_bytes(_b64url_decode(str(jwk["e"])), "big")
        except (KeyError, ValueError, TypeError, BrokerError):
            return False
        if n <= 0 or e <= 1:
            return False
        width = (n.bit_length() + 7) // 8
        if len(signature) != width:
            return False
        decoded = pow(int.from_bytes(signature, "big"), e, n).to_bytes(width, "big")
        digest = hashlib.sha256(signing_input).digest()
        expected_tail = cls._DIGEST_INFO_SHA256 + digest
        if not decoded.startswith(b"\x00\x01") or len(decoded) < len(expected_tail) + 11:
            return False
        separator = decoded.find(b"\x00", 2)
        if separator < 10 or any(byte != 0xFF for byte in decoded[2:separator]):
            return False
        return hmac.compare_digest(decoded[separator + 1 :], expected_tail)

    def verify(self, token: str, now: int | None = None) -> dict[str, Any]:
        parts = token.split(".")
        _require(len(parts) == 3, "oidc_invalid", 401)
        try:
            header = _json_object(_b64url_decode(parts[0]), "oidc_invalid")
            claims = _json_object(_b64url_decode(parts[1]), "oidc_invalid")
            signature = _b64url_decode(parts[2])
        except BrokerError:
            raise BrokerError("oidc_invalid", 401) from None
        _require(header.get("alg") == "RS256" and isinstance(header.get("kid"), str), "oidc_invalid", 401)
        jwk = self._keys().get(header["kid"])
        _require(isinstance(jwk, dict), "oidc_unknown_key", 401)
        _require(
            self._verify_rs256(f"{parts[0]}.{parts[1]}".encode("ascii"), signature, jwk),
            "oidc_signature_invalid",
            401,
        )
        current = int(time.time()) if now is None else now
        _require(claims.get("iss") == "https://token.actions.githubusercontent.com", "oidc_issuer_invalid", 401)
        audience = claims.get("aud")
        _require(audience == OIDC_AUDIENCE or (isinstance(audience, list) and OIDC_AUDIENCE in audience), "oidc_audience_invalid", 401)
        _require(isinstance(claims.get("exp"), int) and claims["exp"] >= current - 30, "oidc_expired", 401)
        _require(isinstance(claims.get("iat"), int) and claims["iat"] <= current + 60, "oidc_invalid", 401)
        _require(claims.get("repository") == CENTRAL_REPOSITORY, "oidc_repository_invalid", 403)
        _require(claims.get("ref") == "refs/heads/main", "oidc_ref_invalid", 403)
        _require(claims.get("workflow_ref") == CENTRAL_WORKFLOW_REF, "oidc_workflow_invalid", 403)
        _require(claims.get("event_name") == "workflow_dispatch", "oidc_event_invalid", 403)
        run_id = str(claims.get("run_id", ""))
        _require(run_id.isdigit() and int(run_id) > 0, "oidc_run_invalid", 403)
        return claims


class CiBroker:
    def __init__(
        self,
        config: BrokerConfig,
        *,
        agent_state: AgentStateClient | None = None,
        source_github: GitHubAppClient | None = None,
        dispatch_github: GitHubAppClient | None = None,
        oidc: GithubOidcVerifier | None = None,
    ) -> None:
        self.config = config
        self.agent_state = agent_state or AgentStateClient(config.agent_state_url, config.agent_state_secret_key)
        self.source_github = source_github or GitHubAppClient(config.source_app_id, config.source_app_private_key)
        self.dispatch_github = dispatch_github or GitHubAppClient(config.dispatch_app_id, config.dispatch_app_private_key)
        self.oidc = oidc or GithubOidcVerifier()
        self.envelopes = OpaqueEnvelope(config.source_webhook_secret)

    def _dispatcher_token(self) -> str:
        return self.dispatch_github.repository_token(CENTRAL_REPOSITORY)

    def _central_workflow_id(self) -> int:
        token = self._dispatcher_token()
        return int(self.dispatch_github.workflow(CENTRAL_REPOSITORY, CENTRAL_WORKFLOW, token)["id"])

    def _source_and_profile(
        self,
        *,
        project_key: str,
        repository: str,
        ref: str,
        profile_name: str,
        workflow_key: str,
        requested_sha: str | None,
        installation_id: int | None = None,
    ) -> tuple[str, ProductProfile]:
        source_token = (
            self.source_github.installation_token(installation_id)
            if installation_id is not None
            else self.source_github.repository_token(repository)
        )
        commit = self.source_github.get_commit(repository, requested_sha or ref, source_token)
        sha = _safe_sha(commit.get("sha"))
        if requested_sha:
            _require(sha == _safe_sha(requested_sha), "requested_source_mismatch", 409)
        config = ProductConfig.parse(self.source_github.get_private_config(repository, sha, source_token))
        _require(config.project_key == _safe_project(project_key), "project_config_mismatch", 409)
        profile = config.profile(profile_name, workflow_key)
        return sha, profile

    def _dispatch_payload(
        self,
        *,
        kind: str,
        project_key: str,
        repository: str,
        ref: str,
        source_sha: str,
        profile: ProductProfile,
        trigger_kind: str,
        ci_run_id: str | None = None,
    ) -> tuple[str, str]:
        dedupe: dict[str, object] = {
            "kind": kind,
            "project_key": project_key,
            "repository": repository,
            "ref": ref,
            "source_sha": source_sha,
            "workflow_key": profile.workflow_key,
            "test_profile": profile.name,
            "trigger_kind": trigger_kind,
        }
        if ci_run_id is not None:
            dedupe["ci_run_id"] = _uuid(ci_run_id)
        dispatch_id = self.envelopes.dispatch_id(dedupe)
        payload: dict[str, object] = {
            "dedupe": dedupe,
            "profile": profile.as_payload(),
            "exp": int(time.time()) + TOKEN_TTL_SECONDS,
        }
        token = self.envelopes.seal(dispatch_id, payload)
        return dispatch_id, token

    def _dispatch_central(self, dispatch_id: str, dispatch_token: str) -> dict[str, Any] | None:
        token = self._dispatcher_token()
        return self.dispatch_github.dispatch(
            repository=CENTRAL_REPOSITORY,
            workflow=CENTRAL_WORKFLOW,
            ref=CENTRAL_REF,
            inputs={"dispatch_id": dispatch_id, "dispatch_token": dispatch_token},
            token=token,
        )

    @staticmethod
    def _run_from_result(value: Mapping[str, object]) -> dict[str, Any]:
        run = value.get("run")
        _require(value.get("ok") is True and isinstance(run, dict), "agent_state_ci_rejected", 409)
        return run

    def _fail_claimed(self, ci_run_id: str, code: str) -> None:
        try:
            self.agent_state.transition(ci_run_id, {"status": "failed", "error_summary": code[:128]})
        except BrokerError:
            pass

    def handle_agent_state_webhook(self, raw: bytes, headers: Mapping[str, str]) -> dict[str, object]:
        supplied = _header(headers, "X-StreamScape-Webhook-Secret")
        _require(
            bool(supplied)
            and hmac.compare_digest(supplied.encode(), self.config.agent_state_webhook_secret.encode()),
            "agent_state_webhook_unauthorized",
            401,
        )
        payload = _json_object(raw)
        _require(payload.get("type") == "INSERT", "agent_state_webhook_ignored", 202)
        _require(payload.get("schema") == "agent_private" and payload.get("table") == "ci_runs", "agent_state_webhook_ignored", 202)
        record = payload.get("record")
        _require(isinstance(record, dict), "agent_state_webhook_invalid")
        if record.get("origin") != "agent_request" or record.get("status") != "requested":
            return {"ok": True, "ignored": True}
        ci_run_id = _uuid(record.get("ci_run_id"))
        claim = self.agent_state.claim(ci_run_id)
        run = self._run_from_result(claim)
        if claim.get("replayed") is True:
            return {"ok": True, "replayed": True}
        try:
            project_key = _safe_project(run.get("project_key"))
            repository = _safe_repository(run.get("repository"))
            ref = _safe_ref(run.get("ref"))
            workflow_key = _safe_workflow_key(run.get("workflow_key"))
            profile_name = _safe_profile(run.get("test_profile"))
            inputs = run.get("inputs")
            _require(inputs in ({}, None), "agent_request_inputs_unsupported", 422)
            requested_sha = run.get("requested_source_sha")
            if requested_sha is not None:
                requested_sha = _safe_sha(requested_sha)
            source_sha, profile = self._source_and_profile(
                project_key=project_key,
                repository=repository,
                ref=ref,
                profile_name=profile_name,
                workflow_key=workflow_key,
                requested_sha=requested_sha,
            )
            dispatch_id, token = self._dispatch_payload(
                kind="agent_request",
                project_key=project_key,
                repository=repository,
                ref=ref,
                source_sha=source_sha,
                profile=profile,
                trigger_kind="agent_dispatch",
                ci_run_id=ci_run_id,
            )
            details = self._dispatch_central(dispatch_id, token)
            if details is not None:
                run_id = details.get("id") or details.get("run_id")
                if isinstance(run_id, int) and run_id > 0:
                    workflow_id = details.get("workflow_id")
                    if not isinstance(workflow_id, int):
                        workflow_id = self._central_workflow_id()
                    attempt = details.get("run_attempt", 1)
                    if not isinstance(attempt, int) or attempt < 1:
                        attempt = 1
                    self.agent_state.transition(
                        ci_run_id,
                        {
                            "status": "queued",
                            "resolved_source_sha": source_sha,
                            "external_workflow_id": workflow_id,
                            "external_run_id": run_id,
                            "external_run_attempt": attempt,
                            "external_run_url": f"https://github.com/{CENTRAL_REPOSITORY}/actions/runs/{run_id}",
                        },
                    )
            return {"ok": True, "dispatched": True}
        except BrokerError as error:
            self._fail_claimed(ci_run_id, error.code)
            raise

    def handle_github_webhook(self, raw: bytes, headers: Mapping[str, str]) -> dict[str, object]:
        signature = _header(headers, "X-Hub-Signature-256")
        expected = "sha256=" + hmac.new(
            self.config.source_webhook_secret.encode("utf-8"), raw, hashlib.sha256
        ).hexdigest()
        _require(signature and hmac.compare_digest(signature, expected), "github_webhook_unauthorized", 401)
        event = _header(headers, "X-GitHub-Event")
        if event != "push":
            return {"ok": True, "ignored": True}
        payload = _json_object(raw)
        if payload.get("deleted") is True:
            return {"ok": True, "ignored": True}
        repository_value = payload.get("repository")
        installation = payload.get("installation")
        _require(isinstance(repository_value, dict) and isinstance(installation, dict), "github_webhook_invalid")
        repository = _safe_repository(repository_value.get("full_name"))
        ref = _safe_ref(payload.get("ref"))
        source_sha = _safe_sha(payload.get("after"))
        _require(source_sha != "0" * 40, "github_webhook_ignored", 202)
        installation_id = installation.get("id")
        _require(isinstance(installation_id, int) and installation_id > 0, "github_webhook_invalid")
        source_token = self.source_github.installation_token(installation_id)
        config = ProductConfig.parse(self.source_github.get_private_config(repository, source_sha, source_token))
        if ref.startswith("refs/tags/"):
            trigger_kind = "tag"
        elif ref.startswith("refs/heads/"):
            trigger_kind = "push"
        else:
            return {"ok": True, "ignored": True}
        profile_name = config.automatic.get(trigger_kind)
        if not profile_name:
            return {"ok": True, "ignored": True}
        head_commit = payload.get("head_commit")
        message = head_commit.get("message", "") if isinstance(head_commit, dict) else ""
        if not message:
            commit = self.source_github.get_commit(repository, source_sha, source_token)
            nested = commit.get("commit")
            message = nested.get("message", "") if isinstance(nested, dict) else ""
        if isinstance(message, str) and any(marker in message.lower() for marker in _SKIP_MARKERS):
            return {"ok": True, "ignored": True, "skip_ci": True}
        profile = config.profile(profile_name)
        dispatch_id, token = self._dispatch_payload(
            kind="workflow_registration",
            project_key=config.project_key,
            repository=repository,
            ref=ref,
            source_sha=source_sha,
            profile=profile,
            trigger_kind=trigger_kind,
        )
        self._dispatch_central(dispatch_id, token)
        return {"ok": True, "dispatched": True}

    def _action_identity(self, raw: bytes, headers: Mapping[str, str]) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        authorization = _header(headers, "Authorization")
        _require(authorization.startswith("Bearer "), "oidc_required", 401)
        claims = self.oidc.verify(authorization[7:])
        request = _json_object(raw)
        dispatch_id = request.get("dispatch_id")
        dispatch_token = request.get("dispatch_token")
        _require(isinstance(dispatch_id, str) and 20 <= len(dispatch_id) <= 128, "invalid_dispatch_id")
        _require(isinstance(dispatch_token, str), "invalid_dispatch_token")
        envelope = self.envelopes.open(dispatch_id, dispatch_token)
        return claims, request, envelope

    def _duplicate(self, envelope: Mapping[str, Any], run_id: int) -> bool:
        dedupe = envelope["dedupe"]
        kind = dedupe.get("kind")
        project_key = _safe_project(dedupe.get("project_key"))
        if kind == "agent_request":
            ci_run_id = _uuid(dedupe.get("ci_run_id"))
            result = self.agent_state.get(project_key, ci_run_id)
            if result.get("ok") is not True or not isinstance(result.get("run"), dict):
                return True
            run = result["run"]
            external = run.get("external_run_id")
            return isinstance(external, int) and external > 0 and external != run_id
        if kind == "workflow_registration":
            listed = self.agent_state.list(project_key, 100)
            if listed.get("ok") is not True or not isinstance(listed.get("runs"), list):
                return False
            for candidate in listed["runs"]:
                if not isinstance(candidate, dict) or candidate.get("origin") != "workflow_registration":
                    continue
                if (
                    candidate.get("repository") == dedupe.get("repository")
                    and candidate.get("ref") == dedupe.get("ref")
                    and candidate.get("resolved_source_sha") == dedupe.get("source_sha")
                    and candidate.get("workflow_key") == dedupe.get("workflow_key")
                    and candidate.get("test_profile") == dedupe.get("test_profile")
                    and candidate.get("trigger_kind") == dedupe.get("trigger_kind")
                ):
                    external = candidate.get("external_run_id")
                    if isinstance(external, int) and external != run_id:
                        return True
            return False
        raise BrokerError("invalid_dispatch_kind")

    def action_route(self, raw: bytes, headers: Mapping[str, str]) -> dict[str, object]:
        claims, _request, envelope = self._action_identity(raw, headers)
        run_id = int(str(claims["run_id"]))
        profile = envelope.get("profile")
        _require(isinstance(profile, dict), "invalid_dispatch_profile")
        capability = profile.get("capability")
        _require(capability in _SUPPORTED_CAPABILITIES, "unsupported_capability", 422)
        return {
            "ok": True,
            "capability": capability,
            "duplicate": self._duplicate(envelope, run_id),
        }

    def action_start(self, raw: bytes, headers: Mapping[str, str]) -> dict[str, object]:
        claims, request, envelope = self._action_identity(raw, headers)
        run_id = int(str(claims["run_id"]))
        _require(not self._duplicate(envelope, run_id), "duplicate_dispatch", 409)
        run_attempt = request.get("run_attempt")
        _require(isinstance(run_attempt, int) and 1 <= run_attempt <= 1000, "invalid_run_attempt")
        dedupe = envelope["dedupe"]
        profile_raw = envelope["profile"]
        profile = ProductProfile(
            name=_safe_profile(profile_raw.get("name")),
            workflow_key=_safe_workflow_key(profile_raw.get("workflow_key")),
            capability=str(profile_raw.get("capability")),
            workspace=_safe_workspace(profile_raw.get("workspace")),
            scheme=_safe_product_scalar(profile_raw.get("scheme"), "invalid_scheme"),
            test_target=_safe_product_scalar(profile_raw.get("test_target"), "invalid_test_target"),
        )
        repository = _safe_repository(dedupe.get("repository"))
        project_key = _safe_project(dedupe.get("project_key"))
        ref = _safe_ref(dedupe.get("ref"))
        source_sha = _safe_sha(dedupe.get("source_sha"))
        workflow_id = self._central_workflow_id()
        run_url = f"https://github.com/{CENTRAL_REPOSITORY}/actions/runs/{run_id}"
        kind = dedupe.get("kind")
        if kind == "agent_request":
            ci_run_id = _uuid(dedupe.get("ci_run_id"))
            transition = self.agent_state.transition(
                ci_run_id,
                {
                    "status": "running",
                    "resolved_source_sha": source_sha,
                    "external_workflow_id": workflow_id,
                    "external_run_id": run_id,
                    "external_run_attempt": run_attempt,
                    "external_run_url": run_url,
                },
            )
            self._run_from_result(transition)
        elif kind == "workflow_registration":
            registered = self.agent_state.register(
                {
                    "project_key": project_key,
                    "repository": repository,
                    "ref": ref,
                    "workflow_key": profile.workflow_key,
                    "test_profile": profile.name,
                    "trigger_kind": str(dedupe.get("trigger_kind")),
                    "inputs": {},
                    "resolved_source_sha": source_sha,
                    "external_workflow_id": workflow_id,
                    "external_run_id": run_id,
                    "external_run_attempt": run_attempt,
                    "external_run_url": run_url,
                }
            )
            registered_run = self._run_from_result(registered)
            ci_run_id = _uuid(registered_run.get("ci_run_id"))
        else:
            raise BrokerError("invalid_dispatch_kind")
        source_token = self.source_github.repository_token(repository)
        return {
            "ok": True,
            "ci_run_id": ci_run_id,
            "repository": repository,
            "source_sha": source_sha,
            "source_token": source_token,
            "capability": profile.capability,
            "workspace": profile.workspace,
            "scheme": profile.scheme,
            "test_target": profile.test_target,
        }

    def action_finish(self, raw: bytes, headers: Mapping[str, str]) -> dict[str, object]:
        claims, request, envelope = self._action_identity(raw, headers)
        run_id = int(str(claims["run_id"]))
        dedupe = envelope["dedupe"]
        project_key = _safe_project(dedupe.get("project_key"))
        ci_run_id = _uuid(request.get("ci_run_id"))
        state = self.agent_state.get(project_key, ci_run_id)
        run = self._run_from_result(state)
        _require(run.get("external_run_id") == run_id, "run_identity_mismatch", 409)
        status = request.get("status")
        _require(status in _TERMINAL, "invalid_terminal_status")
        transition: dict[str, object] = {"status": status}
        if status == "succeeded":
            transition["summary"] = "Central validation completed"
        else:
            transition["error_summary"] = str(request.get("error_summary") or "central_validation_failed")[:256]
        logs_status = request.get("logs_status")
        if logs_status in ("uploaded", "failed", "missing"):
            transition["logs_status"] = logs_status
        object_key = request.get("logs_object_key")
        digest = request.get("logs_sha256")
        if logs_status == "uploaded":
            _require(isinstance(object_key, str) and object_key.startswith(f"ci-diagnostics/{ci_run_id}/"), "invalid_logs_object_key")
            _require(isinstance(digest, str) and _SHA256.fullmatch(digest) is not None, "invalid_logs_digest")
            transition["logs_object_key"] = object_key
            transition["logs_sha256"] = digest
        result = self.agent_state.transition(ci_run_id, transition)
        self._run_from_result(result)
        return {"ok": True}

    def diagnostic(self, project_key: str, ci_run_id: str) -> bytes:
        project_key = _safe_project(urllib.parse.unquote(project_key))
        ci_run_id = _uuid(ci_run_id)
        state = self.agent_state.get(project_key, ci_run_id)
        run = self._run_from_result(state)
        _require(run.get("logs_status") == "uploaded", "diagnostic_unavailable", 404)
        object_key = run.get("logs_object_key")
        digest = run.get("logs_sha256")
        _require(isinstance(object_key, str) and object_key.startswith(f"ci-diagnostics/{ci_run_id}/"), "diagnostic_unavailable", 404)
        _require(isinstance(digest, str) and _SHA256.fullmatch(digest) is not None, "diagnostic_unavailable", 404)
        try:
            compressed = download_private_diagnostic(
                object_key=object_key,
                expected_sha256=digest,
                account_id=self.config.r2_account_id,
                bucket=self.config.r2_bucket,
                access_key_id=self.config.r2_read_access_key_id,
                secret_access_key=self.config.r2_read_secret_access_key,
            )
            raw = gzip.decompress(compressed)
        except (R2DiagnosticError, OSError):
            raise BrokerError("diagnostic_unavailable", 502) from None
        _require(len(raw) <= 50 * 1024 * 1024, "diagnostic_too_large", 502)
        return raw


class _Handler(BaseHTTPRequestHandler):
    server_version = "central-hooks"
    sys_version = ""

    @property
    def broker(self) -> CiBroker:
        return self.server.broker  # type: ignore[attr-defined]

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def _send_json(self, status: int, value: Mapping[str, object]) -> None:
        raw = _canonical(dict(value))
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _body(self) -> bytes:
        length_text = self.headers.get("Content-Length", "")
        _require(length_text.isdigit(), "content_length_required", 411)
        length = int(length_text)
        _require(0 <= length <= MAX_BODY_BYTES, "request_too_large", 413)
        raw = self.rfile.read(length)
        _require(len(raw) == length, "request_truncated")
        return raw

    def do_GET(self) -> None:  # noqa: N802
        try:
            parsed = urllib.parse.urlsplit(self.path)
            if parsed.path == "/healthz":
                self._send_json(200, {"ok": True})
                return
            parts = parsed.path.split("/")
            if len(parts) == 5 and parts[1] == "diagnostics":
                raw = self.broker.diagnostic(parts[2], parts[3] if parts[4] == "" else parts[3])
                # Compatibility with a trailing slash is deliberately not supported.
                raise BrokerError("not_found", 404)
            if len(parts) == 4 and parts[1] == "diagnostics":
                raw = self.broker.diagnostic(parts[2], parts[3])
                self.send_response(200)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
                return
            raise BrokerError("not_found", 404)
        except BrokerError as error:
            self._send_json(error.status, {"ok": False, "code": error.code})

    def do_POST(self) -> None:  # noqa: N802
        try:
            raw = self._body()
            headers = {key: value for key, value in self.headers.items()}
            path = urllib.parse.urlsplit(self.path).path
            if path == "/hooks/agent-state":
                value = self.broker.handle_agent_state_webhook(raw, headers)
            elif path == "/hooks/github":
                value = self.broker.handle_github_webhook(raw, headers)
            elif path == "/actions/route":
                value = self.broker.action_route(raw, headers)
            elif path == "/actions/start":
                value = self.broker.action_start(raw, headers)
            elif path == "/actions/finish":
                value = self.broker.action_finish(raw, headers)
            else:
                raise BrokerError("not_found", 404)
            self._send_json(200, value)
        except BrokerError as error:
            if error.code in {"agent_state_webhook_ignored", "github_webhook_ignored"}:
                self._send_json(202, {"ok": True, "ignored": True})
            else:
                self._send_json(error.status, {"ok": False, "code": error.code})
        except Exception:
            self._send_json(500, {"ok": False, "code": "internal_error"})


class BrokerServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], broker: CiBroker) -> None:
        self.broker = broker
        super().__init__(address, _Handler)


def serve(config: BrokerConfig | None = None) -> None:
    selected = config or BrokerConfig.from_environment()
    broker = CiBroker(selected)
    server = BrokerServer(("0.0.0.0", selected.port), broker)
    server.serve_forever(poll_interval=0.5)


def self_check() -> dict[str, object]:
    envelope = OpaqueEnvelope("synthetic-webhook-secret")
    dedupe = {
        "kind": "agent_request",
        "project_key": "example",
        "repository": "example/repository",
        "ref": "refs/heads/main",
        "source_sha": "a" * 40,
        "workflow_key": "validation.apple",
        "test_profile": "host",
        "trigger_kind": "agent_dispatch",
        "ci_run_id": "00000000-0000-4000-8000-000000000001",
    }
    dispatch_id = envelope.dispatch_id(dedupe)
    token = envelope.seal(
        dispatch_id,
        {
            "dedupe": dedupe,
            "profile": {
                "name": "host",
                "workflow_key": "validation.apple",
                "capability": "apple-host-test",
                "workspace": "Sample.xcworkspace",
                "scheme": "Sample",
                "test_target": "SampleTests/SelectedIntegrationTests",
            },
            "exp": int(time.time()) + 60,
        },
    )
    opened = envelope.open(dispatch_id, token)
    _require(opened["dedupe"] == dedupe, "self_check_failed", 500)
    parsed = ProductConfig.parse(
        {
            "schema_version": 1,
            "project_key": "example",
            "profiles": {
                "host": {
                    "workflow_key": "validation.apple",
                    "capability": "apple-host-test",
                    "workspace": "Sample.xcworkspace",
                    "scheme": "Sample",
                    "test_target": "SampleTests/SelectedIntegrationTests",
                }
            },
            "automatic": {"push": "host"},
        }
    )
    _require(parsed.profile("host").capability == "apple-host-test", "self_check_failed", 500)
    return {"ok": True}


__all__ = (
    "AgentStateClient",
    "BrokerConfig",
    "BrokerError",
    "BrokerServer",
    "CENTRAL_REPOSITORY",
    "CENTRAL_WORKFLOW",
    "CiBroker",
    "GithubOidcVerifier",
    "GitHubAppClient",
    "OIDC_AUDIENCE",
    "OpaqueEnvelope",
    "ProductConfig",
    "ProductProfile",
    "self_check",
    "serve",
)
