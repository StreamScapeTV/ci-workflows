"""Fail-closed OCI publication guards over the issue-17 registry runtime."""
from __future__ import annotations

import hashlib
import json
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from . import oci_publish as _runtime
from . import oci_publish_assertions as _assertions
from .oci_types import is_exact_base_reference

OciPublishError = _runtime.OciPublishError
PublishPlan = _runtime.PublishPlan
PublishTarget = _runtime.PublishTarget
cleanup = _runtime.cleanup
publication_state_root = _runtime.publication_state_root
replay_decision = _runtime.replay_decision
residue = _runtime.residue

ROOT = Path(__file__).resolve().parents[2]
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_EVIDENCE_ID = re.compile(r"^[0-9a-f]{64}$")
_ABSENCE_MARKERS = (b"manifest unknown", b"manifest_unknown", b"name unknown")


@dataclass(frozen=True)
class _PublishPreflight:
    target: PublishTarget
    layout: Path
    local: Mapping[str, Any]
    assertions: Mapping[str, object]
    local_digest: str
    publish_version: bool
    publish_source: bool
    replayed: bool


@dataclass(frozen=True)
class _BuildEvidence:
    identity: Mapping[str, str]
    resolved_base_references: Mapping[str, tuple[str, ...]]


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise OciPublishError(code)


def _mapping(value: Any, code: str) -> Mapping[str, Any]:
    _require(isinstance(value, Mapping), code)
    return value


def _encoded_mapping(value: Any, code: str) -> Mapping[str, Any]:
    _require(isinstance(value, str), code)
    try:
        return _mapping(json.loads(value), code)
    except json.JSONDecodeError as error:
        raise OciPublishError(code) from error


def _build_identity(value: Any, plan: PublishPlan) -> Mapping[str, str]:
    identity = _mapping(value, "publication_state_missing")
    _require(
        set(identity) == {"source_sha", "product_id", "release_version", "evidence_id"}
        and identity.get("source_sha") == plan.admitted_sha
        and identity.get("product_id") == plan.product_id
        and identity.get("release_version") == plan.release_version
        and isinstance(identity.get("evidence_id"), str)
        and _EVIDENCE_ID.fullmatch(identity["evidence_id"]) is not None,
        "publication_state_missing",
    )
    return {key: str(identity[key]) for key in sorted(identity)}


def _base_references(value: Any) -> tuple[str, ...]:
    _require(
        isinstance(value, list)
        and bool(value)
        and all(is_exact_base_reference(item) for item in value),
        "build_evidence_mismatch",
    )
    return tuple(value)


def _load_build_evidence(
    build_root: Path,
    plan: PublishPlan,
    preflight: tuple[_PublishPreflight, ...],
) -> _BuildEvidence:
    """Bind exact source/base evidence to every inspected local manifest."""

    path = build_root / "result.json"
    try:
        mode = path.lstat().st_mode
        _require(stat.S_ISREG(mode) and not stat.S_ISLNK(mode), "build_evidence_missing")
        payload = _mapping(
            json.loads(path.read_text(encoding="utf-8")),
            "build_evidence_missing",
        )
    except (OSError, json.JSONDecodeError) as error:
        raise OciPublishError("build_evidence_missing") from error
    _require(
        payload.get("result") == "success"
        and payload.get("source_sha") == plan.admitted_sha
        and payload.get("product_id") == plan.product_id
        and payload.get("release_version") == plan.release_version
        and payload.get("clean_tree") == "true"
        and payload.get("artifact_exception_used") == "false",
        "build_evidence_mismatch",
    )
    evidence_id = payload.get("evidence_id")
    _require(
        isinstance(evidence_id, str)
        and _EVIDENCE_ID.fullmatch(evidence_id) is not None,
        "build_evidence_mismatch",
    )
    manifests = _encoded_mapping(
        payload.get("publication_manifest_digests_json"),
        "build_evidence_mismatch",
    )
    bases = _encoded_mapping(
        payload.get("resolved_base_references_json"), "build_evidence_mismatch"
    )
    expected_targets = {item.target.target_id for item in preflight}
    _require(
        set(manifests) == expected_targets and set(bases) == expected_targets,
        "build_evidence_mismatch",
    )
    resolved: dict[str, tuple[str, ...]] = {}
    for item in preflight:
        target_id = item.target.target_id
        _require(
            manifests.get(target_id) == item.local_digest,
            "build_evidence_mismatch",
        )
        resolved[target_id] = _base_references(bases[target_id])
    return _BuildEvidence(
        identity={
            "source_sha": plan.admitted_sha,
            "product_id": plan.product_id,
            "release_version": plan.release_version,
            "evidence_id": evidence_id,
        },
        resolved_base_references=resolved,
    )


def _validate_layout_marker(layout: Path) -> None:
    marker = layout / "oci-layout"
    try:
        value = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise OciPublishError("oci_layout_malformed") from error
    _require(
        value == {"imageLayoutVersion": "1.0.0"}
        and marker.is_file()
        and not marker.is_symlink(),
        "oci_layout_malformed",
    )


def inspect_layout(
    layout: Path, target: PublishTarget, ref_name: str
) -> Mapping[str, Any]:
    """Inspect an OCI layout only after validating the OCI layout marker."""

    _validate_layout_marker(layout)
    return _runtime.inspect_layout(layout, target, ref_name)


def _inspect_remote_digest(reference: str, authfile: Path) -> str | None:
    """Return None only for explicit registry manifest/name absence."""

    try:
        result = _runtime._run(  # noqa: SLF001 - bounded shared runtime primitive
            [
                "skopeo",
                "inspect",
                "--authfile",
                str(authfile),
                "--raw",
                f"docker://{reference}",
            ],
            check=False,
        )
    except OSError as error:
        raise OciPublishError("registry_inspection_failed") from error
    if result.returncode == 0:
        _require(bool(result.stdout), "registry_inspection_failed")
        return "sha256:" + hashlib.sha256(result.stdout).hexdigest()
    stderr = result.stderr.lower()
    if any(marker in stderr for marker in _ABSENCE_MARKERS):
        return None
    raise OciPublishError("registry_inspection_failed")


def _publication_allowed(plan: PublishPlan, environment: Mapping[str, str]) -> bool:
    """Return true only for an exact version-tag push; manual calls are read-only."""

    event = environment.get("GITHUB_EVENT_NAME", "")
    if event == "push":
        _require(
            environment.get("GITHUB_REF_TYPE") == "tag"
            and environment.get("GITHUB_REF_NAME") == plan.release_version
            and environment.get("GITHUB_REF") == f"refs/tags/{plan.release_version}",
            "publication_ref_forbidden",
        )
        return True
    if event == "workflow_dispatch":
        return False
    raise OciPublishError("publication_untrusted")


def authenticate(
    plan: PublishPlan,
    environment: Mapping[str, str],
    username: str,
    token: str,
) -> dict[str, str]:
    """Create registry auth only after the caller event is publication-eligible."""

    _publication_allowed(plan, environment)
    return _runtime.authenticate(plan, environment, username, token)


def _preflight_publish(
    plan: PublishPlan,
    contract_root: Path,
    build_root: Path,
    authfile: Path,
    *,
    allow_publish: bool,
) -> tuple[_PublishPreflight, ...]:
    """Validate every target and decide all remote writes without mutating them."""

    decisions: list[_PublishPreflight] = []
    for target in plan.targets:
        layout = build_root / "layouts" / target.target_id
        local = inspect_layout(layout, target, "validation")
        assertion_evidence = _assertions.assert_filesystem_contract(
            contract_root, plan, target, layout
        )
        local_digest = str(local["manifest_digest"])
        _require(_DIGEST.fullmatch(local_digest) is not None, "oci_digest_mismatch")
        version_digest = _inspect_remote_digest(target.version_reference, authfile)
        source_digest = _inspect_remote_digest(target.source_reference, authfile)
        publish_version, publish_source, replayed = replay_decision(
            local_digest, version_digest, source_digest
        )
        if not allow_publish and (publish_version or publish_source):
            raise OciPublishError("remote_reference_missing")
        decisions.append(
            _PublishPreflight(
                target=target,
                layout=layout,
                local=local,
                assertions=assertion_evidence,
                local_digest=local_digest,
                publish_version=publish_version,
                publish_source=publish_source,
                replayed=replayed,
            )
        )
    return tuple(decisions)


def publish(
    plan: PublishPlan,
    environment: Mapping[str, str],
    *,
    allow_publish: bool | None = None,
    repository_root: Path | None = None,
) -> dict[str, str]:
    """Publish missing immutable refs or verify an existing pair without writes."""

    if allow_publish is None:
        allow_publish = _publication_allowed(plan, environment)
    _require(isinstance(allow_publish, bool), "invalid_request")
    contract_root = ROOT if repository_root is None else repository_root
    root = publication_state_root(environment)
    authfile = _runtime._secure_existing_authfile(root)  # noqa: SLF001
    build_root = _runtime.build_state_root(environment)
    preflight = _preflight_publish(
        plan,
        contract_root,
        build_root,
        authfile,
        allow_publish=allow_publish,
    )
    build_evidence = _load_build_evidence(build_root, plan, preflight)
    results: dict[str, Any] = {}
    any_replayed = any(item.replayed for item in preflight)
    any_published = False
    for item in preflight:
        target = item.target
        if item.publish_version:
            observed = _inspect_remote_digest(target.version_reference, authfile)
            if observed is None:
                _runtime._copy(  # noqa: SLF001
                    f"oci:{item.layout}:validation",
                    f"docker://{target.version_reference}",
                    authfile,
                )
                any_published = True
            else:
                _require(observed == item.local_digest, "immutable_reference_conflict")
                any_replayed = True
        if item.publish_source:
            observed = _inspect_remote_digest(target.source_reference, authfile)
            if observed is None:
                _runtime._copy(  # noqa: SLF001
                    f"oci:{item.layout}:validation",
                    f"docker://{target.source_reference}",
                    authfile,
                )
                any_published = True
            else:
                _require(observed == item.local_digest, "immutable_reference_conflict")
                any_replayed = True
        verified_version = _inspect_remote_digest(target.version_reference, authfile)
        verified_source = _inspect_remote_digest(target.source_reference, authfile)
        _require(
            verified_version == item.local_digest
            and verified_source == item.local_digest,
            "registry_digest_mismatch",
        )
        results[target.target_id] = {
            "repository": target.registry_repository,
            "version_reference": target.version_reference,
            "source_reference": target.source_reference,
            "manifest_digest": item.local_digest,
            "resolved_base_references": list(
                build_evidence.resolved_base_references[target.target_id]
            ),
            "assertions": item.assertions,
            "local": item.local,
            "replayed": item.replayed,
        }
    _runtime._write_state(  # noqa: SLF001
        root / "publication.json",
        {"build": build_evidence.identity, "targets": results},
    )
    return {
        "result": "published" if any_published else "replayed",
        "manifest_digests_json": json.dumps(
            {key: row["manifest_digest"] for key, row in sorted(results.items())},
            separators=(",", ":"),
        ),
        "replayed": str(any_replayed or not any_published).lower(),
        "failure_code": "",
    }


def read_back(
    plan: PublishPlan,
    environment: Mapping[str, str],
    *,
    repository_root: Path | None = None,
) -> dict[str, str]:
    """Independently copy exact registry bytes into fresh marker-checked layouts."""

    contract_root = ROOT if repository_root is None else repository_root
    root = publication_state_root(environment)
    authfile = _runtime._secure_existing_authfile(root)  # noqa: SLF001
    try:
        published = _mapping(
            json.loads((root / "publication.json").read_text(encoding="utf-8")),
            "publication_state_missing",
        )
    except (OSError, json.JSONDecodeError) as error:
        raise OciPublishError("publication_state_missing") from error
    rows = _mapping(published.get("targets"), "publication_state_missing")
    build_identity = _build_identity(published.get("build"), plan)
    _require(
        set(rows) == {target.target_id for target in plan.targets},
        "publication_state_missing",
    )
    preserved_bases: dict[str, tuple[str, ...]] = {}
    for target in plan.targets:
        row = _mapping(rows.get(target.target_id), "publication_state_missing")
        preserved_bases[target.target_id] = _base_references(
            row.get("resolved_base_references")
        )
    readback_root = root / "readback"
    _require(
        not readback_root.exists() and not readback_root.is_symlink(),
        "residue_detected",
    )
    readback_root.mkdir(mode=0o700)
    verified: dict[str, Any] = {}
    for target in plan.targets:
        row = _mapping(rows.get(target.target_id), "publication_state_missing")
        destination = readback_root / target.target_id
        _runtime._copy(  # noqa: SLF001
            f"docker://{target.version_reference}",
            f"oci:{destination}:readback",
            authfile,
        )
        remote = inspect_layout(destination, target, "readback")
        remote_assertions = _assertions.assert_filesystem_contract(
            contract_root, plan, target, destination
        )
        local = _mapping(row.get("local"), "publication_state_missing")
        local_assertions = _mapping(
            row.get("assertions"), "publication_state_missing"
        )
        _require(remote == local, "registry_readback_mismatch")
        _require(
            remote_assertions == local_assertions,
            "registry_readback_mismatch",
        )
        source_digest = _inspect_remote_digest(target.source_reference, authfile)
        _require(
            source_digest == remote["manifest_digest"],
            "registry_readback_mismatch",
        )
        verified[target.target_id] = {
            "repository": target.registry_repository,
            "version_reference": target.version_reference,
            "source_reference": target.source_reference,
            "manifest_digest": remote["manifest_digest"],
            "platforms": remote["platforms"],
            "resolved_base_references": list(
                preserved_bases[target.target_id]
            ),
            "assertions": remote_assertions,
            "replayed": bool(row.get("replayed")),
        }
    _runtime._write_state(  # noqa: SLF001
        root / "readback.json",
        {"build": build_identity, "targets": verified},
    )
    return {
        "result": "read-back",
        "manifest_digests_json": json.dumps(
            {key: row["manifest_digest"] for key, row in sorted(verified.items())},
            separators=(",", ":"),
        ),
        "platform_digests_json": json.dumps(
            {key: row["platforms"] for key, row in sorted(verified.items())},
            sort_keys=True,
            separators=(",", ":"),
        ),
        "assertion_evidence_json": json.dumps(
            {key: row["assertions"] for key, row in sorted(verified.items())},
            sort_keys=True,
            separators=(",", ":"),
        ),
        "failure_code": "",
    }
