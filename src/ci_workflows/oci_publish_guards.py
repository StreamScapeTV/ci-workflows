"""Fail-closed OCI publication guards over the issue-17 registry runtime."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Mapping

from . import oci_publish as _runtime
from . import oci_publish_assertions as _assertions

OciPublishError = _runtime.OciPublishError
PublishPlan = _runtime.PublishPlan
PublishTarget = _runtime.PublishTarget
cleanup = _runtime.cleanup
publication_state_root = _runtime.publication_state_root
replay_decision = _runtime.replay_decision
residue = _runtime.residue

ROOT = Path(__file__).resolve().parents[2]
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_ABSENCE_MARKERS = (b"manifest unknown", b"manifest_unknown", b"name unknown")


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise OciPublishError(code)


def _mapping(value: Any, code: str) -> Mapping[str, Any]:
    _require(isinstance(value, Mapping), code)
    return value


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
    results: dict[str, Any] = {}
    any_replayed = False
    any_published = False
    for target in plan.targets:
        layout = build_root / "layouts" / target.target_id
        local = inspect_layout(layout, target, "validation")
        _assertions.assert_filesystem_contract(contract_root, plan, target, layout)
        local_digest = str(local["manifest_digest"])
        _require(_DIGEST.fullmatch(local_digest) is not None, "oci_digest_mismatch")
        version_digest = _inspect_remote_digest(target.version_reference, authfile)
        source_digest = _inspect_remote_digest(target.source_reference, authfile)
        publish_version, publish_source, replayed = replay_decision(
            local_digest, version_digest, source_digest
        )
        if not allow_publish and (publish_version or publish_source):
            raise OciPublishError("remote_reference_missing")
        any_replayed = any_replayed or replayed
        if publish_version:
            _runtime._copy(  # noqa: SLF001
                f"oci:{layout}:validation",
                f"docker://{target.version_reference}",
                authfile,
            )
            any_published = True
        if publish_source:
            _runtime._copy(  # noqa: SLF001
                f"oci:{layout}:validation",
                f"docker://{target.source_reference}",
                authfile,
            )
            any_published = True
        verified_version = _inspect_remote_digest(target.version_reference, authfile)
        verified_source = _inspect_remote_digest(target.source_reference, authfile)
        _require(
            verified_version == local_digest and verified_source == local_digest,
            "registry_digest_mismatch",
        )
        results[target.target_id] = {
            "repository": target.registry_repository,
            "version_reference": target.version_reference,
            "source_reference": target.source_reference,
            "manifest_digest": local_digest,
            "local": local,
            "replayed": replayed,
        }
    _runtime._write_state(root / "publication.json", {"targets": results})  # noqa: SLF001
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
        _assertions.assert_filesystem_contract(
            contract_root, plan, target, destination
        )
        local = _mapping(row.get("local"), "publication_state_missing")
        _require(remote == local, "registry_readback_mismatch")
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
            "replayed": bool(row.get("replayed")),
        }
    _runtime._write_state(root / "readback.json", {"targets": verified})  # noqa: SLF001
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
        "failure_code": "",
    }
