"""Credential-safe bounded GitHub HTTP transport for maintenance."""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from email.message import Message
from typing import Any, Mapping, Sequence

from .maintenance_contract import MaintenanceError

_API_VERSION = "2022-11-28"
_RETRYABLE_READ = {429, 500, 502, 503, 504}
_SAFE_RETRY_METHODS = {"GET", "HEAD"}


def _origin(value: urllib.parse.SplitResult) -> tuple[str, str, int | None]:
    port = value.port
    if port is None:
        port = 443 if value.scheme.casefold() == "https" else 80
    return value.scheme.casefold(), (value.hostname or "").casefold(), port


class _SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Follow HTTPS redirects without forwarding credentials cross-origin."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        source = urllib.parse.urlsplit(req.full_url)
        target = urllib.parse.urlsplit(newurl)
        if (
            target.scheme.casefold() != "https"
            or not target.hostname
            or target.username is not None
            or target.password is not None
        ):
            raise MaintenanceError("unsafe_redirect_url")
        cross_origin = _origin(source) != _origin(target)
        if cross_origin and req.get_method().upper() not in {"GET", "HEAD"}:
            raise MaintenanceError("unsafe_redirect_url")
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected is not None and cross_origin:
            redirected.remove_header("Authorization")
        return redirected


class GitHubTransport:
    def __init__(
        self,
        token: str,
        *,
        api_url: str = "https://api.github.com",
        max_attempts: int = 3,
        opener=None,
        sleep=time.sleep,
    ) -> None:
        if not token:
            raise MaintenanceError("credential_missing")
        normalized = api_url.rstrip("/")
        parsed = urllib.parse.urlsplit(normalized)
        if (
            parsed.scheme.casefold() != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise MaintenanceError("unsafe_api_url")
        self.token = token
        self.api = normalized
        self._api_url = parsed
        self.max_attempts = max(1, min(max_attempts, 5))
        self.opener = opener or urllib.request.build_opener(
            _SafeRedirectHandler()
        ).open
        self.sleep = sleep

    def _url(self, path: str) -> str:
        if path.startswith(("https://", "http://")):
            parsed = urllib.parse.urlsplit(path)
            if (
                parsed.scheme.casefold() != "https"
                or parsed.username is not None
                or parsed.password is not None
                or _origin(parsed) != _origin(self._api_url)
            ):
                raise MaintenanceError("unsafe_api_url")
            api_path = self._api_url.path.rstrip("/")
            if api_path and not (
                parsed.path == api_path
                or parsed.path.startswith(api_path + "/")
            ):
                raise MaintenanceError("unsafe_api_url")
            return path
        return self.api + "/" + path.lstrip("/")

    def request(
        self,
        method: str,
        path: str,
        *,
        payload: Mapping[str, Any] | None = None,
        expected: Sequence[int] = (200,),
        allow_404: bool = False,
        raw: bool = False,
    ) -> tuple[Any, Message]:
        method = method.upper()
        safe_retry = method in _SAFE_RETRY_METHODS
        url = self._url(path)
        data = (
            None
            if payload is None
            else json.dumps(payload, separators=(",", ":")).encode()
        )
        request = urllib.request.Request(url, data=data, method=method)
        for key, value in (
            ("Accept", "application/vnd.github+json"),
            ("Authorization", f"Bearer {self.token}"),
            ("X-GitHub-Api-Version", _API_VERSION),
            ("User-Agent", "StreamScapeTV-ci-workflows-maintenance"),
        ):
            request.add_header(key, value)
        if data is not None:
            request.add_header("Content-Type", "application/json")
        for attempt in range(1, self.max_attempts + 1):
            try:
                with self.opener(request, timeout=30) as response:
                    status = int(
                        getattr(response, "status", response.getcode())
                    )
                    if status not in expected:
                        raise MaintenanceError("github_unexpected_status")
                    body = response.read()
                    value = (
                        body
                        if raw
                        else (None if not body else json.loads(body.decode()))
                    )
                    return value, response.headers
            except urllib.error.HTTPError as error:
                if allow_404 and error.code == 404:
                    return None, Message()
                if error.code == 429 and attempt < self.max_attempts:
                    retry = (
                        error.headers.get("Retry-After", "")
                        if error.headers
                        else ""
                    )
                    delay = (
                        float(retry)
                        if str(retry).isdigit()
                        else float(2 ** (attempt - 1))
                    )
                    self.sleep(min(delay, 30))
                    continue
                if error.code in _RETRYABLE_READ - {429}:
                    if safe_retry and attempt < self.max_attempts:
                        self.sleep(float(2 ** (attempt - 1)))
                        continue
                    if not safe_retry:
                        raise MaintenanceError(
                            "github_mutation_state_unknown"
                        ) from error
                raise MaintenanceError("github_api_failed") from error
            except urllib.error.URLError as error:
                if not safe_retry:
                    raise MaintenanceError(
                        "github_mutation_state_unknown"
                    ) from error
                if attempt < self.max_attempts:
                    self.sleep(float(2 ** (attempt - 1)))
                    continue
                raise MaintenanceError("github_api_failed") from error
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise MaintenanceError("github_response_invalid") from error
        raise MaintenanceError("github_api_failed")

    @staticmethod
    def _next(headers: Message) -> str | None:
        for part in headers.get("Link", "").split(","):
            section = part.strip()
            if 'rel="next"' not in section:
                continue
            if not section.startswith("<") or ">" not in section:
                raise MaintenanceError("pagination_invalid")
            return section[1 : section.index(">")]
        return None

    def paginate(
        self,
        path: str,
        *,
        collection_key: str | None = None,
        maximum_pages: int = 20,
    ) -> list[Mapping[str, Any]]:
        output: list[Mapping[str, Any]] = []
        next_path: str | None = path
        pages = 0
        while next_path:
            pages += 1
            if pages > maximum_pages:
                raise MaintenanceError("pagination_bound_exceeded")
            payload, headers = self.request("GET", next_path)
            values = (
                payload
                if collection_key is None
                else payload.get(collection_key)
                if isinstance(payload, Mapping)
                else None
            )
            if not isinstance(values, list) or any(
                not isinstance(item, Mapping) for item in values
            ):
                raise MaintenanceError("github_response_invalid")
            output.extend(values)
            next_path = self._next(headers)
        return output

    @staticmethod
    def _repo(repository: str) -> str:
        return "/".join(
            urllib.parse.quote(part, safe="")
            for part in repository.split("/", 1)
        )
