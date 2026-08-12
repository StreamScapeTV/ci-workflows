"""Normalize redacted dependency evidence for the immutable release manifest."""
from __future__ import annotations

import json
import re
from typing import Any, Mapping

from .release_types import ReleaseError


SAFE_ID = re.compile(r"^[A-Za-z0-9._:@/+\-]{0,256}$")
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


def _safe_id(value: str, code: str, *, required: bool = False) -> str:
    candidate = value.strip()
    require((not required or bool(candidate)) and SAFE_ID.fullmatch(candidate) is not None, code)
    return candidate


def image_publication_evidence(
    *,
    platform_digests_json: str,
    evidence_id: str,
    replayed: str,
    canary_id: str = "",
    previous_known_good: str = "",
    rollback_id: str = "",
) -> dict[str, Any]:
    platforms = _mapping(platform_digests_json, "image_evidence_rejected")
    evidence = _safe_id(evidence_id, "image_evidence_rejected", required=True)
    require(replayed in {"true", "false"}, "image_evidence_rejected")
    selection = {
        "canary_id": _safe_id(canary_id, "image_evidence_rejected"),
        "previous_known_good": _safe_id(previous_known_good, "image_evidence_rejected"),
        "rollback_id": _safe_id(rollback_id, "image_evidence_rejected"),
    }
    populated = [bool(value) for value in selection.values()]
    require(all(populated) or not any(populated), "image_evidence_rejected")
    return {
        "evidence_id": evidence,
        "platform_digests": platforms,
        "replayed": replayed == "true",
        "flux_selection": selection if all(populated) else None,
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
