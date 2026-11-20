#!/usr/bin/env python3
"""Cache identity and save guard for per-purpose CI workflows.

``identity`` derives the actions/cache keys. Design rules (see docs/per-purpose-ci-workflows.md):

* The key names the repository, OS/arch and the toolchain generation (Gradle version) plus a
  fingerprint of tracked dependency inputs and an ISO week. It never names the commit, branch,
  test selector or workflow run.
* ``restore-keys`` fall back first to the same fingerprint of an older week, then to any entry of
  the same repository/toolchain. A dependency change therefore starts from the previous cache and
  downloads only the difference, instead of starting cold.
* Only integration refs (``TRUSTED_REFS``) may save. Feature branches and tags only restore. A
  moving branch therefore cannot plant bytes that later runs of other refs restore.

``guard`` refuses to save a cache directory that contains symlinks, is too large or contains a
configured credential value.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys

# One reviewed place for the product integration refs that may seed shared caches.
TRUSTED_REFS = frozenset(("develop", "main"))
MAX_CACHE_BYTES = 4 * 1024 * 1024 * 1024
MAX_CACHE_FILES = 200_000
MAX_SCAN_FILE_BYTES = 8 * 1024 * 1024
MAX_IDENTITY_FILES = 512
MAX_IDENTITY_FILE_BYTES = 1024 * 1024
MAX_IDENTITY_TOTAL_BYTES = 8 * 1024 * 1024
REPOSITORY = re.compile(r"[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}")


def _tracked(root: Path) -> list:
    raw = subprocess.run(["git", "-C", str(root), "ls-files", "-z"], check=True,
                         stdout=subprocess.PIPE).stdout
    return [item.decode("utf-8") for item in raw.split(b"\0") if item]


def _digest(root: Path, selected: list, seed: bytes) -> str:
    digest = hashlib.sha256(seed + b"\0")
    total = 0
    if len(selected) > MAX_IDENTITY_FILES:
        raise SystemExit("cache identity exceeds the reviewed file bound")
    for relative in selected:
        path = root / relative
        if path.is_symlink() or not path.is_file():
            raise SystemExit(f"cache identity input must be one regular tracked file: {relative}")
        data = path.read_bytes()
        total += len(data)
        if len(data) > MAX_IDENTITY_FILE_BYTES or total > MAX_IDENTITY_TOTAL_BYTES:
            raise SystemExit("cache identity exceeds the reviewed size bound")
        digest.update(relative.encode() + b"\0" + data + b"\0")
    return digest.hexdigest()


def gradle_inputs(tracked: list) -> list:
    selected = []
    for relative in tracked:
        name = PurePosixPath(relative).name
        parts = PurePosixPath(relative).parts
        if (relative.startswith("gradle/") or relative.endswith((".gradle", ".gradle.kts"))
                or name in {"gradle.properties", "versions.properties", "gradle.lockfile"}
                or (parts and parts[0] in {"buildSrc", "build-logic"})):
            selected.append(relative)
    return sorted(selected)


def gradle_version(root: Path) -> str:
    wrapper = root / "gradle/wrapper/gradle-wrapper.properties"
    if wrapper.is_symlink() or not wrapper.is_file():
        raise SystemExit("Gradle cache requires one tracked Gradle wrapper")
    match = re.search(r"gradle-([0-9][0-9A-Za-z.+-]{0,31}?)-(?:bin|all)\.zip", wrapper.read_text(encoding="utf-8"))
    if match is None:
        raise SystemExit("Gradle wrapper distributionUrl is not a recognised Gradle distribution")
    return match.group(1)


def swiftpm_fingerprint(root: Path):
    """Same inputs as the legacy Apple cache: Package.resolved, Package manifests, Xcode package refs."""
    records = []
    for name in sorted(filter(None, _tracked(root))):
        path = root / name
        if name.endswith(".xcodeproj/project.pbxproj"):
            text = path.read_text()
            for section in ("XCRemoteSwiftPackageReference", "XCLocalSwiftPackageReference"):
                match = re.search(r"/\* Begin " + section + r" section \*/(.*?)/\* End " + section + r" section \*/", text, re.S)
                if match:
                    records.append((name + ":" + section, " ".join(match.group(1).split()).encode()))
        elif path.name in ("Package.resolved", "Package.swift") or (
                path.name.startswith("Package@swift-") and path.suffix == ".swift"):
            records.append((name, path.read_bytes()))
    if not records:
        return None
    digest = hashlib.sha256(b"ci-swiftpm-v1\0")
    for name, data in records:
        digest.update(name.encode() + b"\0" + data + b"\0")
    return digest.hexdigest()


def trust_of(ref: str, is_tag: str) -> str:
    return "trusted" if is_tag == "false" and ref in TRUSTED_REFS else "untrusted"


def identity(kind: str, root: Path, repository: str, ref: str, is_tag: str, runner_os: str,
             runner_arch: str, home: Path, today: datetime.date) -> dict:
    if REPOSITORY.fullmatch(repository) is None:
        raise SystemExit("cache repository is outside the reviewed bound")
    if is_tag not in ("true", "false"):
        raise SystemExit("cache source kind is invalid")
    iso = today.isocalendar()
    week = f"{iso[0]}w{iso[1]:02d}"
    repo = hashlib.sha256(repository.encode()).hexdigest()[:16]
    arch = re.sub(r"[^A-Za-z0-9]", "", runner_arch)
    osname = re.sub(r"[^A-Za-z0-9]", "", runner_os)
    if kind == "gradle":
        version = gradle_version(root)
        fingerprint = _digest(root, gradle_inputs(_tracked(root)), b"ci-gradle-v3")[:32]
        prefix = f"ci-gradle-v3-{osname}-{arch}-{repo}-g{version}-"
        paths = [home / "caches/modules-2", home / "caches/build-cache-1", home / "wrapper/dists"]
    elif kind == "swiftpm":
        full = swiftpm_fingerprint(root)
        if full is None:
            return {"enabled": "false"}
        fingerprint = full[:32]
        prefix = f"ci-swiftpm-v1-{osname}-{arch}-{repo}-"
        base = home / "Library/Caches/org.swift.swiftpm"
        paths = [base / "artifacts", base / "repositories"]
    else:
        raise SystemExit("unsupported cache kind")
    trust = trust_of(ref, is_tag)
    return {
        "enabled": "true",
        "trust": trust,
        "save_allowed": "true" if trust == "trusted" else "false",
        "fingerprint": fingerprint,
        "key": f"{prefix}{fingerprint}-{week}",
        "restore_keys": f"{prefix}{fingerprint}-\n{prefix}",
        "exact_prefix": f"{prefix}{fingerprint}-",
        "paths": "\n".join(str(path) for path in paths),
    }


def guard(paths: list, secrets: list) -> tuple:
    """Return (ok, reason). Missing paths are ignored; at least one real file must exist."""
    total = 0
    files = 0
    for base in paths:
        if not base.exists() and not base.is_symlink():
            continue
        if base.is_symlink() or not base.is_dir():
            return False, "cache root must be one real directory"
        resolved_base = base.resolve(strict=True)
        for current, dirs, names in os.walk(base):
            for name in [*dirs, *names]:
                path = Path(current) / name
                if path.is_symlink():
                    return False, "cache path contains a symlink"
            for name in names:
                path = Path(current) / name
                files += 1
                size = path.stat().st_size
                total += size
                if files > MAX_CACHE_FILES:
                    return False, "cache exceeds the reviewed file limit"
                if total > MAX_CACHE_BYTES:
                    return False, "cache exceeds the reviewed size limit"
                if os.path.commonpath((str(path.resolve(strict=True)), str(resolved_base))) != str(resolved_base):
                    return False, "cache path escapes its root"
                if secrets and size <= MAX_SCAN_FILE_BYTES:
                    data = path.read_bytes()
                    if any(secret in data for secret in secrets):
                        return False, "cache contains a configured credential value"
    if files == 0:
        return False, "no cache files were produced"
    return True, f"{files} file(s), {total} bytes"


def _write_output(values: dict) -> None:
    with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as output:
        for name, value in values.items():
            if "\n" in value:
                output.write(f"{name}<<CI_CACHE_EOF\n{value}\nCI_CACHE_EOF\n")
            else:
                output.write(f"{name}={value}\n")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    ident = sub.add_parser("identity")
    ident.add_argument("--kind", required=True, choices=("gradle", "swiftpm"))
    ident.add_argument("--source", required=True)
    ident.add_argument("--home", required=True, help="GRADLE_USER_HOME for gradle, the user HOME for swiftpm")
    check = sub.add_parser("guard")
    check.add_argument("--path", action="append", required=True)
    args = parser.parse_args(argv)
    if args.command == "identity":
        values = identity(
            args.kind, Path(args.source), os.environ["SOURCE_REPOSITORY"], os.environ["SOURCE_REF"],
            os.environ["SOURCE_IS_TAG"], os.environ["RUNNER_OS"], os.environ["RUNNER_ARCH"],
            Path(args.home), datetime.datetime.now(datetime.timezone.utc).date(),
        )
        _write_output(values)
        return 0
    secrets = [value.encode() for name, value in os.environ.items()
               if name.startswith("CI_SECRET_") and len(value) >= 8]
    ok, reason = guard([Path(item) for item in args.path], secrets)
    if not ok:
        print(f"::warning::cache save skipped: {reason}", file=sys.stderr)
    else:
        print(f"cache save prepared: {reason}")
    _write_output({"save_allowed": "true" if ok else "false"})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
