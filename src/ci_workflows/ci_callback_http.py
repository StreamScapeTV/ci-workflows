"""HTTP transport policy for broker callbacks from Central GitHub Actions."""
from __future__ import annotations

import io
import json
import re
from typing import Any
import urllib.error
import urllib.parse
import urllib.request

CENTRAL_HTTP_USER_AGENT = "StreamScapeTV-Central-CI/1.0"
_CALLBACK_ERROR_BYTES = 4096
_BROKER_CALLBACK_PATHS = frozenset({"/actions/start", "/actions/finish", "/actions/route"})
_SAFE_REMOTE_CODE = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")


def _safe_broker_error_code(request: urllib.request.Request, raw: bytes) -> str | None:
    """Return only a bounded stable broker code for reviewed callback paths."""

    if len(raw) > _CALLBACK_ERROR_BYTES:
        return None
    if urllib.parse.urlsplit(request.full_url).path not in _BROKER_CALLBACK_PATHS:
        return None
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(value, dict):
        return None
    code = value.get("code")
    if not isinstance(code, str) or _SAFE_REMOTE_CODE.fullmatch(code) is None:
        return None
    return code


def central_urlopen(request: urllib.request.Request, timeout: int) -> Any:
    """Open one Central-owned HTTP request with an explicit stable API identity."""

    request.add_header("User-Agent", CENTRAL_HTTP_USER_AGENT)
    try:
        return urllib.request.urlopen(request, timeout=timeout)
    except urllib.error.HTTPError as error:
        try:
            raw = error.read(_CALLBACK_ERROR_BYTES + 1)
        except OSError:
            raise
        error.fp = io.BytesIO(raw)
        safe_code = _safe_broker_error_code(request, raw)
        if safe_code is not None:
            error.code = f"{int(error.code)}_{safe_code}"
        raise


__all__ = ("CENTRAL_HTTP_USER_AGENT", "central_urlopen")
