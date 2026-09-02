#!/usr/bin/env python3
"""Bounded Streamscape Media package publication helpers for Central CI."""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import urllib.error
import urllib.request
import zipfile

OWNER = "mimranfaruqi"
BASE = f"https://git.faruqi.dev/api/packages/{OWNER}/generic"
APPLE_PACKAGE = "streamscape-media-apple"
RELEASE_PACKAGE = "streamscape-media-release"
SEMVER = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)")
SHA = re.compile(r"[0-9a-f]{40}")
HEX64 = re.compile(r"[0-9a-f]{64}")


class ReleaseError(RuntimeError):
    pass


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def require_version(value: str) -> str:
    if not SEMVER.fullmatch(value):
        raise ReleaseError("release version must be stable MAJOR.MINOR.PATCH")
    return value


def require_sha(value: str) -> str:
    if not SHA.fullmatch(value):
        raise ReleaseError("source SHA must be one lowercase 40-character commit SHA")
    return value


def output(**values: str) -> None:
    target = os.environ.get("GITHUB_OUTPUT")
    if not target:
        return
    with open(target, "a", encoding="utf-8") as handle:
        for key, value in values.items():
            if "\n" in value or "\r" in value:
                raise ReleaseError(f"output {key} must be one line")
            handle.write(f"{key}={value}\n")


def auth_header() -> str:
    username = os.environ.get("PACKAGE_USERNAME", "")
    token = os.environ.get("PACKAGE_TOKEN", "")
    if not username or not token:
        raise ReleaseError("private package publication credentials are unavailable")
    return "Basic " + base64.b64encode(f"{username}:{token}".encode()).decode()


def request(method: str, url: str, *, data: bytes | None = None) -> tuple[int, bytes]:
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", auth_header())
    if data is not None:
        req.add_header("Content-Type", "application/octet-stream")
    try:
        with urllib.request.urlopen(req, timeout=60) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()
    except urllib.error.URLError as error:
        raise ReleaseError("private package service request failed") from error


def package_url(package: str, version: str, filename: str) -> str:
    safe = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,199}")
    for value, label in ((package, "package"), (version, "version"), (filename, "filename")):
        if not safe.fullmatch(value):
            raise ReleaseError(f"invalid generic package {label}")
    return f"{BASE}/{package}/{version}/{filename}"


def preflight(package: str, version: str, files: list[Path]) -> list[Path]:
    missing: list[Path] = []
    for path in files:
        code, body = request("GET", package_url(package, version, path.name))
        if code == 200:
            if body != path.read_bytes():
                raise ReleaseError(f"immutable package conflict for {path.name}")
        elif code == 404:
            missing.append(path)
        else:
            raise ReleaseError(f"generic package lookup failed for {path.name} with HTTP {code}")
    return missing


def publish_missing(package: str, version: str, missing: list[Path]) -> None:
    for path in missing:
        url = package_url(package, version, path.name)
        expected = path.read_bytes()
        code, _ = request("PUT", url, data=expected)
        if code not in (200, 201):
            # A concurrent identical creator is acceptable. Never mutate again.
            read_code, body = request("GET", url)
            if read_code != 200 or body != expected:
                raise ReleaseError(f"immutable package create failed for {path.name} with HTTP {code}")
        read_code, body = request("GET", url)
        if read_code != 200 or body != expected:
            raise ReleaseError(f"immutable package readback mismatch for {path.name}")


def distribution_from_archive(archive: Path) -> dict:
    with zipfile.ZipFile(archive) as bundle:
        try:
            raw = bundle.read("StreamscapeMediaApple/Distribution.json")
        except KeyError as error:
            raise ReleaseError("Apple archive is missing Distribution.json") from error
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ReleaseError("Apple Distribution.json must be one object")
    return value


def apple(args: argparse.Namespace) -> None:
    version = require_version(args.version)
    source_sha = require_sha(args.source_sha)
    archive = Path(args.archive)
    compatibility = Path(args.compatibility)
    if not archive.is_file() or not compatibility.is_file():
        raise ReleaseError("Apple publication inputs are missing")
    expected_name = f"streamscape-media-{version}-apple-binary.zip"
    if archive.name != expected_name:
        raise ReleaseError("Apple archive filename does not match stable release version")
    distribution = distribution_from_archive(archive)
    if distribution.get("version") != version or distribution.get("commitSha") != source_sha:
        raise ReleaseError("Apple archive provenance does not match release source")
    platforms = distribution.get("platforms")
    boundary = distribution.get("nativeRuntimeBoundary")
    if not isinstance(platforms, dict) or not {"ios", "tvos"} <= set(platforms):
        raise ReleaseError("Apple archive is missing required iOS/tvOS platform metadata")
    if not isinstance(boundary, list):
        raise ReleaseError("Apple archive is missing native runtime boundary metadata")
    compatibility_value = json.loads(compatibility.read_text(encoding="utf-8"))
    if not isinstance(compatibility_value, dict):
        raise ReleaseError("Apple consumer compatibility must be one object")

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    checksum = output_dir / f"{archive.name}.sha256"
    checksum.write_text(f"{digest(archive)}  {archive.name}\n", encoding="utf-8")
    compatibility_copy = output_dir / "apple-consumer-compatibility.json"
    compatibility_copy.write_bytes(compatibility.read_bytes())
    manifest = output_dir / "apple-publication-manifest.json"
    value = {
        "schema_version": 1,
        "project": "Streamscape Media",
        "version": version,
        "source_sha": source_sha,
        "package": APPLE_PACKAGE,
        "archive": archive.name,
        "archive_sha256": digest(archive),
        "consumer_compatibility_sha256": digest(compatibility_copy),
        "included_platforms": sorted(platforms),
        "native_runtime_boundary": boundary,
    }
    manifest.write_text(json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    files = [archive, checksum, compatibility_copy, manifest]
    missing = preflight(APPLE_PACKAGE, version, files)
    publish_missing(APPLE_PACKAGE, version, missing)
    output(
        source_sha=source_sha,
        package_name=APPLE_PACKAGE,
        archive_sha256=value["archive_sha256"],
        compatibility_sha256=value["consumer_compatibility_sha256"],
        manifest_sha256=digest(manifest),
        included_platforms_json=json.dumps(value["included_platforms"], separators=(",", ":")),
        native_runtime_boundary_json=json.dumps(boundary, sort_keys=True, separators=(",", ":")),
    )


def require_hex64(value: str, label: str) -> str:
    if not HEX64.fullmatch(value):
        raise ReleaseError(f"{label} must be one lowercase SHA-256")
    return value


def final(args: argparse.Namespace) -> None:
    version = require_version(args.version)
    source_sha = require_sha(args.source_sha)
    if args.maven_source_sha != source_sha or args.apple_source_sha != source_sha:
        raise ReleaseError("publication children do not match the tag-resolved source")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,127}", args.maven_publication_id):
        raise ReleaseError("Maven publication evidence identifier is invalid")
    platforms = json.loads(args.apple_platforms_json)
    boundary = json.loads(args.apple_boundary_json)
    if not isinstance(platforms, list) or not isinstance(boundary, list):
        raise ReleaseError("Apple publication outputs are invalid")
    release_tag = args.release_tag
    if release_tag not in (version, f"v{version}"):
        raise ReleaseError("release tag does not normalize to the requested stable version")
    value = {
        "schema_version": 1,
        "project": "Streamscape Media",
        "version": version,
        "tag": release_tag,
        "source_sha": source_sha,
        "android": {
            "distribution": "forgejo-maven",
            "group": "com.streamscape.media",
            "consumer_scope": ["android-mobile", "android-tv"],
            "publication_id": args.maven_publication_id,
            "evidence_archive_sha256": require_hex64(args.maven_archive_sha256, "Maven evidence archive"),
            "evidence_manifest_sha256": require_hex64(args.maven_manifest_sha256, "Maven evidence manifest"),
        },
        "apple": {
            "distribution": "forgejo-generic",
            "package": APPLE_PACKAGE,
            "archive": f"streamscape-media-{version}-apple-binary.zip",
            "archive_sha256": require_hex64(args.apple_archive_sha256, "Apple archive"),
            "consumer_compatibility_sha256": require_hex64(args.apple_compatibility_sha256, "Apple compatibility"),
            "publication_manifest_sha256": require_hex64(args.apple_manifest_sha256, "Apple manifest"),
            "included_platforms": platforms,
            "native_runtime_boundary": boundary,
        },
        "claim_boundaries": [
            "This publication is a supported-subset SDK release, not a full platform or engine parity claim.",
            "Android artifacts are library coordinates for mobile and TV consumers; runtime availability remains descriptor-gated.",
            "Apple binary publication preserves the product-derived native runtime exclusion boundary.",
            "Physical output, Cast/AirPlay, and universal-media support are not certified by artifact publication.",
        ],
    }
    manifest = Path(args.manifest)
    manifest.write_text(json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    missing = preflight(RELEASE_PACKAGE, version, [manifest])
    publish_missing(RELEASE_PACKAGE, version, missing)
    output(manifest_sha256=digest(manifest))


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="command", required=True)
    a = sub.add_parser("apple")
    a.add_argument("--version", required=True)
    a.add_argument("--source-sha", required=True)
    a.add_argument("--archive", required=True)
    a.add_argument("--compatibility", required=True)
    a.add_argument("--output-dir", required=True)
    a.set_defaults(func=apple)
    f = sub.add_parser("final")
    for name in (
        "version", "release_tag", "source_sha", "maven_source_sha", "apple_source_sha", "maven_publication_id",
        "maven_archive_sha256", "maven_manifest_sha256", "apple_archive_sha256",
        "apple_compatibility_sha256", "apple_manifest_sha256", "apple_platforms_json", "apple_boundary_json",
        "manifest",
    ):
        f.add_argument("--" + name.replace("_", "-"), required=True)
    f.set_defaults(func=final)
    return p


def main() -> int:
    args = parser().parse_args()
    args.func(args)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ReleaseError, OSError, ValueError, json.JSONDecodeError, zipfile.BadZipFile) as error:
        print(f"Streamscape Media publication failed: {error}", file=sys.stderr)
        raise SystemExit(1)
