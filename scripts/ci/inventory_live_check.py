#!/usr/bin/env python3
"""Compare checked-in workflow inventory with current read-only Git trees."""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Mapping, Sequence

from inventory_contract import ContractError, validate

API_VERSION = "2022-11-28"
SHA = re.compile(r"^[0-9a-f]{40}$")
PREFIX = ".github/workflows/"


class GitHubClient:
    def __init__(self, token: str, api_url: str) -> None:
        if not token:
            raise ContractError("read-only organization contents token is required")
        self.token = token
        self.api_url = api_url.rstrip("/")

    def get(self, path: str) -> Any:
        request = urllib.request.Request(
            self.api_url + path,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self.token}",
                "X-GitHub-Api-Version": API_VERSION,
                "User-Agent": "StreamScapeTV-ci-workflow-inventory",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=45) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
            raise ContractError(f"GitHub API {exc.code} for {path}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise ContractError(f"GitHub API unavailable for {path}: {exc.reason}") from exc


def live_workflows(client: GitHubClient, repository: str, branch: str) -> dict[str, str]:
    owner, name = repository.split("/", 1)
    encoded = urllib.parse.quote(branch, safe="")
    payload = client.get(f"/repos/{owner}/{name}/branches/{encoded}")
    commit = payload.get("commit", {}).get("sha") if isinstance(payload, dict) else None
    if not isinstance(commit, str) or SHA.fullmatch(commit) is None:
        raise ContractError(f"{repository}: branch did not resolve to a full SHA")
    tree = client.get(f"/repos/{owner}/{name}/git/trees/{commit}?recursive=1")
    if not isinstance(tree, dict) or tree.get("truncated") is True:
        raise ContractError(f"{repository}: complete recursive tree is unavailable")
    rows = tree.get("tree")
    if not isinstance(rows, list):
        raise ContractError(f"{repository}: invalid Git tree response")
    result: dict[str, str] = {}
    for row in rows:
        if not isinstance(row, dict) or row.get("type") != "blob":
            continue
        path = row.get("path")
        blob = row.get("sha")
        if isinstance(path, str) and path.startswith(PREFIX) and path.endswith((".yml", ".yaml")):
            if not isinstance(blob, str) or SHA.fullmatch(blob) is None:
                raise ContractError(f"{repository}:{path}: invalid blob identity")
            result[path] = blob
    return result


def compare_inventory(inventory: Mapping[str, Any], live: Mapping[str, Mapping[str, str]]) -> list[str]:
    errors: list[str] = []
    expected_repositories = {row["repository"] for row in inventory["repositories"]}
    actual_repositories = set(live)
    for repository in sorted(expected_repositories | actual_repositories, key=str.casefold):
        if repository not in expected_repositories:
            errors.append(f"repository added: {repository}")
            continue
        if repository not in actual_repositories:
            errors.append(f"repository unavailable: {repository}")
            continue
        row = next(item for item in inventory["repositories"] if item["repository"] == repository)
        expected = {workflow[0]: workflow[6] for workflow in row["workflows"]}
        actual = dict(live[repository])
        for path in sorted(expected.keys() | actual.keys()):
            if path not in expected:
                errors.append(f"{repository}: workflow added: {path}")
            elif path not in actual:
                errors.append(f"{repository}: workflow removed: {path}")
            elif expected[path] is not None and expected[path] != actual[path]:
                errors.append(f"{repository}: workflow changed: {path}")
    return errors


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--root", type=Path, default=Path.cwd())
    result.add_argument("--token-env", default="STREAMSCAPE_ORG_CONTENTS_TOKEN")
    result.add_argument("--api-url", default="https://api.github.com")
    return result


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        data = validate(args.root)
        client = GitHubClient(os.environ.get(args.token_env, ""), args.api_url)
        live = {
            row["repository"]: live_workflows(client, row["repository"], row["branch"])
            for row in data["inventory"]["repositories"]
        }
        errors = compare_inventory(data["inventory"], live)
        if errors:
            raise ContractError("live workflow inventory drift:\n- " + "\n- ".join(errors))
        print(
            f"live inventory matches {len(live)} repositories and "
            f"{sum(len(rows) for rows in live.values())} workflows"
        )
    except ContractError as exc:
        print(f"inventory drift error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
