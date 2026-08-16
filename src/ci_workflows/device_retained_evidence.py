"""Bounded retained product-evidence ingestion for physical-device validation."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Mapping

from .device_contract_common import require, safe_relative
from .physical_log_policy import validate_durable_text

MAX_RETAINED_EVIDENCE_BYTES = 1024 * 1024
_ALLOWED_MEDIA_TYPES = {"application/json", "text/plain"}


def _bounded_retained_path(source_root: Path, relative_path: str) -> Path:
    normalized = safe_relative(relative_path, "evidence_policy_failed")
    require(normalized == relative_path, "evidence_policy_failed")
    target = source_root.joinpath(*Path(normalized).parts)
    current = source_root
    for part in Path(normalized).parts:
        current /= part
        require(not current.is_symlink(), "evidence_policy_failed")
    require(target.is_file() and not target.is_symlink(), "evidence_policy_failed")
    return target


def inspect_retained_evidence(
    *,
    contract_root: Path,
    source_root: Path,
    relative_path: str,
    media_type: str,
) -> dict[str, object]:
    """Validate one fixed redacted handoff and return metadata only.

    The content remains inside the product checkout and is never projected into
    workflow outputs. The caller receives only basename, media type, byte count,
    and SHA-256 so later cleanup can remove the checkout without losing the
    bounded proof identity.
    """

    require(media_type in _ALLOWED_MEDIA_TYPES, "evidence_policy_failed")
    target = _bounded_retained_path(source_root, relative_path)
    try:
        size = target.stat().st_size
        require(0 < size <= MAX_RETAINED_EVIDENCE_BYTES, "evidence_policy_failed")
        payload = target.read_bytes()
    except OSError as error:
        raise RuntimeError("evidence_policy_failed") from error
    require(len(payload) == size, "evidence_policy_failed")
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as error:
        raise RuntimeError("evidence_policy_failed") from error
    validate_durable_text(text, contract_root=contract_root)

    if media_type == "application/json":
        try:
            document = json.loads(text)
        except json.JSONDecodeError as error:
            raise RuntimeError("evidence_policy_failed") from error
        require(isinstance(document, Mapping), "evidence_policy_failed")
    else:
        require("\x00" not in text, "evidence_policy_failed")

    return {
        "name": target.name,
        "media_type": media_type,
        "bytes": size,
        "sha256": hashlib.sha256(payload).hexdigest(),
    }
