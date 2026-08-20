#!/usr/bin/env python3
"""Validate caller-owned inputs for the native image + Helm publisher."""

from __future__ import annotations

import os
from pathlib import Path
import re
import subprocess


_SLUG = re.compile(r"[a-z0-9](?:[a-z0-9._-]{0,126}[a-z0-9])?")


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(message)


def _bounded(root: Path, relative: str, *, directory: bool) -> Path:
    _require(bool(relative), "caller path is empty")
    _require("\\" not in relative and not relative.startswith("/"), "caller path is invalid")
    parts = Path(relative).parts
    _require(all(part not in {"", ".."} for part in parts), "caller path is invalid")
    resolved = (root / relative).resolve()
    _require(resolved == root or root in resolved.parents, "caller path escapes source")
    _require(not resolved.is_symlink(), "caller path must not be a symlink")
    _require(resolved.is_dir() if directory else resolved.is_file(), "caller path type is invalid")
    return resolved


def main() -> int:
    root = Path(os.environ.get("SOURCE_ROOT", "source")).resolve()
    _require(root.is_dir() and not root.is_symlink(), "caller source root is invalid")

    head = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    ).stdout.strip()
    _require(head == os.environ["SOURCE_SHA"], "caller source SHA is not exact")

    for name in ("IMAGE_NAME", "CHART_NAME"):
        _require(_SLUG.fullmatch(os.environ[name]) is not None, f"{name} is invalid")

    _bounded(root, os.environ["BUILD_CONTEXT"], directory=True)
    _bounded(root, os.environ["DOCKERFILE_PATH"], directory=False)
    chart_root = _bounded(root, os.environ["CHART_PATH"], directory=True)
    chart_yaml = chart_root / "Chart.yaml"
    _require(chart_yaml.is_file() and not chart_yaml.is_symlink(), "Chart.yaml is invalid")

    chart_text = chart_yaml.read_text(encoding="utf-8")
    chart_name = re.search(r"^name:\s*([^\s]+)\s*$", chart_text, re.MULTILINE)
    _require(
        chart_name is not None and chart_name.group(1) == os.environ["CHART_NAME"],
        "chart name does not match release input",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
