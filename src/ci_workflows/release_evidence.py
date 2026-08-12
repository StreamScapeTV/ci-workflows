"""Normalize redacted dependency evidence for the immutable release manifest."""
from __future__ import annotations

import json
import re
from typing import Any, Mapping

from .release_types import ReleaseError


MAX_JSON_BYTES = 64 * 1024
DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
TARGET = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
PLATFORM = re.compile(
    r"^[a-z0-9][a-z0-9._-]{0,63}/[a-z0-9][a-z0-9._-]{0,63}"
    r"(?:/[a-z0-9][a-z0-9._-]{0,63})?$"
)


def require(condition: bool, code: str) -> None:
    if not condition:
        raise ReleaseError(code)


def _mapping(value: str, code: str) -> Mapping[str, Any]:
    require(isinstance(value, str), code)
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError) as error:
        raise ReleaseError(code) from error
    require(isinstance(parsed, Mapping), code)
    require(
        len(json.dumps(parsed, separators=(",", ":")).encode("utf-8"))
        <= MAX_JSON_BYTES,
        code,
    )
    return parsed


def _image_digests(value: Mapping[str, Any]) -> dict[str, str]:
    require(0 < len(value) <= 16, "image_evidence_rejected")
    result: dict[str, str] = {}
    for target, digest in value.items():
        require(
            isinstance(target, str)
            and TARGET.fullmatch(target) is not None
            and isinstance(digest, str)
            and DIGEST.fullmatch(digest) is not None,
            "image_evidence_rejected",
        )
        result[target] = digest
    return dict(sorted(result.items()))


def _platform_digests(
    value: Mapping[str, Any], *, expected_targets: set[str]
) -> dict[str, Any]:
    require(set(value) == expected_targets, "image_evidence_rejected")
    normalized: dict[str, Any] = {}
    for target in sorted(expected_targets):
        platforms = value.get(target)
        require(
            isinstance(platforms, Mapping) and 0 < len(platforms) <= 16,
            "image_evidence_rejected",
        )
        target_platforms: dict[str, Any] = {}
        for platform, evidence in platforms.items():
            require(
                isinstance(platform, str)
                and PLATFORM.fullmatch(platform) is not None
                and isinstance(evidence, Mapping)
                and 0 < len(evidence) <= 8,
                "image_evidence_rejected",
            )
            manifest_digest = evidence.get("manifest_digest")
            require(
                isinstance(manifest_digest, str)
                and DIGEST.fullmatch(manifest_digest) is not None,
                "image_evidence_rejected",
            )
            target_platforms[platform] = dict(evidence)
        normalized[target] = dict(sorted(target_platforms.items()))
    return normalized


def image_publication_evidence(
    *,
    result: str,
    image_digest_json: str,
    platform_digests_json: str,
    immutable_references_json: str,
) -> dict[str, Any]:
    require(result == "success", "image_evidence_rejected")
    digests = _image_digests(
        _mapping(image_digest_json, "image_evidence_rejected")
    )
    platforms = _platform_digests(
        _mapping(platform_digests_json, "image_evidence_rejected"),
        expected_targets=set(digests),
    )
    immutable = _mapping(immutable_references_json, "image_evidence_rejected")
    require(bool(immutable), "image_evidence_rejected")
    return {
        "image_digest": digests,
        "immutable_references": immutable,
        "platform_digests": platforms,
        "result": "success",
    }


def chart_publication_evidence(
    *,
    result: str,
    immutable_references_json: str,
) -> dict[str, Any]:
    require(result == "success", "chart_evidence_rejected")
    read_back = _mapping(immutable_references_json, "chart_evidence_rejected")
    require(bool(read_back), "chart_evidence_rejected")
    return {
        "read_back": read_back,
        "result": "success",
    }


def evidence_json(value: Mapping[str, Any]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
