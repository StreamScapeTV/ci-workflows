"""Mint one bounded contents-read GitHub App token for an exact repository."""
from __future__ import annotations

import argparse
import base64
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
from typing import Any, Callable, Mapping, Sequence
import urllib.error
import urllib.parse
import urllib.request

_MAX_RESPONSE_BYTES = 64 * 1024
_HTTP_TIMEOUT_SECONDS = 30
_REPOSITORY = re.compile(r"[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}\Z")
_TOKEN = re.compile(r"[A-Za-z0-9_]{20,512}\Z")


class GitHubAppTokenError(RuntimeError):
    """Stable non-sensitive GitHub App token failure."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise GitHubAppTokenError(code)


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode(
        "utf-8"
    )


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _repository(value: object) -> tuple[str, str, str]:
    _require(
        isinstance(value, str) and _REPOSITORY.fullmatch(value) is not None,
        "invalid_repository",
    )
    owner, name = value.split("/", 1)
    return value, owner, name


def _app_id(value: object) -> int:
    if isinstance(value, int) and not isinstance(value, bool):
        parsed = value
    elif isinstance(value, str) and value.isdigit():
        parsed = int(value)
    else:
        raise GitHubAppTokenError("invalid_github_app_id")
    _require(1 <= parsed <= 2**63 - 1, "invalid_github_app_id")
    return parsed


def _private_key(value: object) -> str:
    _require(isinstance(value, str) and 32 <= len(value.encode("utf-8")) <= 32 * 1024, "invalid_github_app_private_key")
    text = value.replace("\\n", "\n") if "\n" not in value and "\\n" in value else value
    _require("PRIVATE KEY-----" in text, "invalid_github_app_private_key")
    return text


def _token(value: object) -> str:
    _require(isinstance(value, str) and _TOKEN.fullmatch(value) is not None, "github_app_token_invalid")
    return value


def _openssl_sign(signing_input: bytes, private_key: str) -> bytes:
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=True) as handle:
            handle.write(private_key)
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
        raise GitHubAppTokenError("github_app_signing_unavailable") from None
    _require(
        completed.returncode == 0 and bool(completed.stdout),
        "github_app_signing_failed",
    )
    return completed.stdout


class GitHubAppRepositoryTokenClient:
    """Minimal GitHub App client that can mint only one repository read token."""

    def __init__(
        self,
        app_id: object,
        private_key: object,
        *,
        opener: Any = urllib.request.urlopen,
        signer: Callable[[bytes, str], bytes] = _openssl_sign,
    ) -> None:
        self._app_id = _app_id(app_id)
        self._private_key = _private_key(private_key)
        self._opener = opener
        self._signer = signer

    def _jwt(self, now: int | None = None) -> str:
        current = int(time.time()) if now is None else now
        header = _b64url(_canonical({"alg": "RS256", "typ": "JWT"}))
        payload = _b64url(
            _canonical(
                {
                    "iat": current - 30,
                    "exp": current + 8 * 60,
                    "iss": str(self._app_id),
                }
            )
        )
        signing_input = f"{header}.{payload}".encode("ascii")
        signature = self._signer(signing_input, self._private_key)
        _require(bool(signature), "github_app_signing_failed")
        return f"{header}.{payload}.{_b64url(signature)}"

    def _request(
        self,
        method: str,
        path: str,
        *,
        authorization: str,
        body: Mapping[str, object] | None = None,
        expected: tuple[int, ...] = (200,),
    ) -> Mapping[str, Any]:
        _require(path.startswith("/"), "invalid_github_path")
        data = None if body is None else _canonical(dict(body))
        request = urllib.request.Request(
            "https://api.github.com" + path,
            data=data,
            method=method,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {authorization}",
                "User-Agent": "StreamScapeTV-ci-workflows-repository-token",
                "X-GitHub-Api-Version": "2022-11-28",
                **({"Content-Type": "application/json"} if data is not None else {}),
            },
        )
        try:
            with self._opener(request, timeout=_HTTP_TIMEOUT_SECONDS) as response:
                status = int(getattr(response, "status", response.getcode()))
                raw = response.read(_MAX_RESPONSE_BYTES + 1)
        except urllib.error.HTTPError as error:
            raise GitHubAppTokenError(f"github_app_http_{int(error.code)}") from None
        except (OSError, urllib.error.URLError, ValueError):
            raise GitHubAppTokenError("github_app_unavailable") from None
        _require(status in expected, f"github_app_http_{status}")
        _require(len(raw) <= _MAX_RESPONSE_BYTES, "github_app_response_too_large")
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise GitHubAppTokenError("github_app_response_invalid") from None
        _require(isinstance(value, Mapping), "github_app_response_invalid")
        return value

    def repository_contents_read_token(self, repository: object) -> str:
        _full, owner, name = _repository(repository)
        app_jwt = self._jwt()
        installation = self._request(
            "GET",
            f"/repos/{urllib.parse.quote(owner, safe='')}/{urllib.parse.quote(name, safe='')}/installation",
            authorization=app_jwt,
        )
        installation_id = installation.get("id")
        _require(
            isinstance(installation_id, int) and installation_id > 0,
            "github_app_installation_missing",
        )
        issued = self._request(
            "POST",
            f"/app/installations/{installation_id}/access_tokens",
            authorization=app_jwt,
            body={
                "repositories": [name],
                "permissions": {"contents": "read"},
            },
            expected=(201,),
        )
        return _token(issued.get("token"))


def _required(environment: Mapping[str, str], name: str) -> str:
    value = environment.get(name, "")
    _require(bool(value), f"missing_{name.lower()}")
    return value


def issue_repository_token(
    environment: Mapping[str, str] = os.environ,
    *,
    client_factory: Callable[[object, object], GitHubAppRepositoryTokenClient] = GitHubAppRepositoryTokenClient,
) -> str:
    client = client_factory(
        _required(environment, "CIW_GITHUB_APP_ID"),
        _required(environment, "CIW_GITHUB_APP_PRIVATE_KEY"),
    )
    token = client.repository_contents_read_token(
        _required(environment, "INPUT_REPOSITORY")
    )
    output_path = _required(environment, "GITHUB_OUTPUT")
    path = Path(output_path)
    _require(path.is_absolute(), "invalid_github_output")
    # GitHub workflow commands mask the value before it is exported as a step output.
    print(f"::add-mask::{token}")
    try:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(f"token={token}\n")
    except OSError:
        raise GitHubAppTokenError("github_output_unavailable") from None
    return token


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Bounded GitHub App repository token adapter")
    result.add_argument("command", choices=("repository-token",))
    return result


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "repository-token":
            issue_repository_token()
    except GitHubAppTokenError as error:
        print(error.code, file=sys.stderr)
        return 1
    return 0


__all__ = (
    "GitHubAppRepositoryTokenClient",
    "GitHubAppTokenError",
    "issue_repository_token",
    "main",
)
