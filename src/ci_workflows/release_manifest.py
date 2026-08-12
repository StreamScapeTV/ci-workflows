"""Deterministic release manifests and immutable publication identities."""
from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping

from .release_contract import validate_release_version
from .release_types import PublicationIdentity, ReleaseError, ReleasePlan


FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
DIGEST = re.compile(r"^(?:sha256:)?[0-9a-f]{64}$")
IDENTIFIER = re.compile(r"^[a-z][a-z0-9-]{1,63}$")
LATEST_REFERENCE = re.compile(r"(?i)(?:^|[:/])latest(?:@|$)")
SENSITIVE_KEY = re.compile(r"(?i)(?:^|[_-])(authorization|credential|password|secret|token)(?:$|[_-])")
SENSITIVE_VALUE = re.compile(
    r"(?i)(?:gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{30,}|"
    r"-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----|authorization\s*[:=]|"
    r"password\s*[:=]|secret\s*[:=]|token\s*[:=])"
)
MAX_REFERENCE_LENGTH = 512
MAX_EVIDENCE_BYTES = 64 * 1024


def require(condition: bool, code: str) -> None:
    if not condition:
        raise ReleaseError(code)


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def sha256_text(value: str) -> str:
    require(isinstance(value, str), "canonical_text_rejected")
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _sha256_file(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as error:
        raise ReleaseError("release_surface_unavailable") from error


def _sha256_bundle(root: Path, paths: Iterable[Path]) -> str:
    digest = hashlib.sha256()
    found = False
    for path in sorted(paths, key=lambda item: item.as_posix()):
        try:
            relative = path.relative_to(root).as_posix()
            payload = path.read_bytes()
        except (OSError, ValueError) as error:
            raise ReleaseError("release_surface_unavailable") from error
        found = True
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(payload)
        digest.update(b"\0")
    require(found, "release_surface_unavailable")
    return digest.hexdigest()


def _timestamp(value: str) -> str:
    require(isinstance(value, str), "source_timestamp_rejected")
    candidate = value.strip()
    try:
        parsed = datetime.fromisoformat(candidate.replace("Z", "+00:00"))
    except ValueError as error:
        raise ReleaseError("source_timestamp_rejected") from error
    require(parsed.tzinfo is not None, "source_timestamp_rejected")
    return candidate


def normalize_digest(value: str) -> str:
    require(isinstance(value, str), "publication_digest_rejected")
    candidate = value.strip()
    require(DIGEST.fullmatch(candidate) is not None, "publication_digest_rejected")
    return candidate if candidate.startswith("sha256:") else f"sha256:{candidate}"


def _digest_map(digest: str, digests_json: str) -> tuple[str, dict[str, str]]:
    require(
        isinstance(digest, str) and isinstance(digests_json, str),
        "publication_digest_rejected",
    )
    if digests_json.strip():
        try:
            payload = json.loads(digests_json)
        except (TypeError, json.JSONDecodeError) as error:
            raise ReleaseError("publication_digest_rejected") from error
        require(
            isinstance(payload, Mapping) and 0 < len(payload) <= 16,
            "publication_digest_rejected",
        )
        normalized: dict[str, str] = {}
        for key, value in payload.items():
            require(
                isinstance(key, str)
                and 0 < len(key) <= 128
                and not any(character.isspace() for character in key)
                and isinstance(value, str),
                "publication_digest_rejected",
            )
            normalized[key] = normalize_digest(value)
        normalized = dict(sorted(normalized.items()))
        if digest.strip():
            primary = normalize_digest(digest)
            require(primary in set(normalized.values()), "publication_digest_mismatch")
        elif len(normalized) == 1:
            primary = next(iter(normalized.values()))
        else:
            primary = f"sha256:{sha256_text(canonical_json(normalized))}"
        return primary, normalized
    primary = normalize_digest(digest)
    return primary, {"primary": primary}


def _reference_key(key: str | None) -> bool:
    return bool(
        key is None
        or key in {"chart", "image", "reference", "immutable_reference"}
        or (isinstance(key, str) and "reference" in key)
    )


def _reference_values(value: Any, *, key: str | None = None) -> list[str]:
    references: list[str] = []
    if isinstance(value, str):
        candidate = value.strip()
        if _reference_key(key) and candidate:
            references.append(candidate)
        return references
    if isinstance(value, list):
        for item in value:
            references.extend(_reference_values(item, key=key))
        return references
    if isinstance(value, Mapping):
        parent_is_reference = key is not None and _reference_key(key)
        for child_key, child in value.items():
            if isinstance(child_key, str):
                references.extend(
                    _reference_values(child, key=key if parent_is_reference else child_key)
                )
        return references
    return references


def _evidence_is_safe(value: Any, *, key: str | None = None) -> bool:
    if key is not None and SENSITIVE_KEY.search(key):
        return False
    if isinstance(value, str):
        return len(value) <= 4096 and SENSITIVE_VALUE.search(value) is None
    if isinstance(value, Mapping):
        return len(value) <= 64 and all(
            isinstance(child_key, str)
            and len(child_key) <= 128
            and _evidence_is_safe(child, key=child_key)
            for child_key, child in value.items()
        )
    if isinstance(value, list):
        return len(value) <= 64 and all(
            _evidence_is_safe(item, key=key) for item in value
        )
    if isinstance(value, float):
        return math.isfinite(value)
    return value is None or isinstance(value, (bool, int))


def publication_identity(
    *,
    product_id: str,
    kind: str,
    digest: str = "",
    digests_json: str = "",
    immutable_references_json: str,
    evidence_json: str = "{}",
) -> PublicationIdentity:
    require(
        isinstance(product_id, str)
        and IDENTIFIER.fullmatch(product_id) is not None,
        "publication_product_rejected",
    )
    require(kind in {"oci-image", "helm-chart"}, "publication_kind_rejected")
    require(
        isinstance(immutable_references_json, str)
        and isinstance(evidence_json, str),
        "publication_evidence_rejected",
    )
    try:
        reference_payload = json.loads(immutable_references_json)
        evidence = json.loads(evidence_json or "{}")
    except (TypeError, json.JSONDecodeError) as error:
        raise ReleaseError("publication_evidence_rejected") from error
    require(isinstance(evidence, Mapping), "publication_evidence_rejected")
    require(
        len(evidence) <= 64
        and len(canonical_json(evidence).encode("utf-8")) <= MAX_EVIDENCE_BYTES
        and _evidence_is_safe(evidence),
        "publication_evidence_rejected",
    )
    references = sorted(set(_reference_values(reference_payload)))
    require(
        bool(references) and len(references) <= 16,
        "publication_reference_rejected",
    )
    require(
        all(
            0 < len(reference) <= MAX_REFERENCE_LENGTH
            and not any(character.isspace() for character in reference)
            and LATEST_REFERENCE.search(reference) is None
            for reference in references
        ),
        "publication_reference_rejected",
    )
    primary, digests = _digest_map(digest, digests_json)
    if kind == "helm-chart":
        require(
            isinstance(reference_payload, Mapping),
            "publication_reference_rejected",
        )
        chart_digest = reference_payload.get("chart_digest")
        require(isinstance(chart_digest, str), "publication_digest_rejected")
        require(
            normalize_digest(chart_digest) == primary,
            "publication_digest_mismatch",
        )
        read_back = evidence.get("read_back")
        if read_back is not None:
            require(
                read_back == reference_payload,
                "publication_evidence_mismatch",
            )
    return PublicationIdentity(
        product_id=product_id,
        kind=kind,
        digest=primary,
        digests=digests,
        immutable_references=tuple(references),
        evidence=dict(evidence),
    )


def _release_surface(root: Path) -> dict[str, Any]:
    workflow = root / ".github/workflows/reusable-release.yml"
    release_modules = (root / "src/ci_workflows").glob("release*.py")
    schema_paths = {
        "release-manifest": root / "contracts/release-manifest.schema.json",
        "flux-handoff": root / "contracts/flux-handoff.schema.json",
        "releases": root / "contracts/releases.json",
    }
    return {
        "workflow_apis": {
            "release.orchestrate": {
                "version": "1.0.0",
                "file": ".github/workflows/reusable-release.yml",
                "sha256": _sha256_file(workflow),
            }
        },
        "function_library": {
            "version": "1.0.0",
            "sha256": _sha256_bundle(root, release_modules),
        },
        "schemas": {
            name: _sha256_file(path) for name, path in sorted(schema_paths.items())
        },
        "action_lock": {
            "version": "resolve-release-tag-v1",
            "sha256": _sha256_file(root / "actions/resolve-release-tag/action.yml"),
        },
        "tool_lock": {
            "version": "validation-lock",
            "sha256": _sha256_file(root / "requirements/validation.lock"),
        },
        "runner_profiles": {
            "version": "semantic-runner-profiles",
            "sha256": _sha256_file(root / "RUNNERS.md"),
        },
    }


def build_release_manifest(
    *,
    root: Path,
    plan: ReleasePlan,
    release_version: str,
    source_sha: str,
    tag_object_sha: str,
    tag_commit_sha: str,
    source_timestamp: str,
    workflow_sha: str,
    image: PublicationIdentity,
    chart: PublicationIdentity,
) -> dict[str, Any]:
    version = validate_release_version(release_version)
    for value in (source_sha, tag_object_sha, tag_commit_sha, workflow_sha):
        require(
            isinstance(value, str) and FULL_SHA.fullmatch(value) is not None,
            "release_sha_rejected",
        )
    require(source_sha == tag_commit_sha, "tag_source_mismatch")
    require(
        image.product_id == plan.image_product_id and image.kind == "oci-image",
        "image_identity_mismatch",
    )
    require(
        chart.product_id == plan.chart_product_id and chart.kind == "helm-chart",
        "chart_identity_mismatch",
    )
    created_at = _timestamp(source_timestamp)
    surface = _release_surface(root.resolve())
    product_release = {
        "release_id": plan.release_id,
        "repository": plan.repository,
        "release_tag": version,
        "version": version,
        "source_sha": source_sha,
        "tag_object_sha": tag_object_sha,
        "tag_commit_sha": tag_commit_sha,
        "source_timestamp": created_at,
        "workflow_sha": workflow_sha,
        "replay_policy": "exact-match-only",
        "publications": {
            "image": image.as_manifest_dict(),
            "chart": chart.as_manifest_dict(),
        },
        "github_release": {
            "required": plan.github_release,
            "state": "pending-or-matching",
        },
        "flux_handoff": {
            "kind": plan.handoff_kind,
            "target_repository": plan.handoff_target_repository,
            "requested_action": plan.handoff_requested_action,
            "mutation_authorized": False,
        },
    }
    return {
        "schema_version": 1,
        "shared_release": {
            "kind": "product",
            "tag": version,
            "commit": source_sha,
            "created_at": created_at,
        },
        **surface,
        "consumers": [
            {
                "repository": plan.repository,
                "reference": source_sha,
                "products": sorted(plan.product_ids),
            }
        ],
        "product_release": product_release,
    }


def release_manifest_json(**kwargs: Any) -> tuple[str, str]:
    payload = build_release_manifest(**kwargs)
    rendered = canonical_json(payload)
    return rendered, sha256_text(rendered)


def publication_progress(*, image_result: str, chart_result: str) -> str:
    allowed = {"success", "failure", "skipped", "cancelled", "missing"}
    require(
        image_result in allowed and chart_result in allowed,
        "publication_progress_rejected",
    )
    if image_result == "success" and chart_result == "success":
        return "complete"
    if image_result == "success":
        return "image-published-awaiting-chart"
    if image_result in {"failure", "cancelled"}:
        return "image-publication-incomplete"
    return "publication-not-complete"
