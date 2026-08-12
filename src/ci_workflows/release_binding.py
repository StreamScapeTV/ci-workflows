"""Derive exact chart bindings from the registered oci.publish read-back outputs."""
from __future__ import annotations

import json
import re
from typing import Any, Mapping

from .release_contract import validate_release_version
from .release_types import ReleaseError


TARGET = re.compile(r"^[a-z0-9]+(?:[._-][a-z0-9]+)*$")
REPOSITORY = re.compile(r"^ghcr\.io/streamscapetv/[a-z0-9._/-]+$")
DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
SAFE_SELECTION_ID = re.compile(r"^[A-Za-z0-9._:@/+\-]{1,256}$")


def require(condition: bool, code: str) -> None:
    if not condition:
        raise ReleaseError(code)


def _mapping(value: str, code: str) -> Mapping[str, Any]:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as error:
        raise ReleaseError(code) from error
    require(isinstance(parsed, Mapping) and 0 < len(parsed) <= 32, code)
    return parsed


def _selection(value: Any) -> dict[str, str] | None:
    if value is None:
        return None
    require(isinstance(value, Mapping), "image_selection_rejected")
    require(
        set(value) == {"canary_id", "previous_known_good", "rollback_id"},
        "image_selection_rejected",
    )
    result: dict[str, str] = {}
    for key in ("canary_id", "previous_known_good", "rollback_id"):
        item = value[key]
        require(
            isinstance(item, str) and SAFE_SELECTION_ID.fullmatch(item) is not None,
            "image_selection_rejected",
        )
        result[key] = item
    return result


def image_reference_bundle(
    *,
    image_digest_json: str,
    immutable_references_json: str,
    expected_source_sha: str,
    expected_release_version: str,
) -> tuple[dict[str, str], dict[str, str], str, dict[str, str] | None]:
    require(FULL_SHA.fullmatch(expected_source_sha) is not None, "image_release_identity_rejected")
    version = validate_release_version(expected_release_version)
    digests = _mapping(image_digest_json, "image_digest_map_rejected")
    immutable = _mapping(immutable_references_json, "image_reference_map_rejected")
    require(set(immutable).issubset({"targets", "release", "flux"}), "image_reference_map_rejected")
    require({"targets", "release"}.issubset(immutable), "image_reference_map_rejected")
    release = immutable["release"]
    targets = immutable["targets"]
    require(isinstance(release, Mapping) and isinstance(targets, Mapping), "image_reference_map_rejected")
    require(set(release) == {"source_sha", "version"}, "image_reference_map_rejected")
    require(
        release.get("source_sha") == expected_source_sha
        and release.get("version") == version,
        "image_release_identity_mismatch",
    )
    require(0 < len(targets) <= 16 and set(targets) == set(digests), "image_target_mismatch")

    normalized_digests: dict[str, str] = {}
    digest_references: dict[str, str] = {}
    version_references: dict[str, str] = {}
    source_references: dict[str, str] = {}
    for target in sorted(targets):
        row = targets[target]
        digest = digests[target]
        require(
            isinstance(target, str)
            and TARGET.fullmatch(target) is not None
            and isinstance(row, Mapping)
            and set(row) == {"repository", "version", "source_sha", "manifest_digest"},
            "image_reference_map_rejected",
        )
        repository = row["repository"]
        version_reference = row["version"]
        source_reference = row["source_sha"]
        manifest_digest = row["manifest_digest"]
        require(
            isinstance(repository, str) and REPOSITORY.fullmatch(repository) is not None,
            "image_reference_map_rejected",
        )
        require(
            isinstance(digest, str)
            and DIGEST.fullmatch(digest) is not None
            and manifest_digest == digest,
            "image_digest_map_rejected",
        )
        require(
            isinstance(version_reference, str)
            and version_reference == f"{repository}:{version}"
            and isinstance(source_reference, str)
            and source_reference == f"{repository}:sha-{expected_source_sha}"
            and ":latest" not in version_reference.casefold()
            and ":latest" not in source_reference.casefold(),
            "image_reference_map_rejected",
        )
        normalized_digests[target] = digest
        digest_references[target] = f"{repository}@{digest}"
        version_references[target] = version_reference
        source_references[target] = source_reference

    selection = _selection(immutable.get("flux"))
    bundle = {
        "digest_references": digest_references,
        "source_references": source_references,
        "version_references": version_references,
    }
    rendered = json.dumps(bundle, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return normalized_digests, digest_references, rendered, selection
