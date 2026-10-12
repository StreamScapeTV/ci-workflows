#!/usr/bin/env python3
"""Prepare isolated Kubernetes inspector credentials from fixed Central secrets.

The raw bearer token is written only to a mode-0600 file referenced by kubeconfig
user.tokenFile; no token bytes are ever included in the kubeconfig or output.
"""
from __future__ import annotations

import base64
import binascii
import os
from pathlib import Path
import re
import sys

KUBECONFIG_NAME = "cluster-inspector-kubeconfig"
TOKEN_NAME = "cluster-inspector-bearer-token"
TOKEN_MARKER = "__CLUSTER_INSPECTOR_TOKEN_FILE__"
MAX_CONFIG_BYTES = 96 * 1024
MAX_TOKEN_BYTES = 16 * 1024
TOKEN_FILE_LINE = re.compile(r"(?m)^(\s*tokenFile\s*:\s*)" + TOKEN_MARKER + r"\s*$")
OTHER_CREDENTIALS = re.compile(
    r"(?mi)^\s*(?:token|tokenFile|client-certificate(?:-data)?|client-key(?:-data)?|"
    r"username|password|auth-provider|exec|as|as-groups)\s*:"
)


class CredentialError(Exception):
    """Private source or filesystem failure; never print its text."""


def runner_paths(root: Path) -> tuple[Path, Path]:
    if not root.is_absolute() or not root.is_dir() or root.is_symlink():
        raise CredentialError
    return root / KUBECONFIG_NAME, root / TOKEN_NAME


def _write_new(path: Path, content: bytes) -> None:
    if path.exists() or path.is_symlink():
        raise CredentialError
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "wb") as file:
        file.write(content)
    path.chmod(0o600)


def _render(encoded: str, token: str, token_path: Path) -> tuple[bytes, bytes]:
    if (not encoded or len(encoded) > ((MAX_CONFIG_BYTES + 2) // 3) * 4
            or not encoded.isascii() or any(c in encoded for c in "\x00\r\n")):
        raise CredentialError
    try:
        data = base64.b64decode(encoded, validate=True)
        config = data.decode("utf-8", errors="strict")
    except (binascii.Error, ValueError, UnicodeError):
        raise CredentialError from None
    if not 16 <= len(data) <= MAX_CONFIG_BYTES or "\x00" in config or "\r" in config:
        raise CredentialError
    if (not isinstance(token, str) or not 20 <= len(token) <= MAX_TOKEN_BYTES
            or re.fullmatch(r"[!-~]+", token) is None):
        raise CredentialError
    matches = list(TOKEN_FILE_LINE.finditer(config))
    if len(matches) != 1 or config.count(TOKEN_MARKER) != 1:
        raise CredentialError
    # No alternate inline/exec/client-certificate credentials can bypass the
    # separate token's simple, inspectable lifetime/rotation contract.
    remaining = TOKEN_FILE_LINE.sub("", config)
    if OTHER_CREDENTIALS.search(remaining) or "<<:" in remaining:
        raise CredentialError
    # The path is a Central-generated absolute location; it is never a caller
    # input, and the token bytes are never substituted into YAML.
    path = str(token_path)
    if any(c in path for c in "\r\n\t\x00#'\"\\") or " " in path:
        raise CredentialError
    rendered = TOKEN_FILE_LINE.sub(lambda m: m.group(1) + path, config)
    return rendered.encode("utf-8"), token.encode("ascii")


def prepare(root: Path, encoded: str, token: str) -> Path:
    config_path, token_path = runner_paths(root)
    if config_path.exists() or config_path.is_symlink() or token_path.exists() or token_path.is_symlink():
        raise CredentialError
    document, private_token = _render(encoded, token, token_path)
    try:
        _write_new(token_path, private_token)
        _write_new(config_path, document)
    except (OSError, CredentialError):
        token_path.unlink(missing_ok=True)
        config_path.unlink(missing_ok=True)
        raise CredentialError from None
    return config_path


def main() -> int:
    if sys.argv[1:] != ["prepare"]:
        return 2
    os.umask(0o077)
    encoded = os.environ.pop("CLUSTER_INSPECTOR_KUBECONFIG_B64", "")
    token = os.environ.pop("CLUSTER_INSPECTOR_BEARER_TOKEN", "")
    try:
        root = Path(os.environ.get("RUNNER_TEMP", ""))
        path = prepare(root, encoded, token)
        # Only the predetermined, non-sensitive config path is emitted.
        for var, line in (("GITHUB_ENV", f"KUBECONFIG={path}\n"),
                          ("GITHUB_OUTPUT", f"path={path}\n")):
            target = Path(os.environ[var])
            if not target.is_file() or target.is_symlink():
                raise CredentialError
            with target.open("a", encoding="utf-8") as file:
                file.write(line)
        return 0
    except (OSError, ValueError, KeyError, CredentialError):
        print("cluster inspector credential preparation failed")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
