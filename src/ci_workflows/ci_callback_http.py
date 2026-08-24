"""HTTP transport policy for broker callbacks from Central GitHub Actions."""
from __future__ import annotations

from typing import Any
import urllib.request

CENTRAL_HTTP_USER_AGENT = "StreamScapeTV-Central-CI/1.0"


def central_urlopen(request: urllib.request.Request, timeout: int) -> Any:
    """Open one Central-owned HTTP request with an explicit stable API identity."""

    request.add_header("User-Agent", CENTRAL_HTTP_USER_AGENT)
    return urllib.request.urlopen(request, timeout=timeout)


__all__ = ("CENTRAL_HTTP_USER_AGENT", "central_urlopen")
