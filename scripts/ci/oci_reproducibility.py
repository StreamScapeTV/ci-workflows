#!/usr/bin/env python3
"""Compare raw config blobs in two exact amd64/arm64 OCI layouts."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

EXPECTED_PLATFORMS = ("linux/amd64", "linux/arm64/v8")
INDEX_MEDIA_TYPE = "application/vnd.oci.image.index.v1+json"
MANIFEST_MEDIA_TYPE = "application/vnd.oci.image.manifest.v1+json"
CONFIG_MEDIA_TYPE = "application/vnd.oci.image.config.v1+json"
MAX_JSON_BYTES = 16 * 1024 * 1024


class ReproducibilityError(ValueError):
    pass


def _mapping(value: Any, description: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ReproducibilityError(f"{description} must be an object")
    return value


def _items(value: Any, description: str) -> list[Any]:
    if not isinstance(value, list):
        raise ReproducibilityError(f"{description} must be an array")
    return value


def _digest(value: Any, description: str) -> tuple[str, str]:
    if not isinstance(value, str) or not value.startswith("sha256:"):
        raise ReproducibilityError(f"{description} must use sha256")
    encoded = value[7:]
    if len(encoded) != 64:
        raise ReproducibilityError(f"{description} has invalid sha256 length")
    try:
        bytes.fromhex(encoded)
    except ValueError as exc:
        raise ReproducibilityError(f"{description} has invalid sha256 encoding") from exc
    return value, encoded


def _platform(value: Any) -> str:
    value = _mapping(value, "manifest platform")
    if value.get("os") != "linux":
        raise ReproducibilityError(f"unexpected platform OS: {value.get('os')!r}")
    architecture = value.get("architecture")
    variant = value.get("variant")
    if architecture == "amd64" and variant in (None, ""):
        return "linux/amd64"
    if architecture == "arm64" and variant == "v8":
        return "linux/arm64/v8"
    raise ReproducibilityError(
        f"unexpected platform: architecture={architecture!r} variant={variant!r}"
    )


def _descriptor(value: Any, description: str) -> dict[str, Any]:
    value = _mapping(value, description)
    media_type = value.get("mediaType")
    if not isinstance(media_type, str) or not media_type:
        raise ReproducibilityError(f"{description} has no mediaType")
    digest, encoded = _digest(value.get("digest"), f"{description} digest")
    size = value.get("size")
    if not isinstance(size, int) or isinstance(size, bool) or size < 0:
        raise ReproducibilityError(f"{description} has invalid size")
    platform = _platform(value["platform"]) if "platform" in value else None
    return {
        "media_type": media_type,
        "digest": digest,
        "encoded_digest": encoded,
        "size": size,
        "platform": platform,
    }


def _json(data: bytes, description: str) -> dict[str, Any]:
    try:
        return _mapping(json.loads(data), description)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReproducibilityError(f"{description} is not valid JSON") from exc


def _file_bytes(path: Path, description: str) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise ReproducibilityError(f"missing regular {description}")
    if path.stat().st_size > MAX_JSON_BYTES:
        raise ReproducibilityError(f"{description} exceeds bounded JSON size")
    return path.read_bytes()


def _blob(layout: Path, descriptor: dict[str, Any], description: str) -> bytes:
    path = layout / "blobs" / "sha256" / descriptor["encoded_digest"]
    raw = _file_bytes(path, description)
    if len(raw) != descriptor["size"]:
        raise ReproducibilityError(f"OCI blob size mismatch for {descriptor['digest']}")
    if "sha256:" + hashlib.sha256(raw).hexdigest() != descriptor["digest"]:
        raise ReproducibilityError(f"OCI blob digest mismatch for {descriptor['digest']}")
    return raw


def _document(layout: Path, descriptor: dict[str, Any], description: str) -> dict[str, Any]:
    return _json(_blob(layout, descriptor, description), description)


def inspect_layout(path: Path) -> dict[str, dict[str, Any]]:
    layout = path.resolve()
    if not layout.is_dir():
        raise ReproducibilityError(f"OCI layout does not exist: {path}")

    marker = _json(_file_bytes(layout / "oci-layout", "oci-layout"), "oci-layout")
    if marker.get("imageLayoutVersion") != "1.0.0":
        raise ReproducibilityError("unsupported OCI image layout version")
    index = _json(_file_bytes(layout / "index.json", "index.json"), "index.json")
    if index.get("schemaVersion") != 2:
        raise ReproducibilityError("index.json has unsupported schemaVersion")

    platforms: dict[str, dict[str, Any]] = {}

    def walk(raw_descriptor: Any) -> None:
        descriptor = _descriptor(raw_descriptor, "OCI descriptor")
        media_type = descriptor["media_type"]
        if media_type == INDEX_MEDIA_TYPE:
            child_index = _document(layout, descriptor, "nested OCI index")
            if child_index.get("schemaVersion") != 2:
                raise ReproducibilityError("nested OCI index has unsupported schemaVersion")
            children = _items(child_index.get("manifests"), "nested OCI index manifests")
            if not children:
                raise ReproducibilityError("nested OCI index has no manifests")
            for child in children:
                walk(child)
            return
        if media_type != MANIFEST_MEDIA_TYPE:
            raise ReproducibilityError(f"unsupported OCI descriptor mediaType: {media_type}")

        platform = descriptor["platform"]
        if platform is None:
            raise ReproducibilityError("terminal image manifest descriptor has no platform")
        if platform in platforms:
            raise ReproducibilityError(f"duplicate platform {platform}")
        manifest = _document(layout, descriptor, f"manifest {platform}")
        if manifest.get("schemaVersion") != 2:
            raise ReproducibilityError(f"manifest {platform} has unsupported schemaVersion")
        config = _descriptor(manifest.get("config"), f"config {platform}")
        if config["media_type"] != CONFIG_MEDIA_TYPE:
            raise ReproducibilityError(f"config {platform} is not OCI image config")
        config_bytes = _blob(layout, config, f"config {platform}")
        _json(config_bytes, f"config {platform}")
        platforms[platform] = {
            "manifest_digest": descriptor["digest"],
            "config_digest": config["digest"],
            "config_bytes": config_bytes,
        }

    roots = _items(index.get("manifests"), "index.json manifests")
    if not roots:
        raise ReproducibilityError("index.json has no manifests")
    for root in roots:
        walk(root)

    actual = set(platforms)
    expected = set(EXPECTED_PLATFORMS)
    if actual != expected:
        raise ReproducibilityError(
            f"platform set mismatch: expected={sorted(expected)} actual={sorted(actual)}"
        )
    return platforms


def compare_layouts(left_path: Path, right_path: Path) -> dict[str, Any]:
    left = inspect_layout(left_path)
    right = inspect_layout(right_path)
    result: dict[str, Any] = {}
    for platform in EXPECTED_PLATFORMS:
        if left[platform]["config_bytes"] != right[platform]["config_bytes"]:
            raise ReproducibilityError(f"raw config bytes mismatch for {platform}")
        if left[platform]["config_digest"] != right[platform]["config_digest"]:
            raise ReproducibilityError(f"config SHA-256 mismatch for {platform}")
        result[platform] = {
            "config_digest": left[platform]["config_digest"],
            "config_raw_sha256": "sha256:" + hashlib.sha256(left[platform]["config_bytes"]).hexdigest(),
            "config_bytes_identical": True,
        }
    return {
        "schema_version": 1,
        "status": "reproducible",
        "platforms_expected": list(EXPECTED_PLATFORMS),
        "platforms": result,
    }


def _main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    compare = subparsers.add_parser("compare")
    compare.add_argument("--left-layout", required=True, type=Path)
    compare.add_argument("--right-layout", required=True, type=Path)
    compare.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        result = compare_layouts(args.left_layout, args.right_layout)
    except ReproducibilityError as exc:
        print(f"OCI reproducibility comparison failed: {exc}")
        return 2
    rendered = json.dumps(result, sort_keys=True, separators=(",", ":")) + "\n"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
