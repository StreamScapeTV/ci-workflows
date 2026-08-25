"""Persist bounded normalized CI diagnostics to Cloudflare D1."""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from typing import Any, Mapping, Sequence
import urllib.error
import urllib.parse
import urllib.request
import uuid

_RETENTION_HOURS = 24
_MAX_DIAGNOSTICS = 64
_MAX_DIAGNOSTICS_BYTES = 64 * 1024
_MAX_MESSAGE_BYTES = 2048
_MAX_RESPONSE_BYTES = 128 * 1024
_HTTP_TIMEOUT_SECONDS = 30
_REPOSITORY = re.compile(r"[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}\Z")
_PROJECT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_WORKFLOW = re.compile(r"[a-z0-9][a-z0-9._-]{0,127}\Z")
_PROFILE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_CODE = re.compile(r"[a-z][a-z0-9._-]{0,63}\Z")
_STAGE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,127}\Z")
_ACCOUNT = re.compile(r"[A-Za-z0-9]{1,32}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_ALLOWED_STATUS = {"succeeded", "failed", "cancelled", "timed_out"}
_ALLOWED_SEVERITY = {"warning", "error"}
_SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b(token|password|authorization|secret|api[_-]?key)\b\s*[:=]\s*\S+"
)
_BEARER = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/-]{12,}={0,2}")
_GITHUB_TOKEN = re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b")
_URL_CREDENTIAL = re.compile(r"(?i)https://[^\s/@:]+:[^\s/@]+@")

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS ci_diagnostics (
  diagnostic_key TEXT PRIMARY KEY,
  ci_run_id TEXT NOT NULL,
  run_attempt INTEGER NOT NULL,
  github_run_id INTEGER NOT NULL,
  project_key TEXT NOT NULL,
  repository TEXT NOT NULL,
  ref TEXT NOT NULL,
  is_tag INTEGER NOT NULL CHECK (is_tag IN (0, 1)),
  workflow_key TEXT NOT NULL,
  profile TEXT NOT NULL,
  status TEXT NOT NULL,
  diagnostics_json TEXT NOT NULL,
  diagnostics_sha256 TEXT NOT NULL,
  diagnostic_count INTEGER NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  expires_at TEXT NOT NULL
)
""".strip()
_CREATE_EXPIRY_INDEX_SQL = (
    "CREATE INDEX IF NOT EXISTS idx_ci_diagnostics_expires_at "
    "ON ci_diagnostics(expires_at)"
)
_DELETE_EXPIRED_SQL = "DELETE FROM ci_diagnostics WHERE expires_at <= ?"
_INSERT_SQL = """
INSERT INTO ci_diagnostics (
  diagnostic_key, ci_run_id, run_attempt, github_run_id, project_key,
  repository, ref, is_tag, workflow_key, profile, status,
  diagnostics_json, diagnostics_sha256, diagnostic_count,
  created_at, updated_at, expires_at
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(diagnostic_key) DO UPDATE SET
  status = excluded.status,
  diagnostics_json = excluded.diagnostics_json,
  diagnostics_sha256 = excluded.diagnostics_sha256,
  diagnostic_count = excluded.diagnostic_count,
  updated_at = excluded.updated_at,
  expires_at = excluded.expires_at
WHERE ci_diagnostics.ci_run_id = excluded.ci_run_id
  AND ci_diagnostics.run_attempt = excluded.run_attempt
  AND ci_diagnostics.github_run_id = excluded.github_run_id
  AND ci_diagnostics.project_key = excluded.project_key
  AND ci_diagnostics.repository = excluded.repository
  AND ci_diagnostics.ref = excluded.ref
  AND ci_diagnostics.is_tag = excluded.is_tag
  AND ci_diagnostics.workflow_key = excluded.workflow_key
  AND ci_diagnostics.profile = excluded.profile
""".strip()
_VERIFY_SQL = """
SELECT diagnostic_key, ci_run_id, run_attempt, github_run_id, project_key,
       repository, ref, is_tag, workflow_key, profile, status,
       diagnostics_sha256, diagnostic_count, diagnostics_json, expires_at
FROM ci_diagnostics
WHERE diagnostic_key = ?
LIMIT 1
""".strip()


class D1DiagnosticError(RuntimeError):
    """Stable non-sensitive diagnostics persistence failure."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise D1DiagnosticError(code)


def _plain(value: object, code: str, *, maximum: int) -> str:
    _require(isinstance(value, str), code)
    text = value.strip()
    _require(
        bool(text)
        and len(text.encode("utf-8")) <= maximum
        and not any(character in text for character in ("\x00", "\r", "\n")),
        code,
    )
    return text


def _match(value: object, pattern: re.Pattern[str], code: str) -> str:
    text = _plain(value, code, maximum=512)
    _require(pattern.fullmatch(text) is not None, code)
    return text


def _positive_int(value: object, code: str, maximum: int) -> int:
    if isinstance(value, int) and not isinstance(value, bool):
        parsed = value
    elif isinstance(value, str) and value.isdigit():
        parsed = int(value)
    else:
        raise D1DiagnosticError(code)
    _require(1 <= parsed <= maximum, code)
    return parsed


def _boolean(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized == "true":
            return True
        if normalized == "false":
            return False
    raise D1DiagnosticError("invalid_is_tag")


def _uuid(value: object) -> str:
    text = _plain(value, "invalid_ci_run_id", maximum=36)
    try:
        parsed = uuid.UUID(text)
    except (ValueError, AttributeError):
        raise D1DiagnosticError("invalid_ci_run_id") from None
    _require(str(parsed) == text.lower(), "invalid_ci_run_id")
    return str(parsed)


def _database_id(value: object) -> str:
    text = _plain(value, "invalid_d1_database_id", maximum=36)
    try:
        parsed = uuid.UUID(text)
    except (ValueError, AttributeError):
        raise D1DiagnosticError("invalid_d1_database_id") from None
    _require(str(parsed) == text.lower(), "invalid_d1_database_id")
    return str(parsed)


def _ref(value: object) -> str:
    return _plain(value, "invalid_ref", maximum=512)


def _sanitize_message(value: object) -> str:
    _require(isinstance(value, str), "invalid_diagnostic_message")
    text = " ".join(value.replace("\x00", " ").replace("\r", " ").replace("\n", " ").split())
    _require(bool(text), "invalid_diagnostic_message")
    text = _URL_CREDENTIAL.sub("https://<redacted>@", text)
    text = _SECRET_ASSIGNMENT.sub(lambda match: f"{match.group(1)}=<redacted>", text)
    text = _BEARER.sub("Bearer <redacted>", text)
    text = _GITHUB_TOKEN.sub("<redacted>", text)
    encoded = text.encode("utf-8")
    _require(len(encoded) <= _MAX_MESSAGE_BYTES, "diagnostic_message_too_large")
    return text


def _json_array(raw: object) -> list[object]:
    _require(isinstance(raw, str), "invalid_diagnostics_json")
    _require(0 < len(raw.encode("utf-8")) <= _MAX_DIAGNOSTICS_BYTES, "diagnostics_too_large")

    def hook(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise D1DiagnosticError("diagnostic_duplicate_key")
            result[key] = value
        return result

    try:
        parsed = json.loads(raw, object_pairs_hook=hook)
    except json.JSONDecodeError:
        raise D1DiagnosticError("invalid_diagnostics_json") from None
    _require(isinstance(parsed, list), "invalid_diagnostics_json")
    _require(len(parsed) <= _MAX_DIAGNOSTICS, "too_many_diagnostics")
    return parsed


def normalize_diagnostics(raw: object) -> tuple[tuple[dict[str, str], ...], str, str]:
    rows: list[dict[str, str]] = []
    for item in _json_array(raw):
        _require(isinstance(item, dict), "invalid_diagnostic")
        _require(set(item).issubset({"severity", "code", "stage", "message"}), "invalid_diagnostic")
        _require({"severity", "code", "message"}.issubset(item), "invalid_diagnostic")
        severity = _plain(item["severity"], "invalid_diagnostic_severity", maximum=16).lower()
        _require(severity in _ALLOWED_SEVERITY, "invalid_diagnostic_severity")
        code = _match(item["code"], _CODE, "invalid_diagnostic_code")
        stage = ""
        if item.get("stage") not in (None, ""):
            stage = _match(item["stage"], _STAGE, "invalid_diagnostic_stage")
        rows.append(
            {
                "severity": severity,
                "code": code,
                "stage": stage,
                "message": _sanitize_message(item["message"]),
            }
        )
    canonical = json.dumps(rows, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    _require(len(canonical.encode("utf-8")) <= _MAX_DIAGNOSTICS_BYTES, "diagnostics_too_large")
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return tuple(rows), canonical, digest


@dataclass(frozen=True, slots=True)
class D1DiagnosticRequest:
    ci_run_id: str
    run_attempt: int
    github_run_id: int
    project_key: str
    repository: str
    ref: str
    is_tag: bool
    workflow_key: str
    profile: str
    status: str
    diagnostics_json: str
    diagnostics_sha256: str
    diagnostic_count: int

    @property
    def diagnostic_key(self) -> str:
        return f"ci/{self.ci_run_id}/{self.run_attempt}"


@dataclass(frozen=True, slots=True)
class D1DiagnosticReceipt:
    diagnostic_key: str
    diagnostic_status: str
    diagnostic_sha256: str
    diagnostic_count: int

    def output_values(self) -> dict[str, str]:
        return {
            "diagnostic_key": self.diagnostic_key,
            "diagnostic_status": self.diagnostic_status,
            "diagnostic_sha256": self.diagnostic_sha256,
            "diagnostic_count": str(self.diagnostic_count),
        }


def build_request(
    *,
    ci_run_id: object,
    run_attempt: object,
    github_run_id: object,
    project_key: object,
    repository: object,
    ref: object,
    is_tag: object,
    workflow_key: object,
    profile: object,
    status: object,
    diagnostics_json: object,
) -> D1DiagnosticRequest:
    _rows, canonical, digest = normalize_diagnostics(diagnostics_json)
    checked_status = _plain(status, "invalid_diagnostic_status", maximum=16).lower()
    _require(checked_status in _ALLOWED_STATUS, "invalid_diagnostic_status")
    return D1DiagnosticRequest(
        ci_run_id=_uuid(ci_run_id),
        run_attempt=_positive_int(run_attempt, "invalid_run_attempt", 1000),
        github_run_id=_positive_int(github_run_id, "invalid_github_run_id", 2**63 - 1),
        project_key=_match(project_key, _PROJECT, "invalid_project_key"),
        repository=_match(repository, _REPOSITORY, "invalid_repository"),
        ref=_ref(ref),
        is_tag=_boolean(is_tag),
        workflow_key=_match(workflow_key, _WORKFLOW, "invalid_workflow_key"),
        profile=_match(profile, _PROFILE, "invalid_profile"),
        status=checked_status,
        diagnostics_json=canonical,
        diagnostics_sha256=digest,
        diagnostic_count=len(_rows),
    )


def _timestamp(value: datetime) -> str:
    checked = value.astimezone(timezone.utc).replace(microsecond=0)
    return checked.isoformat().replace("+00:00", "Z")


def _request_payload(request: D1DiagnosticRequest, now: datetime) -> tuple[dict[str, object], str, str]:
    created_at = _timestamp(now)
    expires_at = _timestamp(now + timedelta(hours=_RETENTION_HOURS))
    params: list[object] = [
        request.diagnostic_key,
        request.ci_run_id,
        request.run_attempt,
        request.github_run_id,
        request.project_key,
        request.repository,
        request.ref,
        1 if request.is_tag else 0,
        request.workflow_key,
        request.profile,
        request.status,
        request.diagnostics_json,
        request.diagnostics_sha256,
        request.diagnostic_count,
        created_at,
        created_at,
        expires_at,
    ]
    payload: dict[str, object] = {
        "batch": [
            {"sql": _CREATE_TABLE_SQL},
            {"sql": _CREATE_EXPIRY_INDEX_SQL},
            {"sql": _DELETE_EXPIRED_SQL, "params": [created_at]},
            {"sql": _INSERT_SQL, "params": params},
            {"sql": _VERIFY_SQL, "params": [request.diagnostic_key]},
        ]
    }
    return payload, created_at, expires_at


def _verify_response(value: object, request: D1DiagnosticRequest, expires_at: str) -> None:
    _require(isinstance(value, dict) and value.get("success") is True, "d1_response_rejected")
    result = value.get("result")
    _require(isinstance(result, list) and len(result) == 5, "d1_response_invalid")
    _require(all(isinstance(item, dict) and item.get("success") is True for item in result), "d1_query_failed")
    rows = result[-1].get("results")
    _require(isinstance(rows, list) and len(rows) == 1 and isinstance(rows[0], dict), "d1_readback_missing")
    row = rows[0]
    expected: dict[str, object] = {
        "diagnostic_key": request.diagnostic_key,
        "ci_run_id": request.ci_run_id,
        "run_attempt": request.run_attempt,
        "github_run_id": request.github_run_id,
        "project_key": request.project_key,
        "repository": request.repository,
        "ref": request.ref,
        "is_tag": 1 if request.is_tag else 0,
        "workflow_key": request.workflow_key,
        "profile": request.profile,
        "status": request.status,
        "diagnostics_sha256": request.diagnostics_sha256,
        "diagnostic_count": request.diagnostic_count,
        "diagnostics_json": request.diagnostics_json,
        "expires_at": expires_at,
    }
    _require(all(row.get(key) == expected_value for key, expected_value in expected.items()), "d1_readback_mismatch")


def persist_request(
    request: D1DiagnosticRequest,
    *,
    account_id: object,
    database_id: object,
    api_token: object,
    opener: Any = urllib.request.urlopen,
    now: datetime | None = None,
) -> D1DiagnosticReceipt:
    account = _match(account_id, _ACCOUNT, "invalid_d1_account_id")
    database = _database_id(database_id)
    token = _plain(api_token, "invalid_d1_api_token", maximum=1024)
    _require(len(token) >= 20, "invalid_d1_api_token")
    current = datetime.now(timezone.utc) if now is None else now
    payload, _created_at, expires_at = _request_payload(request, current)
    body = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    url = (
        "https://api.cloudflare.com/client/v4/accounts/"
        + urllib.parse.quote(account, safe="")
        + "/d1/database/"
        + urllib.parse.quote(database, safe="")
        + "/query"
    )
    http_request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "StreamScapeTV-ci-workflows-diagnostics",
        },
    )
    try:
        with opener(http_request, timeout=_HTTP_TIMEOUT_SECONDS) as response:
            status = int(getattr(response, "status", response.getcode()))
            raw = response.read(_MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as error:
        raise D1DiagnosticError(f"d1_http_{int(error.code)}") from None
    except (OSError, urllib.error.URLError, ValueError):
        raise D1DiagnosticError("d1_unavailable") from None
    _require(status == 200, f"d1_http_{status}")
    _require(len(raw) <= _MAX_RESPONSE_BYTES, "d1_response_too_large")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise D1DiagnosticError("d1_response_invalid") from None
    _verify_response(value, request, expires_at)
    return D1DiagnosticReceipt(
        diagnostic_key=request.diagnostic_key,
        diagnostic_status="uploaded",
        diagnostic_sha256=request.diagnostics_sha256,
        diagnostic_count=request.diagnostic_count,
    )


def _required(environment: Mapping[str, str], name: str) -> str:
    value = environment.get(name, "")
    _require(bool(value), f"missing_{name.lower()}")
    return value


def persist_from_environment(
    environment: Mapping[str, str] = os.environ,
    *,
    opener: Any = urllib.request.urlopen,
    now: datetime | None = None,
) -> D1DiagnosticReceipt:
    request = build_request(
        ci_run_id=_required(environment, "INPUT_CI_RUN_ID"),
        run_attempt=_required(environment, "GITHUB_RUN_ATTEMPT"),
        github_run_id=_required(environment, "GITHUB_RUN_ID"),
        project_key=_required(environment, "INPUT_PROJECT_KEY"),
        repository=_required(environment, "INPUT_REPOSITORY"),
        ref=_required(environment, "INPUT_REF"),
        is_tag=_required(environment, "INPUT_IS_TAG"),
        workflow_key=_required(environment, "INPUT_WORKFLOW_KEY"),
        profile=_required(environment, "INPUT_PROFILE"),
        status=_required(environment, "INPUT_STATUS"),
        diagnostics_json=_required(environment, "INPUT_DIAGNOSTICS_JSON"),
    )
    receipt = persist_request(
        request,
        account_id=_required(environment, "CIW_D1_ACCOUNT_ID"),
        database_id=_required(environment, "CIW_D1_DATABASE_ID"),
        api_token=_required(environment, "CIW_D1_API_TOKEN"),
        opener=opener,
        now=now,
    )
    output_path = Path(_required(environment, "GITHUB_OUTPUT"))
    _require(output_path.is_absolute(), "invalid_github_output")
    try:
        with output_path.open("a", encoding="utf-8") as handle:
            for key, value in receipt.output_values().items():
                handle.write(f"{key}={value}\n")
    except OSError:
        raise D1DiagnosticError("github_output_unavailable") from None
    return receipt


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Persist bounded normalized CI diagnostics")
    result.add_argument("command", choices=("persist",))
    return result


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "persist":
            persist_from_environment()
    except D1DiagnosticError as error:
        print(error.code, file=sys.stderr)
        return 1
    return 0


__all__ = (
    "D1DiagnosticError",
    "D1DiagnosticReceipt",
    "D1DiagnosticRequest",
    "build_request",
    "main",
    "normalize_diagnostics",
    "persist_from_environment",
    "persist_request",
)
