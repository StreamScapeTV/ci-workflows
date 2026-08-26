#!/usr/bin/env python3
"""Temporary deterministic migration helper for ci-workflows #593.

This branch-only helper is deleted before final validation.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = (
    ".github/workflows/android-validation-smoke.yml",
    ".github/workflows/device-lock-contract-smoke.yml",
    ".github/workflows/device-validation-contract-smoke.yml",
    ".github/workflows/gitops-validation-smoke.yml",
    ".github/workflows/reusable-android-live-service.yml",
    ".github/workflows/reusable-android-release.yml",
    ".github/workflows/reusable-android.yml",
    ".github/workflows/reusable-apple.yml",
    ".github/workflows/reusable-device.yml",
    ".github/workflows/reusable-flutter.yml",
    ".github/workflows/reusable-gradle-maven-publish.yml",
    ".github/workflows/reusable-native-image-chart.yml",
    ".github/workflows/reusable-native.yml",
    ".github/workflows/reusable-node.yml",
    ".github/workflows/reusable-oci-reproducibility.yml",
    ".github/workflows/reusable-package-publish.yml",
    ".github/workflows/reusable-public-native-image-chart.yml",
    ".github/workflows/reusable-python.yml",
    ".github/workflows/reusable-resolve-source.yml",
    ".github/workflows/reusable-static-web.yml",
    ".github/workflows/reusable-tag-image-chart.yml",
)
FIRST_PARTY = re.compile(
    r"(?P<prefix>StreamScapeTV/ci-workflows/(?:actions|\.github/workflows)/[^@\s]+)"
    r"@[0-9a-f]{40}(?:\s+#\s*[^\n]+)?"
)
THIRD_PARTY_RELEASE = re.compile(
    r"(?P<indent>\s*uses:\s+)(?P<name>(?!StreamScapeTV/ci-workflows/)[^@\s]+)"
    r"@[0-9a-f]{40}\s+#\s*(?P<release>v[0-9]+(?:\.[0-9]+){0,2}(?:[-.A-Za-z0-9]+)?)\s*$",
    re.MULTILINE,
)


def migrate(path: Path) -> bool:
    original = path.read_text(encoding="utf-8")
    text = FIRST_PARTY.sub(lambda match: f"{match.group('prefix')}@main", original)
    text = THIRD_PARTY_RELEASE.sub(
        lambda match: f"{match.group('indent')}{match.group('name')}@{match.group('release')}",
        text,
    )
    text = re.sub(
        r"^\s*-\s+contracts/action-tool-lock\.json\s*\n",
        "",
        text,
        flags=re.MULTILINE,
    )
    text = re.sub(
        r"^(\s*workflow_release:\s*)validation-[A-Za-z0-9._-]+\s*$",
        r"\1main",
        text,
        flags=re.MULTILINE,
    )
    if text != original:
        path.write_text(text, encoding="utf-8")
        return True
    return False


def main() -> None:
    changed = []
    for relative in WORKFLOWS:
        path = ROOT / relative
        if path.is_file() and migrate(path):
            changed.append(relative)
    print("#593 migrated files:")
    for relative in changed:
        print(relative)


if __name__ == "__main__":
    main()
