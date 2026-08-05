#!/usr/bin/env python3
"""Capture and validate StreamScapeTV GitHub Actions workflow inventory.

The collector reads repository metadata and workflow source through the GitHub
REST API. It never checks out or executes consumer source.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

API_VERSION = "2022-11-28"
WORKFLOW_PREFIX = ".github/workflows/"
WORKFLOW_SUFFIXES = (".yml", ".yaml")
REPOSITORY_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
SECRET_PATTERN = re.compile(r"\$\{\{\s*secrets\.([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")
USES_PATTERN = re.compile(r"^\s*-?\s*uses:\s*['\"]?([^'\"\s#]+)", re.MULTILINE)
RUNS_ON_PATTERN = re.compile(r"^\s*runs-on:\s*(.+?)\s*$", re.MULTILINE)
PRODUCT_MARKERS: Mapping[str, re.Pattern[str]] = {
    "agent-state": re.compile(r"agent[-_ ]state", re.IGNORECASE),
    "android": re.compile(r"\b(android|gradle|adb)\b", re.IGNORECASE),
    "apple": re.compile(r"\b(xcodebuild|swift|ios|tvos|macos)\b", re.IGNORECASE),
    "flutter": re.compile(r"\b(flutter|dart)\b", re.IGNORECASE),
    "gitops": re.compile(r"\b(flux|kustomize|sops)\b", re.IGNORECASE),
    "helm": re.compile(r"\bhelm\b", re.IGNORECASE),
    "node": re.compile(r"\b(node|npm|pnpm|yarn|next)\b", re.IGNORECASE),
    "oci": re.compile(r"\b(buildah|docker|podman|skopeo|oci|containerfile)\b", re.IGNORECASE),
    "python": re.compile(r"\b(python|pytest|ruff|mypy)\b", re.IGNORECASE),
}
SOURCE_MARKERS: Mapping[str, re.Pattern[str]] = {
    "checkout": re.compile(r"actions/checkout@", re.IGNORECASE),
    "explicit-ref": re.compile(r"^\s*ref:\s*", re.MULTILINE),
    "github-sha": re.compile(r"github\.sha|pull_request\.head\.sha"),
    "persist-credentials-false": re.compile(r"persist-credentials:\s*false", re.IGNORECASE),
    "tag-trigger": re.compile(r"^\s*tags(?:-ignore)?:\s*", re.MULTILINE),
    "workflow-call": re.compile(r"^\s*workflow_call:\s*", re.MULTILINE),
    "workflow-dispatch": re.compile(r"^\s*workflow_dispatch:\s*", re.MULTILINE),
}


class InventoryError(RuntimeError):
    """Raised for deterministic contract or API failures."""


@dataclass(frozen=True)
class Consumer:
    repository: str
    integration_branch: str
    agent_state_project: str
    technologies: tuple[str, ...]
    products: tuple[str, ...]
    runner_capabilities: tuple[str, ...]
    required_checks: tuple[str, ...]
    migration_status: str


class GitHubClient:
    """Minimal read-only GitHub REST client."""

    def __init__(self, token: str, api_url: str = "https://api.github.com") -> None:
        if not token:
            raise InventoryError("GitHub token is required")
        self._token = token
        self._api_url = api_url.rstrip("/")

    def get(self, path: str) -> Any:
        request = urllib.request.Request(
            f"{self._api_url}{path}",
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self._token}",
                "X-GitHub-Api-Version": API_VERSION,
                "User-Agent": "StreamScapeTV-ci-workflow-inventory",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=45) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")[:500]
            raise InventoryError(f"GitHub API {exc.code} for {path}: {body}") from exc
        except urllib.error.URLError as exc:
            raise InventoryError(f"GitHub API unavailable for {path}: {exc.reason}") from exc


def _string_list(value: Any, field: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        raise InventoryError(f"{field} must be an array of non-empty strings")
    return tuple(sorted(set(value)))


def load_consumers(path: Path) -> tuple[Consumer, ...]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise InventoryError(f"cannot read {path}: {exc}") from exc
    if document.get("schema_version") != 1 or document.get("organization") != "StreamScapeTV":
        raise InventoryError("unsupported consumer contract")
    rows = document.get("repositories")
    if not isinstance(rows, list) or not rows:
        raise InventoryError("consumer contract must contain repositories")

    consumers: list[Consumer] = []
    seen: set[str] = set()
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise InventoryError(f"repositories[{index}] must be an object")
        repository = row.get("repository")
        branch = row.get("integration_branch")
        project = row.get("agent_state_project")
        status = row.get("migration_status")
        if not isinstance(repository, str) or not REPOSITORY_PATTERN.fullmatch(repository):
            raise InventoryError(f"invalid repository at index {index}")
        if repository in seen:
            raise InventoryError(f"duplicate repository: {repository}")
        if not isinstance(branch, str) or not branch:
            raise InventoryError(f"invalid integration_branch for {repository}")
        if not isinstance(project, str) or not project:
            raise InventoryError(f"invalid agent_state_project for {repository}")
        if not isinstance(status, str) or not status:
            raise InventoryError(f"invalid migration_status for {repository}")
        seen.add(repository)
        consumers.append(
            Consumer(
                repository=repository,
                integration_branch=branch,
                agent_state_project=project,
                technologies=_string_list(row.get("technologies"), f"{repository}.technologies"),
                products=_string_list(row.get("products"), f"{repository}.products"),
                runner_capabilities=_string_list(
                    row.get("runner_capabilities"), f"{repository}.runner_capabilities"
                ),
                required_checks=_string_list(row.get("required_checks"), f"{repository}.required_checks"),
                migration_status=status,
            )
        )
    return tuple(sorted(consumers, key=lambda item: item.repository.casefold()))


def _strip_comment(value: str) -> str:
    return value.split("#", 1)[0].strip().strip("'\"")


def _parse_inline_list(value: str) -> list[str]:
    value = _strip_comment(value)
    if value.startswith("[") and value.endswith("]"):
        return sorted({_strip_comment(item) for item in value[1:-1].split(",") if _strip_comment(item)})
    return [value] if value else []


def _top_level_block(lines: Sequence[str], key: str) -> list[str]:
    start: int | None = None
    block: list[str] = []
    for index, line in enumerate(lines):
        if re.fullmatch(rf"{re.escape(key)}:\s*.*", line):
            start = index
            remainder = line.split(":", 1)[1].strip()
            if remainder:
                block.append(remainder)
            continue
        if start is not None:
            if line and not line[0].isspace():
                break
            block.append(line)
    return block


def parse_triggers(source: str) -> list[str]:
    lines = source.splitlines()
    block = _top_level_block(lines, "on")
    if not block:
        return []
    if len(block) == 1 and block[0] and not block[0][0].isspace():
        return _parse_inline_list(block[0])
    triggers: set[str] = set()
    for line in block:
        match = re.match(r"^\s{2}([A-Za-z_][A-Za-z0-9_-]*):", line)
        if match:
            triggers.add(match.group(1))
        elif re.match(r"^\s{2}-\s*", line):
            item = _strip_comment(re.sub(r"^\s{2}-\s*", "", line))
            if item:
                triggers.add(item)
    return sorted(triggers)


def parse_permissions(source: str) -> Any:
    lines = source.splitlines()
    block = _top_level_block(lines, "permissions")
    if not block:
        return None
    if len(block) == 1 and block[0] and not block[0][0].isspace():
        return _strip_comment(block[0])
    permissions: dict[str, str] = {}
    for line in block:
        match = re.match(r"^\s{2}([A-Za-z_-]+):\s*(.+?)\s*$", line)
        if match:
            permissions[match.group(1)] = _strip_comment(match.group(2))
    return dict(sorted(permissions.items()))


def analyze_workflow(source: str) -> dict[str, Any]:
    name_match = re.search(r"^name:\s*(.+?)\s*$", source, re.MULTILINE)
    uses = sorted(set(USES_PATTERN.findall(source)))
    runners = sorted({_strip_comment(value) for value in RUNS_ON_PATTERN.findall(source)})
    products = sorted(name for name, pattern in PRODUCT_MARKERS.items() if pattern.search(source))
    source_markers = sorted(name for name, pattern in SOURCE_MARKERS.items() if pattern.search(source))
    return {
        "name": _strip_comment(name_match.group(1)) if name_match else None,
        "triggers": parse_triggers(source),
        "permissions": parse_permissions(source),
        "runners": runners,
        "uses": uses,
        "reusable_workflows": sorted(item for item in uses if "/.github/workflows/" in item),
        "secrets": sorted(set(SECRET_PATTERN.findall(source))),
        "artifact_actions": sorted(item for item in uses if "actions/upload-artifact@" in item or "actions/download-artifact@" in item),
        "product_markers": products,
        "source_markers": source_markers,
        "has_inline_run": bool(re.search(r"^\s+run:\s*", source, re.MULTILINE)),
    }


def _repository_parts(repository: str) -> tuple[str, str]:
    owner, name = repository.split("/", 1)
    return owner, name


def _resolve_branch_sha(client: GitHubClient, consumer: Consumer) -> str:
    owner, name = _repository_parts(consumer.repository)
    branch = urllib.parse.quote(consumer.integration_branch, safe="")
    payload = client.get(f"/repos/{owner}/{name}/branches/{branch}")
    sha = payload.get("commit", {}).get("sha") if isinstance(payload, dict) else None
    if not isinstance(sha, str) or not SHA_PATTERN.fullmatch(sha):
        raise InventoryError(f"cannot resolve exact commit for {consumer.repository}")
    return sha


def _workflow_paths(client: GitHubClient, consumer: Consumer, sha: str) -> list[str]:
    owner, name = _repository_parts(consumer.repository)
    tree = client.get(f"/repos/{owner}/{name}/git/trees/{sha}?recursive=1")
    if not isinstance(tree, dict) or tree.get("truncated"):
        raise InventoryError(f"complete tree unavailable for {consumer.repository}@{sha}")
    rows = tree.get("tree")
    if not isinstance(rows, list):
        raise InventoryError(f"invalid tree response for {consumer.repository}@{sha}")
    return sorted(
        row["path"]
        for row in rows
        if isinstance(row, dict)
        and row.get("type") == "blob"
        and isinstance(row.get("path"), str)
        and row["path"].startswith(WORKFLOW_PREFIX)
        and row["path"].endswith(WORKFLOW_SUFFIXES)
    )


def _fetch_text(client: GitHubClient, repository: str, path: str, sha: str) -> str:
    owner, name = _repository_parts(repository)
    encoded_path = urllib.parse.quote(path, safe="/")
    payload = client.get(f"/repos/{owner}/{name}/contents/{encoded_path}?ref={sha}")
    if not isinstance(payload, dict) or payload.get("encoding") != "base64":
        raise InventoryError(f"unexpected file response for {repository}:{path}@{sha}")
    content = payload.get("content")
    if not isinstance(content, str):
        raise InventoryError(f"missing file content for {repository}:{path}@{sha}")
    try:
        return base64.b64decode(content, validate=False).decode("utf-8")
    except (ValueError, UnicodeDecodeError) as exc:
        raise InventoryError(f"invalid UTF-8 workflow {repository}:{path}@{sha}") from exc


def capture_repository(client: GitHubClient, consumer: Consumer) -> dict[str, Any]:
    sha = _resolve_branch_sha(client, consumer)
    workflows: list[dict[str, Any]] = []
    for path in _workflow_paths(client, consumer, sha):
        source = _fetch_text(client, consumer.repository, path, sha)
        record = analyze_workflow(source)
        record.update(
            {
                "path": path,
                "sha256": hashlib.sha256(source.encode("utf-8")).hexdigest(),
                "disposition": "unclassified",
                "migration_target": None,
            }
        )
        workflows.append(record)
    return {
        "repository": consumer.repository,
        "integration_branch": consumer.integration_branch,
        "exact_commit": sha,
        "agent_state_project": consumer.agent_state_project,
        "technologies": list(consumer.technologies),
        "products": list(consumer.products),
        "runner_capabilities": list(consumer.runner_capabilities),
        "required_checks": list(consumer.required_checks),
        "migration_status": consumer.migration_status,
        "workflows": workflows,
    }


def normalize_inventory(document: Mapping[str, Any]) -> dict[str, Any]:
    repositories = document.get("repositories")
    if not isinstance(repositories, list):
        raise InventoryError("inventory.repositories must be an array")
    normalized: list[dict[str, Any]] = []
    for row in repositories:
        if not isinstance(row, dict):
            raise InventoryError("inventory repository rows must be objects")
        copy = dict(row)
        workflows = copy.get("workflows")
        if not isinstance(workflows, list):
            raise InventoryError(f"{copy.get('repository')}.workflows must be an array")
        copy["workflows"] = sorted((dict(item) for item in workflows), key=lambda item: item.get("path", ""))
        normalized.append(copy)
    return {
        "schema_version": 1,
        "organization": "StreamScapeTV",
        "repositories": sorted(normalized, key=lambda item: str(item.get("repository", "")).casefold()),
    }


def capture_inventory(
    client: GitHubClient, consumers: Iterable[Consumer], selected: set[str] | None = None
) -> dict[str, Any]:
    rows = [capture_repository(client, item) for item in consumers if not selected or item.repository in selected]
    if selected:
        missing = selected - {item.repository for item in consumers}
        if missing:
            raise InventoryError(f"unknown repositories requested: {', '.join(sorted(missing))}")
    return normalize_inventory({"repositories": rows})


def compare_inventory(expected: Mapping[str, Any], actual: Mapping[str, Any]) -> list[str]:
    expected_rows = {row["repository"]: row for row in normalize_inventory(expected)["repositories"]}
    actual_rows = {row["repository"]: row for row in normalize_inventory(actual)["repositories"]}
    errors: list[str] = []
    for repository in sorted(expected_rows.keys() | actual_rows.keys()):
        if repository not in expected_rows:
            errors.append(f"unexpected repository: {repository}")
            continue
        if repository not in actual_rows:
            errors.append(f"missing repository: {repository}")
            continue
        old = expected_rows[repository]
        new = actual_rows[repository]
        if old.get("exact_commit") != new.get("exact_commit"):
            errors.append(f"{repository}: integration commit changed {old.get('exact_commit')} -> {new.get('exact_commit')}")
        old_workflows = {item["path"]: item for item in old["workflows"]}
        new_workflows = {item["path"]: item for item in new["workflows"]}
        for path in sorted(old_workflows.keys() | new_workflows.keys()):
            if path not in old_workflows:
                errors.append(f"{repository}: workflow added: {path}")
            elif path not in new_workflows:
                errors.append(f"{repository}: workflow removed: {path}")
            elif old_workflows[path].get("sha256") != new_workflows[path].get("sha256"):
                errors.append(f"{repository}: workflow changed: {path}")
    return errors


def _markdown_cell(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, (list, tuple)):
        text = ", ".join(str(item) for item in value) or "—"
    elif isinstance(value, dict):
        text = ", ".join(f"{key}:{item}" for key, item in sorted(value.items())) or "—"
    else:
        text = str(value)
    return text.replace("|", "\\|").replace("\n", " ")


def render_markdown(document: Mapping[str, Any]) -> str:
    inventory = normalize_inventory(document)
    lines = [
        "# Organization workflow inventory",
        "",
        "Generated from `contracts/workflow-inventory.json`. The inventory records exact integration commits and reads workflow source without executing consumer code.",
        "",
    ]
    for repository in inventory["repositories"]:
        lines.extend(
            [
                f"## {repository['repository']}",
                "",
                f"- Integration: `{repository['integration_branch']}@{repository['exact_commit']}`",
                f"- Products: {_markdown_cell(repository.get('products'))}",
                f"- Migration: `{repository.get('migration_status', 'unknown')}`",
                "",
                "| Workflow | Name | Triggers | Runners | Products | Disposition | Migration target |",
                "|---|---|---|---|---|---|---|",
            ]
        )
        workflows = repository.get("workflows", [])
        if not workflows:
            lines.append("| — | — | — | — | — | — | — |")
        for workflow in workflows:
            lines.append(
                "| "
                + " | ".join(
                    _markdown_cell(value)
                    for value in (
                        workflow.get("path"),
                        workflow.get("name"),
                        workflow.get("triggers"),
                        workflow.get("runners"),
                        workflow.get("product_markers"),
                        workflow.get("disposition"),
                        workflow.get("migration_target"),
                    )
                )
                + " |"
            )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def read_inventory(path: Path) -> dict[str, Any]:
    try:
        return normalize_inventory(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError) as exc:
        raise InventoryError(f"cannot read inventory {path}: {exc}") from exc


def write_json(path: Path, document: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(normalize_inventory(document), indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _client_from_args(args: argparse.Namespace) -> GitHubClient:
    token = os.environ.get(args.token_env, "")
    return GitHubClient(token=token, api_url=args.api_url)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    capture = subparsers.add_parser("capture", help="capture exact live workflow inventory")
    capture.add_argument("--consumers", type=Path, default=Path("contracts/consumers.json"))
    capture.add_argument("--output", type=Path, default=Path("contracts/workflow-inventory.json"))
    capture.add_argument("--markdown", type=Path, default=Path("docs/inventory/workflows.md"))
    capture.add_argument("--repository", action="append", default=[])
    capture.add_argument("--token-env", default="GITHUB_TOKEN")
    capture.add_argument("--api-url", default="https://api.github.com")

    validate = subparsers.add_parser("validate", help="validate and normalize checked-in inventory")
    validate.add_argument("--inventory", type=Path, default=Path("contracts/workflow-inventory.json"))

    render = subparsers.add_parser("render", help="render checked-in inventory documentation")
    render.add_argument("--inventory", type=Path, default=Path("contracts/workflow-inventory.json"))
    render.add_argument("--output", type=Path, default=Path("docs/inventory/workflows.md"))

    check = subparsers.add_parser("check-live", help="fail when live workflow trees drift")
    check.add_argument("--consumers", type=Path, default=Path("contracts/consumers.json"))
    check.add_argument("--inventory", type=Path, default=Path("contracts/workflow-inventory.json"))
    check.add_argument("--repository", action="append", default=[])
    check.add_argument("--token-env", default="GITHUB_TOKEN")
    check.add_argument("--api-url", default="https://api.github.com")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "capture":
            consumers = load_consumers(args.consumers)
            selected = set(args.repository) or None
            inventory = capture_inventory(_client_from_args(args), consumers, selected)
            write_json(args.output, inventory)
            args.markdown.parent.mkdir(parents=True, exist_ok=True)
            args.markdown.write_text(render_markdown(inventory), encoding="utf-8")
            print(f"captured {sum(len(row['workflows']) for row in inventory['repositories'])} workflows")
        elif args.command == "validate":
            inventory = read_inventory(args.inventory)
            print(f"validated {len(inventory['repositories'])} repositories")
        elif args.command == "render":
            inventory = read_inventory(args.inventory)
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(render_markdown(inventory), encoding="utf-8")
        elif args.command == "check-live":
            expected = read_inventory(args.inventory)
            consumers = load_consumers(args.consumers)
            selected = set(args.repository) or {row["repository"] for row in expected["repositories"]}
            actual = capture_inventory(_client_from_args(args), consumers, selected)
            errors = compare_inventory(expected, actual)
            if errors:
                raise InventoryError("workflow inventory drift:\n- " + "\n- ".join(errors))
            print("live workflow inventory matches checked-in contract")
        else:  # pragma: no cover
            raise InventoryError(f"unknown command: {args.command}")
    except InventoryError as exc:
        print(f"workflow inventory error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
