"""Normalize redacted dependency evidence for the immutable release manifest."""
from __future__ import annotations

import json
from typing import Any, Mapping

from .release_types import ReleaseError


MAX_JSON_BYTES = 64 * 1024


def require(condition: bool, code: str) -> None:
    if not condition:
        raise ReleaseError(code)


def _mapping(value: str, code: str) -> Mapping[str, Any]:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as error:
        raise ReleaseError(code) from error
    require(isinstance(parsed, Mapping), code)
    require(len(json.dumps(parsed, separators=(",", ":")).encode("utf-8")) <= MAX_JSON_BYTES, code)
    return parsed


def image_publication_evidence(
    *,
    result: str,
    image_digest_json: str,
    platform_digests_json: str,
    immutable_references_json: str,
) -> dict[str, Any]:
    require(result == "success", "image_evidence_rejected")
    digests = _mapping(image_digest_json, "image_evidence_rejected")
    platforms = _mapping(platform_digests_json, "image_evidence_rejected")
    immutable = _mapping(immutable_references_json, "image_evidence_rejected")
    require(bool(digests) and bool(platforms) and bool(immutable), "image_evidence_rejected")
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
    return {
        "read_back": read_back,
        "result": "success",
    }


def evidence_json(value: Mapping[str, Any]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
