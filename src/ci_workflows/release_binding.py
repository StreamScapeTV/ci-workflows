"""Derive exact digest-pinned chart image bindings from OCI read-back outputs."""
from __future__ import annotations

import json
import re
from typing import Any, Mapping

from .release_types import ReleaseError


TARGET = re.compile(r"^[a-z0-9]+(?:[._-][a-z0-9]+)*$")
REPOSITORY = re.compile(r"^ghcr\.io/streamscapetv/[a-z0-9._/-]+$")
DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")


def require(condition: bool, code: str) -> None:
    if not condition:
        raise ReleaseError(code)


def _mapping(value: str, code: str) -> Mapping[str, Any]:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as error:
        raise ReleaseError(code) from error
    require(isinstance(parsed, Mapping) and 0 < len(parsed) <= 16, code)
    return parsed


def digest_pinned_image_references(
    repositories_json: str,
    manifest_digests_json: str,
) -> dict[str, str]:
    repositories = _mapping(repositories_json, "image_repository_map_rejected")
    digests = _mapping(manifest_digests_json, "image_digest_map_rejected")
    require(set(repositories) == set(digests), "image_target_mismatch")
    result: dict[str, str] = {}
    for target in sorted(repositories):
        repository = repositories[target]
        digest = digests[target]
        require(
            isinstance(target, str)
            and TARGET.fullmatch(target) is not None
            and isinstance(repository, str)
            and REPOSITORY.fullmatch(repository) is not None,
            "image_repository_map_rejected",
        )
        require(
            isinstance(digest, str) and DIGEST.fullmatch(digest) is not None,
            "image_digest_map_rejected",
        )
        result[target] = f"{repository}@{digest}"
    return result


def image_reference_bundle(
    *,
    repositories_json: str,
    manifest_digests_json: str,
    version_references_json: str,
    source_references_json: str,
) -> tuple[dict[str, str], str]:
    repositories = _mapping(repositories_json, "image_repository_map_rejected")
    digest_references = digest_pinned_image_references(
        repositories_json,
        manifest_digests_json,
    )
    versions = _mapping(version_references_json, "image_reference_map_rejected")
    sources = _mapping(source_references_json, "image_reference_map_rejected")
    expected = set(digest_references)
    require(
        set(repositories) == expected
        and set(versions) == expected
        and set(sources) == expected,
        "image_target_mismatch",
    )
    for mapping in (versions, sources):
        for target, value in mapping.items():
            repository = repositories[target]
            require(
                isinstance(repository, str)
                and REPOSITORY.fullmatch(repository) is not None
                and isinstance(value, str)
                and 0 < len(value) <= 512
                and not any(character.isspace() for character in value)
                and ":latest" not in value.casefold(),
                "image_reference_map_rejected",
            )
            require(
                value.startswith(f"{repository}:"),
                "image_reference_map_rejected",
            )
    bundle = {
        "digest_references": digest_references,
        "source_references": dict(sorted((str(k), str(v)) for k, v in sources.items())),
        "version_references": dict(sorted((str(k), str(v)) for k, v in versions.items())),
    }
    rendered = json.dumps(bundle, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return digest_references, rendered
