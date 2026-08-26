#!/usr/bin/env python3
"""Temporary branch-only audit helper for ci-workflows #593."""
from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CENTRAL_SHA = re.compile(r"StreamScapeTV/ci-workflows/(?:actions|\.github/workflows)/[^\s@'\"]+@[0-9a-f]{40}")
THIRD_PARTY_SHA = re.compile(r"uses:\s+(?!StreamScapeTV/ci-workflows/)([^\s@]+)@[0-9a-f]{40}(?:\s+#\s*([^\n]+))?")


def files(pattern: str):
    return sorted(path for path in ROOT.glob(pattern) if path.is_file())


def main() -> None:
    print("FIRST_PARTY_COMPONENT_SHA_PATHS")
    for path in files(".github/workflows/*.y*ml") + files("docs/**/*.md") + files("tests/test_*.py"):
        text = path.read_text(encoding="utf-8")
        if CENTRAL_SHA.search(text):
            print(path.relative_to(ROOT).as_posix())

    print("THIRD_PARTY_SHA_WORKFLOW_PATHS")
    for path in files(".github/workflows/*.y*ml"):
        text = path.read_text(encoding="utf-8")
        matches = THIRD_PARTY_SHA.findall(text)
        if matches:
            print(path.relative_to(ROOT).as_posix(), "::", ", ".join(f"{uses} #{comment or ''}" for uses, comment in matches))

    print("ACTION_TOOL_LOCK_REFERENCES")
    for path in files("**/*"):
        if path.suffix not in {".py", ".md", ".yml", ".yaml", ".json"}:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if "action-tool-lock.json" in text:
            print(path.relative_to(ROOT).as_posix())

    print("POLICY_TEST_METHODS")
    for path in files("tests/test_*.py"):
        text = path.read_text(encoding="utf-8")
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) or not node.name.startswith("test_"):
                continue
            segment = ast.get_source_segment(text, node) or ""
            lower = (node.name + "\n" + segment).lower()
            if (
                "action-tool-lock" in lower
                or "first-party" in lower and any(word in lower for word in ("immutable", "checkpoint", "pin"))
                or "streamscapetv/ci-workflows/actions/" in lower and any(word in lower for word in ("immutable", "checkpoint", "pin", "locked"))
                or any(word in node.name.lower() for word in ("checkpoint", "pin_activation", "exact_locked", "immutable_helper", "action_pin"))
            ):
                print(f"{path.relative_to(ROOT).as_posix()}::{node.name}")

    print("AUDIT_COMPLETE")


if __name__ == "__main__":
    main()
