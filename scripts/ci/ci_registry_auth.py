#!/usr/bin/env python3
"""Create one ephemeral, private authentication home for a per-purpose CI workflow.

Writes under ``$RUNNER_TEMP/central-registry-auth``: a ``home/.netrc`` (GitHub and the private
registry), an optional ``home/.gradle/gradle.properties`` with the two fixed Gradle credential
properties, and a Git config whose credential helper serves the same two hosts. Which of the
three authentications are created is chosen by the workflow (fixed per purpose), never by the
product. Prints ``auth_home=`` and ``git_config=`` lines for GITHUB_OUTPUT.

Credentials are taken from the environment (``GITHUB_SOURCE_TOKEN``, ``REGISTRY_USERNAME``,
``REGISTRY_READ_TOKEN``) and are never printed.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import stat

REGISTRY_HOST = "git.faruqi.dev"
HELPER = """#!/usr/bin/env bash
set -Eeuo pipefail
protocol=""
host=""
while IFS='=' read -r key value; do
  case "${key}" in
    protocol) protocol="${value}" ;;
    host) host="${value}" ;;
  esac
done
test "${protocol}" = https || exit 0
root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
case "${host}" in
  github.com)
    test -f "${root}/github-token" || exit 0
    printf 'username=x-access-token\\npassword=%s\\n' "$(cat "${root}/github-token")"
    ;;
  git.faruqi.dev)
    test -f "${root}/registry-user" && test -f "${root}/registry-token" || exit 0
    printf 'username=%s\\npassword=%s\\n' "$(cat "${root}/registry-user")" "$(cat "${root}/registry-token")"
    ;;
esac
"""


def _private_write(path: Path, content: str, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        stream.write(content)
    path.chmod(mode)


def build(root: Path, *, github_git: bool, registry_netrc: bool, gradle_maven: bool, environ) -> dict:
    github_token = environ.get("GITHUB_SOURCE_TOKEN", "")
    username = environ.get("REGISTRY_USERNAME", "")
    token = environ.get("REGISTRY_READ_TOKEN", "")
    if github_git and not github_token:
        raise SystemExit("GitHub source token is required for github_git authentication")
    if (registry_netrc or gradle_maven) and not (username and token):
        raise SystemExit("private registry read credential is missing")
    if root.exists():
        shutil.rmtree(root)
    home = root / "home"
    home.mkdir(parents=True)
    root.chmod(0o700)
    home.chmod(0o700)
    if github_git:
        _private_write(root / "github-token", github_token)
    if registry_netrc or gradle_maven:
        _private_write(root / "registry-user", username)
        _private_write(root / "registry-token", token)
    helper = root / "git-credential-central"
    _private_write(helper, HELPER, 0o700)
    git_config = root / "gitconfig"
    _private_write(git_config, f"[credential]\n\thelper = {helper}\n\tuseHttpPath = false\n")
    netrc = ""
    if github_git:
        netrc += f"machine github.com\n  login x-access-token\n  password {github_token}\n"
    if registry_netrc:
        netrc += f"machine {REGISTRY_HOST}\n  login {username}\n  password {token}\n"
    _private_write(home / ".netrc", netrc)
    if gradle_maven:
        gradle = home / ".gradle"
        gradle.mkdir()
        gradle.chmod(0o700)
        _private_write(gradle / "gradle.properties",
                       f"centralRegistryUsername={username}\ncentralRegistryReadToken={token}\n")
    for path in (home / ".netrc",):
        if stat.S_IMODE(path.stat().st_mode) & 0o077:
            raise SystemExit("authentication file permissions are too open")
    return {"auth_home": str(home), "git_config": str(git_config)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--github-git", action="store_true")
    parser.add_argument("--registry-netrc", action="store_true")
    parser.add_argument("--gradle-maven", action="store_true")
    args = parser.parse_args()
    root = Path(os.environ["RUNNER_TEMP"]) / "central-registry-auth"
    values = build(root, github_git=args.github_git, registry_netrc=args.registry_netrc,
                   gradle_maven=args.gradle_maven, environ=os.environ)
    with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as output:
        for name, value in values.items():
            output.write(f"{name}={value}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
