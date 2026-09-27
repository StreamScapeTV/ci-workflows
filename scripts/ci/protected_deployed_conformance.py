#!/usr/bin/env python3
"""Bounded protected deployed-conformance transport for Repository CI.

The public repository-ci contract authorizes only the generic capability name.
A private Agent State descriptor selects one tracked no-argv Python entrypoint and
maps a finite protected bundle into repository-owned environment variable names.
Confidential targets/scenarios/credentials arrive only through fixed Central
secrets, are materialized as mode-0600 files, and never become workflow inputs.
"""

from __future__ import annotations

import argparse
import base64
import gzip
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys
import tempfile
from typing import Any, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


CAPABILITY = "protected_deployed_conformance"
CONFIG_KEY = "protected_deployed_conformance"
SCHEMA_VERSION = 1
MAX_SCENARIO_BYTES = 512 * 1024
MAX_CHILD_TEXT_BYTES = 4 * 1024 * 1024
MAX_SETUP_CREDENTIAL_BYTES = 4096
MAX_RESET_BODY_BYTES = 4096
RUN_TIMEOUT_SECONDS = 30 * 60

_SHA40 = re.compile(r"^[0-9a-f]{40}$")
_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_SEMVER = re.compile(r"^(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)$")
_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_ENV_NAME = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")

PROJECTION_FIELDS = (
    "httpsTarget",
    "wssTarget",
    "httpScenarioPath",
    "websocketScenarioPath",
    "setupCredential",
    "releaseVersion",
    "releaseSourceSha",
    "releaseImageDigest",
    "releaseChartDigest",
    "deployedImageDigest",
    "deployedChartDigest",
    "environmentSha256",
    "runId",
    "liveFlag",
)

IDENTITY_FIELDS = (
    "releaseVersion",
    "releaseSourceSha",
    "releaseImageDigest",
    "releaseChartDigest",
    "deployedImageDigest",
    "deployedChartDigest",
    "environmentSha256",
)

FORBIDDEN_ENV_NAMES = {
    "PATH",
    "HOME",
    "SHELL",
    "PYTHONPATH",
    "GITHUB_ENV",
    "GITHUB_OUTPUT",
    "GITHUB_TOKEN",
}
FORBIDDEN_ENV_PREFIXES = (
    "CI_",
    "GITHUB_",
    "RUNNER_",
    "ACTIONS_",
    "AGENT_STATE_",
    "GOOGLE_DRIVE_",
    "TS_OAUTH_",
    "REGISTRY_",
    "LD_",
    "DYLD_",
)

SECRET_ENV = {
    "httpsTarget": "CI_PROTECTED_DEPLOYED_HTTPS_TARGET",
    "wssTarget": "CI_PROTECTED_DEPLOYED_WSS_TARGET",
    "httpScenarioGzipBase64": "CI_PROTECTED_DEPLOYED_HTTP_SCENARIO_GZIP_BASE64",
    "websocketScenarioGzipBase64": "CI_PROTECTED_DEPLOYED_WEBSOCKET_SCENARIO_GZIP_BASE64",
    "setupCredential": "CI_PROTECTED_DEPLOYED_SETUP_CREDENTIAL",
}


class ProtectedConformanceError(RuntimeError):
    pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[override]
        return None


def _loads_object(raw: str, label: str) -> dict[str, Any]:
    def reject_duplicates(pairs):
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate key: {key}")
            result[key] = value
        return result

    try:
        value = json.loads(raw, object_pairs_hook=reject_duplicates)
    except (json.JSONDecodeError, ValueError) as exc:
        raise ProtectedConformanceError(f"{label} is not one unambiguous JSON object") from exc
    if not isinstance(value, dict):
        raise ProtectedConformanceError(f"{label} must be one JSON object")
    return value


def _read_object(path: Path, label: str) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise ProtectedConformanceError(f"{label} must be one regular non-symlink file")
    return _loads_object(path.read_text(encoding="utf-8"), label)


def _write_private(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    fd, temporary_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        os.chmod(temporary_name, 0o600)
        os.replace(temporary_name, path)
    finally:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
    if path.is_symlink() or not path.is_file() or stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise ProtectedConformanceError("protected material permissions are unsafe")


def _safe_relative_python(path_raw: Any) -> str:
    if not isinstance(path_raw, str) or len(path_raw) > 240:
        raise ProtectedConformanceError("protected entrypoint path is invalid")
    path = PurePosixPath(path_raw)
    if path.is_absolute() or ".." in path.parts or "." in path.parts:
        raise ProtectedConformanceError("protected entrypoint must be one bounded relative path")
    if not path.parts or path.parts[0] not in {"scripts", "tools"} or path.suffix != ".py":
        raise ProtectedConformanceError("protected entrypoint must be one tracked scripts/tools Python file")
    if any(not part or part.startswith("-") for part in path.parts):
        raise ProtectedConformanceError("protected entrypoint path contains an unsafe component")
    return path.as_posix()


def _safe_env_name(value: Any, label: str) -> str:
    if not isinstance(value, str) or _ENV_NAME.fullmatch(value) is None:
        raise ProtectedConformanceError(f"{label} environment name is invalid")
    if value in FORBIDDEN_ENV_NAMES or value.startswith(FORBIDDEN_ENV_PREFIXES):
        raise ProtectedConformanceError(f"{label} environment name overlaps Central-owned state")
    return value


def _safe_url(value: str, *, websocket: bool) -> str:
    if not isinstance(value, str) or not value or len(value) > 2048 or "\n" in value or "\r" in value:
        raise ProtectedConformanceError("protected deployed target is invalid")
    parsed = urlsplit(value)
    allowed = {"wss"} if websocket else {"https"}
    if parsed.hostname in {"127.0.0.1", "localhost", "::1"}:
        allowed |= {"ws"} if websocket else {"http"}
    if parsed.scheme not in allowed or not parsed.hostname:
        raise ProtectedConformanceError("protected deployed target uses an unsafe transport")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ProtectedConformanceError("protected deployed target contains unexpected authority material")
    return value.rstrip("/") if not websocket else value


def _decode_scenario(raw: str, label: str) -> tuple[bytes, dict[str, Any]]:
    if not raw or len(raw) > 64 * 1024:
        raise ProtectedConformanceError(f"{label} protected scenario transport is missing or oversized")
    try:
        compressed = base64.b64decode(raw, validate=True)
        payload = gzip.decompress(compressed)
    except Exception as exc:
        raise ProtectedConformanceError(f"{label} protected scenario transport is invalid") from exc
    if not 2 <= len(payload) <= MAX_SCENARIO_BYTES:
        raise ProtectedConformanceError(f"{label} protected scenario is outside the reviewed size bound")
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ProtectedConformanceError(f"{label} protected scenario is not UTF-8 JSON") from exc
    document = _loads_object(text, f"{label} protected scenario")
    if document.get("schema_version") != 1:
        raise ProtectedConformanceError(f"{label} protected scenario schema_version must be 1")
    return payload if payload.endswith(b"\n") else payload + b"\n", document


def _validate_identity(value: Any) -> dict[str, str]:
    if not isinstance(value, dict) or set(value) != set(IDENTITY_FIELDS):
        raise ProtectedConformanceError("protected deployed identity descriptor is incomplete")
    identity = {key: value[key] for key in IDENTITY_FIELDS}
    if not isinstance(identity["releaseVersion"], str) or _SEMVER.fullmatch(identity["releaseVersion"]) is None:
        raise ProtectedConformanceError("protected release version is invalid")
    if not isinstance(identity["releaseSourceSha"], str) or _SHA40.fullmatch(identity["releaseSourceSha"]) is None:
        raise ProtectedConformanceError("protected release source identity is invalid")
    for key in ("releaseImageDigest", "releaseChartDigest", "deployedImageDigest", "deployedChartDigest"):
        if not isinstance(identity[key], str) or _SHA256.fullmatch(identity[key]) is None:
            raise ProtectedConformanceError(f"protected {key} is invalid")
    if not isinstance(identity["environmentSha256"], str) or _HEX64.fullmatch(identity["environmentSha256"]) is None:
        raise ProtectedConformanceError("protected environment identity is invalid")
    return identity  # type: ignore[return-value]


def _validate_reset(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {"request", "verify"}:
        raise ProtectedConformanceError("protected reset descriptor is incomplete")
    request = value.get("request")
    verify = value.get("verify")
    if not isinstance(request, dict) or set(request) != {
        "method", "pathTemplate", "subject", "authorization", "body", "expectedStatus"
    }:
        raise ProtectedConformanceError("protected reset request is invalid")
    if request.get("method") not in {"POST", "PATCH", "DELETE"}:
        raise ProtectedConformanceError("protected reset request method is invalid")
    path_template = request.get("pathTemplate")
    if (
        not isinstance(path_template, str)
        or not path_template.startswith("/")
        or len(path_template) > 256
        or path_template.count("{subject}") != 1
        or "?" in path_template
        or "#" in path_template
    ):
        raise ProtectedConformanceError("protected reset path template is invalid")
    subject = request.get("subject")
    if not isinstance(subject, dict) or set(subject) != {"scenario", "jsonPointer"}:
        raise ProtectedConformanceError("protected reset subject selector is invalid")
    if subject.get("scenario") not in {"http", "websocket"}:
        raise ProtectedConformanceError("protected reset subject scenario is invalid")
    _validate_json_pointer(subject.get("jsonPointer"), "reset subject")
    if request.get("authorization") != "setupCredential":
        raise ProtectedConformanceError("protected reset authorization is invalid")
    body = request.get("body")
    if not isinstance(body, dict) or len(json.dumps(body, separators=(",", ":")).encode()) > MAX_RESET_BODY_BYTES:
        raise ProtectedConformanceError("protected reset request body is invalid")
    if request.get("expectedStatus") != 200:
        raise ProtectedConformanceError("protected reset status is outside the reviewed contract")

    if not isinstance(verify, dict) or set(verify) != {
        "path", "authorization", "expectedStatus", "jsonPointer", "equals"
    }:
        raise ProtectedConformanceError("protected reset verification is invalid")
    path = verify.get("path")
    if not isinstance(path, str) or not path.startswith("/") or len(path) > 256 or "?" in path or "#" in path:
        raise ProtectedConformanceError("protected reset verification path is invalid")
    authorization = verify.get("authorization")
    if not isinstance(authorization, dict) or set(authorization) != {"scenario", "jsonPointer"}:
        raise ProtectedConformanceError("protected reset verification authorization is invalid")
    if authorization.get("scenario") not in {"http", "websocket"}:
        raise ProtectedConformanceError("protected reset verification scenario is invalid")
    _validate_json_pointer(authorization.get("jsonPointer"), "reset verification authorization")
    if verify.get("expectedStatus") != 200:
        raise ProtectedConformanceError("protected reset verification status is outside the reviewed contract")
    _validate_json_pointer(verify.get("jsonPointer"), "reset verification")
    if isinstance(verify.get("equals"), (dict, list)):
        raise ProtectedConformanceError("protected reset verification equality must be scalar")
    return value


def _validate_json_pointer(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.startswith("/") or len(value) > 256 or "\n" in value or "\r" in value:
        raise ProtectedConformanceError(f"protected {label} JSON pointer is invalid")
    return value


def _pointer(document: Any, pointer: str) -> Any:
    current = document
    for raw in pointer.split("/")[1:]:
        token = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(current, Mapping):
            if token not in current:
                raise ProtectedConformanceError("protected reset JSON pointer is absent")
            current = current[token]
        elif isinstance(current, list) and token.isdigit() and int(token) < len(current):
            current = current[int(token)]
        else:
            raise ProtectedConformanceError("protected reset JSON pointer is invalid for the selected value")
    return current


def _validate_config(project_state: dict[str, Any], *, repository: str, operation: str) -> dict[str, Any]:
    value = project_state.get(CONFIG_KEY)
    if not isinstance(value, dict):
        raise ProtectedConformanceError("trusted protected deployed-conformance descriptor is absent")
    if set(value) != {
        "schemaVersion", "repository", "operation", "entrypoint", "environmentProjection", "identity", "reset"
    }:
        raise ProtectedConformanceError("trusted protected deployed-conformance descriptor has unsupported fields")
    if value.get("schemaVersion") != SCHEMA_VERSION:
        raise ProtectedConformanceError("trusted protected deployed-conformance descriptor version is invalid")
    if value.get("repository") != repository:
        raise ProtectedConformanceError("trusted protected deployed-conformance descriptor is bound to a different repository")
    if value.get("operation") != operation or operation != "full":
        raise ProtectedConformanceError("protected deployed conformance is bounded to repository full validation")
    entrypoint = value.get("entrypoint")
    if not isinstance(entrypoint, dict) or set(entrypoint) != {"kind", "path"} or entrypoint.get("kind") != "python":
        raise ProtectedConformanceError("trusted protected entrypoint descriptor is invalid")
    entrypoint_path = _safe_relative_python(entrypoint.get("path"))
    projection = value.get("environmentProjection")
    if not isinstance(projection, dict) or set(projection) != set(PROJECTION_FIELDS):
        raise ProtectedConformanceError("protected environment projection is incomplete")
    normalized_projection = {
        field: _safe_env_name(projection.get(field), field) for field in PROJECTION_FIELDS
    }
    if len(set(normalized_projection.values())) != len(normalized_projection):
        raise ProtectedConformanceError("protected environment projection names must be unique")
    identity = _validate_identity(value.get("identity"))
    reset = _validate_reset(value.get("reset"))
    return {
        "entrypoint": entrypoint_path,
        "environmentProjection": normalized_projection,
        "identity": identity,
        "reset": reset,
    }


def _required_secret(name: str, *, max_bytes: int | None = None) -> str:
    value = os.environ.get(name, "")
    if not value:
        raise ProtectedConformanceError("required protected deployed-conformance material is absent")
    encoded = value.encode("utf-8")
    if max_bytes is not None and len(encoded) > max_bytes:
        raise ProtectedConformanceError("protected deployed-conformance material exceeds its reviewed bound")
    if "\x00" in value:
        raise ProtectedConformanceError("protected deployed-conformance material contains NUL")
    return value


def materialize(args: argparse.Namespace) -> int:
    project_state = _read_object(Path(args.project_state), "Agent State project configuration")
    config = _validate_config(project_state, repository=args.repository, operation=args.operation)
    if _RUN_ID.fullmatch(args.run_id) is None:
        raise ProtectedConformanceError("protected deployed-conformance run identity is invalid")

    https_target = _safe_url(_required_secret(SECRET_ENV["httpsTarget"], max_bytes=2048), websocket=False)
    wss_target = _safe_url(_required_secret(SECRET_ENV["wssTarget"], max_bytes=2048), websocket=True)
    setup_credential = _required_secret(
        SECRET_ENV["setupCredential"], max_bytes=MAX_SETUP_CREDENTIAL_BYTES
    )
    if len(setup_credential) < 16 or "\n" in setup_credential or "\r" in setup_credential:
        raise ProtectedConformanceError("protected setup credential is invalid")
    http_payload, http_document = _decode_scenario(
        _required_secret(SECRET_ENV["httpScenarioGzipBase64"]), "HTTP"
    )
    ws_payload, ws_document = _decode_scenario(
        _required_secret(SECRET_ENV["websocketScenarioGzipBase64"]), "WebSocket"
    )

    root = Path(args.output_root)
    if root.exists() or root.is_symlink():
        raise ProtectedConformanceError("protected material root must not already exist")
    root.mkdir(parents=True, mode=0o700)
    os.chmod(root, 0o700)
    http_path = root / "http-scenario.json"
    ws_path = root / "websocket-scenario.json"
    env_path = root / "child-environment.json"
    metadata_path = root / "metadata.json"
    _write_private(http_path, http_payload)
    _write_private(ws_path, ws_payload)

    identity = config["identity"]
    bundle_values: dict[str, str] = {
        "httpsTarget": https_target,
        "wssTarget": wss_target,
        "httpScenarioPath": str(http_path),
        "websocketScenarioPath": str(ws_path),
        "setupCredential": setup_credential,
        "releaseVersion": identity["releaseVersion"],
        "releaseSourceSha": identity["releaseSourceSha"],
        "releaseImageDigest": identity["releaseImageDigest"],
        "releaseChartDigest": identity["releaseChartDigest"],
        "deployedImageDigest": identity["deployedImageDigest"],
        "deployedChartDigest": identity["deployedChartDigest"],
        "environmentSha256": identity["environmentSha256"],
        "runId": args.run_id,
        "liveFlag": "1",
    }
    child_environment = {
        config["environmentProjection"][field]: bundle_values[field]
        for field in PROJECTION_FIELDS
    }
    _write_private(
        env_path,
        (json.dumps(child_environment, sort_keys=True, separators=(",", ":")) + "\n").encode(),
    )

    metadata = {
        "schemaVersion": 1,
        "capability": CAPABILITY,
        "repository": args.repository,
        "operation": args.operation,
        "runId": args.run_id,
        "entrypoint": config["entrypoint"],
        "identity": identity,
        "targetSha256": {
            "https": hashlib.sha256(https_target.encode()).hexdigest(),
            "wss": hashlib.sha256(wss_target.encode()).hexdigest(),
        },
        "scenarioSha256": {
            "http": hashlib.sha256(http_payload).hexdigest(),
            "websocket": hashlib.sha256(ws_payload).hexdigest(),
        },
        "reset": config["reset"],
        "scenarioSelectors": {
            "http": http_document,
            "websocket": ws_document,
        },
    }
    # scenarioSelectors are needed only for deterministic reset and remain inside
    # the mode-0600 private root; they are removed before evidence packaging.
    _write_private(
        metadata_path,
        (json.dumps(metadata, sort_keys=True, separators=(",", ":")) + "\n").encode(),
    )
    print(json.dumps({"root": str(root), "metadata": str(metadata_path), "environment": str(env_path)}, sort_keys=True))
    return 0


def _tracked_entrypoint(source_root: Path, relative: str) -> Path:
    target = source_root / relative
    resolved_root = source_root.resolve(strict=True)
    if target.is_symlink() or not target.is_file():
        raise ProtectedConformanceError("protected entrypoint must be one tracked regular file")
    resolved = target.resolve(strict=True)
    if os.path.commonpath((str(resolved), str(resolved_root))) != str(resolved_root):
        raise ProtectedConformanceError("protected entrypoint escapes the exact source checkout")
    try:
        tracked = subprocess.run(
            ["git", "-C", str(source_root), "ls-files", "--error-unmatch", "--", relative],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        raise ProtectedConformanceError("protected entrypoint tracking check failed") from exc
    if tracked.returncode != 0:
        raise ProtectedConformanceError("protected entrypoint is not tracked in the exact source")
    return resolved


def _protected_strings(value: Any) -> set[str]:
    """Collect scenario strings for conservative private-log redaction."""
    strings: set[str] = set()
    if isinstance(value, str):
        if len(value) >= 4:
            strings.add(value)
    elif isinstance(value, Mapping):
        for item in value.values():
            strings.update(_protected_strings(item))
    elif isinstance(value, list):
        for item in value:
            strings.update(_protected_strings(item))
    return strings


_SENSITIVE_SCENARIO_KEY = re.compile(
    r"(?:token|secret|password|credential|cookie|email|user[_-]?id|account[_-]?id|"
    r"device[_-]?id|session[_-]?id|refresh|access|namespace)",
    re.IGNORECASE,
)


def _scenario_confidential_strings(
    value: Any, *, key: str = "", protected_context: bool = False
) -> set[str]:
    """Collect values that must never cross into bounded public evidence.

    Private logs are redacted more aggressively by ``_protected_strings``.  Evidence
    checks intentionally avoid classifying every short scenario enum/semantic word
    (for example ``passed`` or ``accepted``) as a secret, because those words may
    legitimately occur in the certifier's bounded result.  Identity/credential
    fields, protected variables, URLs/emails, UUID-like or otherwise long opaque
    values remain confidential.
    """
    strings: set[str] = set()
    if isinstance(value, Mapping):
        for child_key, child in value.items():
            child_name = str(child_key)
            child_protected = (
                protected_context
                or child_name == "variables"
                or bool(_SENSITIVE_SCENARIO_KEY.search(child_name))
            )
            strings.update(
                _scenario_confidential_strings(
                    child, key=child_name, protected_context=child_protected
                )
            )
        return strings
    if isinstance(value, list):
        for child in value:
            strings.update(
                _scenario_confidential_strings(
                    child, key=key, protected_context=protected_context
                )
            )
        return strings
    if isinstance(value, str):
        candidate = value.strip()
        if not candidate:
            return strings
        looks_private = (
            protected_context
            or "@" in candidate
            or candidate.startswith(("http://", "https://", "ws://", "wss://"))
            or len(candidate) >= 24
        )
        if looks_private and len(candidate) >= 4:
            strings.add(candidate)
    return strings


def _redact_text(value: str, protected: set[str]) -> str:
    redacted = value
    for secret in sorted(protected, key=len, reverse=True):
        redacted = redacted.replace(secret, "***")
    return redacted


def _private_log(path: Path, stdout: str, stderr: str, *, protected: set[str]) -> None:
    combined = stdout + ("\n" if stdout and stderr else "") + stderr
    payload = _redact_text(combined, protected).encode("utf-8", errors="replace")
    if len(payload) > MAX_CHILD_TEXT_BYTES:
        raise ProtectedConformanceError("protected deployed-conformance diagnostics exceed the reviewed bound")
    _write_private(path, payload if payload.endswith(b"\n") or not payload else payload + b"\n")


def _same_origin_url(base: str, path: str) -> str:
    base_parts = urlsplit(base.rstrip("/") + "/")
    joined = urljoin(base.rstrip("/") + "/", path.lstrip("/"))
    joined_parts = urlsplit(joined)
    if (
        joined_parts.scheme != base_parts.scheme
        or joined_parts.hostname != base_parts.hostname
        or joined_parts.port != base_parts.port
        or joined_parts.username
        or joined_parts.password
        or joined_parts.query
        or joined_parts.fragment
    ):
        raise ProtectedConformanceError("protected reset escaped the approved HTTPS origin")
    return joined


def _request_json(*, method: str, url: str, token: str, body: dict[str, Any] | None, expected_status: int) -> Any:
    raw = None if body is None else json.dumps(body, separators=(",", ":")).encode()
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    if raw is not None:
        headers["Content-Type"] = "application/json"
    request = Request(url, data=raw, headers=headers, method=method)
    opener = build_opener(NoRedirect())
    try:
        with opener.open(request, timeout=20) as response:
            status = response.status
            payload = response.read(1024 * 1024 + 1)
    except HTTPError as exc:
        status = exc.code
        payload = exc.read(1024 * 1024 + 1)
    except (URLError, TimeoutError, OSError) as exc:
        raise ProtectedConformanceError("protected reset transport failed") from exc
    if len(payload) > 1024 * 1024:
        raise ProtectedConformanceError("protected reset response exceeds the reviewed bound")
    if status != expected_status:
        raise ProtectedConformanceError("protected reset returned an unexpected HTTP status")
    if not payload:
        return None
    try:
        return json.loads(payload)
    except json.JSONDecodeError as exc:
        raise ProtectedConformanceError("protected reset returned invalid JSON") from exc


def _run_reset(metadata: dict[str, Any], env_document: dict[str, Any]) -> dict[str, Any]:
    reset = metadata["reset"]
    selectors = metadata["scenarioSelectors"]
    # Resolve protected fields deterministically from the finite environment
    # projection recorded inside the private metadata.
    field_env = metadata["fieldEnvironment"]
    https_target = env_document[field_env["httpsTarget"]]
    setup_credential = env_document[field_env["setupCredential"]]

    request = reset["request"]
    subject_selector = request["subject"]
    subject = _pointer(selectors[subject_selector["scenario"]], subject_selector["jsonPointer"])
    if not isinstance(subject, str) or not subject or len(subject) > 256:
        raise ProtectedConformanceError("protected reset subject is invalid")
    reset_path = request["pathTemplate"].replace("{subject}", quote(subject, safe=""))
    reset_url = _same_origin_url(https_target, reset_path)
    _request_json(
        method=request["method"],
        url=reset_url,
        token=setup_credential,
        body=request["body"],
        expected_status=request["expectedStatus"],
    )

    verify = reset["verify"]
    auth_selector = verify["authorization"]
    verify_token = _pointer(selectors[auth_selector["scenario"]], auth_selector["jsonPointer"])
    if not isinstance(verify_token, str) or len(verify_token) < 16 or len(verify_token) > MAX_SETUP_CREDENTIAL_BYTES:
        raise ProtectedConformanceError("protected reset verification credential is invalid")
    verify_payload = _request_json(
        method="GET",
        url=_same_origin_url(https_target, verify["path"]),
        token=verify_token,
        body=None,
        expected_status=verify["expectedStatus"],
    )
    actual = _pointer(verify_payload, verify["jsonPointer"])
    if actual != verify["equals"]:
        raise ProtectedConformanceError("protected reset verification did not reach the required state")
    return {
        "outcome": "passed",
        "requestMethod": request["method"],
        "requestPathSha256": hashlib.sha256(reset_path.encode()).hexdigest(),
        "verificationPathSha256": hashlib.sha256(verify["path"].encode()).hexdigest(),
    }


def run(args: argparse.Namespace) -> int:
    source_root = Path(args.source_root)
    config_root = Path(args.config_root)
    metadata_path = config_root / "metadata.json"
    environment_path = config_root / "child-environment.json"
    metadata = _read_object(metadata_path, "protected deployed-conformance metadata")
    env_document = _read_object(environment_path, "protected deployed-conformance child environment")
    if metadata.get("capability") != CAPABILITY:
        raise ProtectedConformanceError("protected deployed-conformance metadata capability is invalid")
    if metadata.get("repository") != args.repository or metadata.get("operation") != args.operation:
        raise ProtectedConformanceError("protected deployed-conformance metadata is bound to a different request")
    if metadata.get("runId") != args.run_id:
        raise ProtectedConformanceError("protected deployed-conformance metadata run identity is invalid")
    entrypoint = _tracked_entrypoint(source_root, str(metadata.get("entrypoint", "")))

    log_dir = Path(args.log_dir)
    artifact_dir = Path(args.artifact_dir)
    log_dir.mkdir(parents=True, exist_ok=True)
    artifact_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(log_dir, 0o700)
    os.chmod(artifact_dir, 0o700)
    child_log = log_dir / "protected-deployed-conformance.txt"
    evidence_path = artifact_dir / "protected-deployed-conformance.json"
    reset_evidence_path = artifact_dir / "protected-deployed-reset.json"

    child_env = os.environ.copy()
    for key in list(child_env):
        if key.startswith(("CI_SECRET_", "CHECKPOINT_GOOGLE_DRIVE_")) or key in {
            "AGENT_STATE_SUPABASE_URL", "AGENT_STATE_SUPABASE_SECRET_KEY",
            "GOOGLE_DRIVE_CLIENT_ID", "GOOGLE_DRIVE_CLIENT_SECRET", "GOOGLE_DRIVE_REFRESH_TOKEN",
            "TS_OAUTH_CLIENT_ID", "TS_OAUTH_SECRET", "REGISTRY_USERNAME", "REGISTRY_READ_TOKEN", "REGISTRY_WRITE_TOKEN",
        }:
            child_env.pop(key, None)
    for key, value in env_document.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise ProtectedConformanceError("protected child environment is malformed")
        child_env[key] = value

    field_env = metadata["fieldEnvironment"]
    protected_values = {
        env_document[field_env[field]]
        for field in (
            "httpsTarget",
            "wssTarget",
            "httpScenarioPath",
            "websocketScenarioPath",
            "setupCredential",
        )
    }
    scenario_log_values = _protected_strings(metadata["scenarioSelectors"])
    protected_log_values = protected_values | scenario_log_values
    protected_log_values = {
        value for value in protected_log_values if isinstance(value, str) and len(value) >= 4
    }
    protected_evidence_values = protected_values | _scenario_confidential_strings(
        metadata["scenarioSelectors"]
    )
    protected_evidence_values = {
        value for value in protected_evidence_values
        if isinstance(value, str) and len(value) >= 4
    }

    child_error: str | None = None
    reset_error: str | None = None
    try:
        try:
            result = subprocess.run(
                [sys.executable, str(entrypoint)],
                cwd=source_root,
                env=child_env,
                text=True,
                capture_output=True,
                timeout=RUN_TIMEOUT_SECONDS,
                check=False,
            )
            _private_log(child_log, result.stdout, result.stderr, protected=protected_log_values)
            if result.returncode != 0:
                child_error = f"protected deployed-conformance entrypoint failed with exit {result.returncode}"
            else:
                evidence = _loads_object(result.stdout.strip(), "protected deployed-conformance evidence")
                serialized = json.dumps(evidence, sort_keys=True, separators=(",", ":"))
                if any(secret in serialized for secret in protected_evidence_values):
                    child_error = "protected deployed-conformance evidence contains confidential material"
                else:
                    _write_private(evidence_path, (serialized + "\n").encode())
        except subprocess.TimeoutExpired:
            child_error = "protected deployed-conformance entrypoint timed out"
        except OSError:
            child_error = "protected deployed-conformance entrypoint could not execute"

        try:
            reset_evidence = _run_reset(metadata, env_document)
            _write_private(
                reset_evidence_path,
                (json.dumps(reset_evidence, sort_keys=True, separators=(",", ":")) + "\n").encode(),
            )
        except ProtectedConformanceError as exc:
            reset_error = str(exc)
    finally:
        # The private scenarios/child env must not survive into repository evidence.
        for path in sorted(config_root.glob("*")):
            if path.is_file() or path.is_symlink():
                path.unlink(missing_ok=True)
        try:
            config_root.rmdir()
        except OSError:
            pass

    if reset_error is not None:
        raise ProtectedConformanceError(reset_error)
    if child_error is not None:
        raise ProtectedConformanceError(child_error)
    print("protected deployed conformance and deterministic reset completed")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)

    materialize_parser = sub.add_parser("materialize")
    materialize_parser.add_argument("--project-state", required=True)
    materialize_parser.add_argument("--repository", required=True)
    materialize_parser.add_argument("--operation", required=True)
    materialize_parser.add_argument("--run-id", required=True)
    materialize_parser.add_argument("--output-root", required=True)

    run_parser = sub.add_parser("run")
    run_parser.add_argument("--source-root", required=True)
    run_parser.add_argument("--config-root", required=True)
    run_parser.add_argument("--repository", required=True)
    run_parser.add_argument("--operation", required=True)
    run_parser.add_argument("--run-id", required=True)
    run_parser.add_argument("--log-dir", required=True)
    run_parser.add_argument("--artifact-dir", required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        if args.command == "materialize":
            # Embed the finite environment projection in the private metadata so
            # reset can recover exact bundle fields without guessing variable names.
            project_state = _read_object(Path(args.project_state), "Agent State project configuration")
            config = _validate_config(project_state, repository=args.repository, operation=args.operation)
            result = materialize(args)
            metadata_path = Path(args.output_root) / "metadata.json"
            metadata = _read_object(metadata_path, "protected deployed-conformance metadata")
            metadata["fieldEnvironment"] = config["environmentProjection"]
            _write_private(
                metadata_path,
                (json.dumps(metadata, sort_keys=True, separators=(",", ":")) + "\n").encode(),
            )
            return result
        if args.command == "run":
            return run(args)
        raise AssertionError(args.command)
    except ProtectedConformanceError as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
