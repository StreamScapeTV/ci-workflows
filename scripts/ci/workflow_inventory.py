#!/usr/bin/env python3
"""Analyze workflow source and route inventory commands to the v2 contract.

`inventory_contract.py` is the sole checked-in schema and report authority.
`inventory_live_check.py` is the sole organization live-tree comparator.  This
module keeps the source-shape analyzer used by inventory fixtures and provides a
stable compatibility CLI without maintaining a second inventory format.
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path
from typing import Any, Mapping, Sequence

import inventory_contract
import inventory_live_check

RUNS_ON = re.compile(r"(?m)^\s*runs-on:\s*(.+?)\s*$")
USES = re.compile(r"(?m)^\s*(?:-\s*)?uses:\s*([^\s#]+)")
SECRET_REFERENCE = re.compile(r"secrets\.([A-Za-z_][A-Za-z0-9_]*)")
PRODUCT_MARKERS = {
    "oci": re.compile(r"(?i)\b(buildah|skopeo|podman|docker|buildx|oci|registry)\b"),
    "helm": re.compile(r"(?i)\bhelm\b"),
    "android": re.compile(r"(?i)\b(android|gradle|adb)\b"),
    "apple": re.compile(r"(?i)\b(xcode|swift|ios|tvos|macos|simulator)\b"),
    "flutter": re.compile(r"(?i)\b(flutter|dart)\b"),
    "node": re.compile(r"(?i)\b(node|npm|next\.js|nextjs)\b"),
    "python": re.compile(r"(?i)\b(python|pytest|pip|venv)\b"),
    "gitops": re.compile(r"(?i)\b(kustomize|kubectl|flux|sops|kubernetes)\b"),
}


def strip_comment(value: str) -> str:
    return value.split("#", 1)[0].strip().strip("'\"")


def top_level_block(source: str, key: str) -> list[str]:
    lines = source.splitlines()
    result: list[str] = []
    active = False
    for line in lines:
        if not line.strip() or line.lstrip().startswith("#"):
            if active:
                result.append(line)
            continue
        indent = len(line) - len(line.lstrip())
        if indent == 0:
            if active:
                break
            if re.match(rf"^[\"']?{re.escape(key)}[\"']?\s*:", line):
                active = True
                remainder = line.split(":", 1)[1].strip()
                if remainder:
                    result.append(remainder)
            continue
        if active:
            result.append(line)
    return result


def parse_triggers(source: str) -> list[str]:
    triggers: set[str] = set()
    for line in top_level_block(source, "on"):
        text = line.strip()
        if not text or text.startswith("#"):
            continue
        if text.startswith("[") and text.endswith("]"):
            for item in text[1:-1].split(","):
                value = strip_comment(item)
                if value:
                    triggers.add(value)
            continue
        match = re.match(r"([A-Za-z_][A-Za-z0-9_-]*)\s*:", text)
        if match:
            triggers.add(match.group(1))
    return sorted(triggers)


def parse_permissions(source: str) -> list[str]:
    permissions: set[str] = set()
    for line in top_level_block(source, "permissions"):
        match = re.match(r"\s*([A-Za-z-]+)\s*:\s*([^#]+)", line)
        if match:
            permissions.add(f"{match.group(1)}:{strip_comment(match.group(2))}")
    return sorted(permissions)


def parse_runs_on(source: str) -> list[str]:
    return sorted({strip_comment(match.group(1)) for match in RUNS_ON.finditer(source)})


def parse_uses(source: str) -> list[str]:
    return sorted({strip_comment(match.group(1)) for match in USES.finditer(source)})


def parse_secrets(source: str) -> list[str]:
    return sorted(set(SECRET_REFERENCE.findall(source)))


def analyze_workflow(path: str, source: str, blob_sha: str | None = None) -> dict[str, Any]:
    """Return deterministic source-shape metadata without executing workflow code."""

    name_match = re.search(r"(?m)^name:\s*(.+?)\s*$", source)
    products = sorted(
        name for name, pattern in PRODUCT_MARKERS.items() if pattern.search(source)
    )
    return {
        "path": path,
        "name": strip_comment(name_match.group(1)) if name_match else Path(path).stem,
        "triggers": parse_triggers(source),
        "permissions": parse_permissions(source),
        "runs_on": parse_runs_on(source),
        "uses": parse_uses(source),
        "secrets": parse_secrets(source),
        "products": products,
        "uploads_artifacts": "actions/upload-artifact" in source,
        "downloads_artifacts": "actions/download-artifact" in source,
        "calls_reusable_workflows": sorted(
            value for value in parse_uses(source) if "/.github/workflows/" in value
        ),
        "blob_sha": blob_sha,
    }


def compare_inventory(
    inventory: Mapping[str, Any], live: Mapping[str, Mapping[str, str]]
) -> list[str]:
    """Compatibility alias for the sole live-tree comparison implementation."""

    return inventory_live_check.compare_inventory(inventory, live)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("validate")
    render = commands.add_parser("render")
    render.add_argument("--check", action="store_true")
    live = commands.add_parser("check-live")
    live.add_argument("--token-env", default="STREAMSCAPE_ORG_CONTENTS_TOKEN")
    live.add_argument("--api-url", default="https://api.github.com")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = str(args.root)
    if args.command == "validate":
        return inventory_contract.main(["--root", root, "validate"])
    if args.command == "render":
        forwarded = ["--root", root, "render"]
        if args.check:
            forwarded.append("--check")
        return inventory_contract.main(forwarded)
    return inventory_live_check.main(
        [
            "--root",
            root,
            "--token-env",
            args.token_env,
            "--api-url",
            args.api_url,
        ]
    )


if __name__ == "__main__":
    raise SystemExit(main())
