"""Public oci.publish contract projection over the issue-#17 registry runtime."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Mapping

from . import oci_publish as _runtime

OciPublishError = _runtime.OciPublishError
PublishTarget = _runtime.PublishTarget
PublishPlan = _runtime.PublishPlan
cleanup = _runtime.cleanup
inspect_layout = _runtime.inspect_layout
publication_state_root = _runtime.publication_state_root
read_back = _runtime.read_back
replay_decision = _runtime.replay_decision
residue = _runtime.residue

_PRODUCT = re.compile(r"^[a-z0-9]+(?:[._-][a-z0-9]+)*$")
_RUNNER_PRODUCT = "ciw-runner-images"
_RUNNER_REGISTRY_HOST = "git.faruqi.dev"
_RUNNER_REGISTRY_NAMESPACE = "mimranfaruqi"


@dataclass(frozen=True)
class PublishRequest:
    repository: str
    admitted_sha: str
    release_authority_sha: str
    product_id: str
    release_version: str
    source_trust: str
    platform_set: str | None = None


def request_from_environment(environment: Mapping[str, str]) -> PublishRequest:
    """Validate the registered public request plus internal release authority."""

    base = _runtime.request_from_environment(environment)
    platform_set = environment.get("INPUT_PLATFORM_SET", "")
    if platform_set and _PRODUCT.fullmatch(platform_set) is None:
        raise OciPublishError("invalid_platform_set")
    return PublishRequest(
        repository=base.repository,
        admitted_sha=base.admitted_sha,
        release_authority_sha=base.release_authority_sha,
        product_id=base.product_id,
        release_version=base.release_version,
        source_trust=base.source_trust,
        platform_set=platform_set or None,
    )


def _confirm_platform_set(repository_root: Path, request: PublishRequest) -> None:
    if request.platform_set is None:
        return
    contract = _runtime.load_product_contract(repository_root)
    platform_sets = contract.get("platform_sets")
    products = contract.get("products")
    if not isinstance(platform_sets, Mapping) or not isinstance(products, Mapping):
        raise OciPublishError("invalid_contract")
    expected = platform_sets.get(request.platform_set)
    if not isinstance(expected, list) or not expected or not all(
        isinstance(item, str) and item for item in expected
    ):
        raise OciPublishError("invalid_platform_set")
    product = products.get(request.product_id)
    if not isinstance(product, Mapping):
        raise OciPublishError("unsupported_product")
    targets = product.get("targets")
    if not isinstance(targets, list) or not targets:
        raise OciPublishError("invalid_contract")
    expected_tuple = tuple(expected)
    for raw in targets:
        if not isinstance(raw, Mapping):
            raise OciPublishError("invalid_contract")
        platform_set = raw.get("platform_set")
        actual = platform_sets.get(platform_set) if isinstance(platform_set, str) else None
        if not isinstance(actual, list) or tuple(actual) != expected_tuple:
            raise OciPublishError("platform_override_forbidden")


def _runner_registry_repository(target_id: str) -> str:
    if not target_id.startswith("runner-") or _PRODUCT.fullmatch(target_id) is None:
        raise OciPublishError("invalid_contract")
    return (
        f"{_RUNNER_REGISTRY_HOST}/{_RUNNER_REGISTRY_NAMESPACE}/"
        f"github-actions-{target_id}"
    )


def resolve_plan(repository_root: Path, request: PublishRequest) -> PublishPlan:
    """Resolve the runtime plan and project runner products to private Forgejo."""

    _confirm_platform_set(repository_root, request)
    plan = _runtime.resolve_plan(
        repository_root,
        _runtime.PublishRequest(
            repository=request.repository,
            admitted_sha=request.admitted_sha,
            release_authority_sha=request.release_authority_sha,
            product_id=request.product_id,
            release_version=request.release_version,
            source_trust=request.source_trust,
        ),
    )
    if plan.product_id != _RUNNER_PRODUCT:
        return plan
    targets: list[PublishTarget] = []
    for target in plan.targets:
        repository = _runner_registry_repository(target.target_id)
        targets.append(
            replace(
                target,
                registry_repository=repository,
                version_reference=f"{repository}:{plan.release_version}",
                source_reference=f"{repository}:sha-{plan.admitted_sha}",
            )
        )
    return replace(plan, targets=tuple(targets))


def authenticate(
    plan: PublishPlan,
    environment: Mapping[str, str],
    username: str,
    token: str,
) -> dict[str, str]:
    """Authenticate central runner-image publication to owner-managed Forgejo."""

    if plan.product_id != _RUNNER_PRODUCT:
        return _runtime.authenticate(plan, environment, username, token)
    _runtime._require(
        username == username.strip()
        and bool(username)
        and "\n" not in username
        and "\r" not in username,
        "registry_auth_invalid",
    )
    _runtime._require(
        bool(token) and "\n" not in token and "\r" not in token,
        "registry_auth_invalid",
    )
    _runtime._require(
        _runtime.shutil.which("skopeo") is not None,
        "registry_tool_unavailable",
    )
    root = publication_state_root(environment)
    _runtime._require(
        not root.exists() and not root.is_symlink(),
        "residue_detected",
    )
    root.mkdir(parents=True, mode=0o700)
    authfile = _runtime._authfile(root)
    try:
        _runtime._run(
            [
                "skopeo",
                "login",
                "--authfile",
                str(authfile),
                "--username",
                username,
                "--password-stdin",
                _RUNNER_REGISTRY_HOST,
            ],
            input_bytes=token.encode("utf-8"),
        )
    except (OSError, _runtime.subprocess.CalledProcessError) as error:
        raise OciPublishError("registry_auth_failed") from error
    try:
        authfile.chmod(0o600)
    except OSError as error:
        raise OciPublishError("registry_auth_invalid") from error
    _runtime._secure_existing_authfile(root)
    state = {
        "api": "oci.publish",
        "version": "1.0.0",
        "source_sha": plan.admitted_sha,
        "product_id": plan.product_id,
        "release_version": plan.release_version,
        "registry_host": _RUNNER_REGISTRY_HOST,
    }
    _runtime._write_state(root / "plan.json", state)
    return {"result": "authenticated", "failure_code": ""}


def runner_rebuild_decision(
    local_digest: str,
    version_digest: str | None,
    source_digest: str | None,
) -> tuple[bool, bool, bool]:
    """Return safe runner-tag writes for an exact Git-tag rebuild.

    The human-readable version tag is a replaceable projection of the authoritative
    ci-workflows Git tag. The source-SHA tag is immutable provenance: publish it
    once, accept an equal replay, and reject any conflicting digest.
    """

    _runtime._require(
        _runtime._DIGEST.fullmatch(local_digest) is not None,
        "oci_digest_mismatch",
    )
    for remote in (version_digest, source_digest):
        _runtime._require(
            remote is None or _runtime._DIGEST.fullmatch(remote) is not None,
            "registry_inspection_failed",
        )
    if source_digest is not None and source_digest != local_digest:
        raise OciPublishError("immutable_reference_conflict")
    return (
        version_digest != local_digest,
        source_digest is None,
        version_digest is not None or source_digest is not None,
    )


def publish(plan: PublishPlan, environment: Mapping[str, str]) -> dict[str, str]:
    """Publish runners under repository-tag rebuild semantics; delegate all others."""

    if plan.product_id != _RUNNER_PRODUCT:
        return _runtime.publish(plan, environment)
    root = publication_state_root(environment)
    authfile = _runtime._secure_existing_authfile(root)
    build_root = _runtime.build_state_root(environment)
    results: dict[str, Any] = {}
    any_replayed = False
    for target in plan.targets:
        layout = build_root / "layouts" / target.target_id
        local = _runtime.inspect_layout(layout, target, "validation")
        local_digest = str(local["manifest_digest"])
        version_digest = _runtime._inspect_remote_digest(
            target.version_reference, authfile
        )
        source_digest = _runtime._inspect_remote_digest(
            target.source_reference, authfile
        )
        publish_version, publish_source, replayed = runner_rebuild_decision(
            local_digest, version_digest, source_digest
        )
        any_replayed = any_replayed or replayed
        if publish_version:
            _runtime._copy(
                f"oci:{layout}:validation",
                f"docker://{target.version_reference}",
                authfile,
            )
        if publish_source:
            _runtime._copy(
                f"oci:{layout}:validation",
                f"docker://{target.source_reference}",
                authfile,
            )
        verified_version = _runtime._inspect_remote_digest(
            target.version_reference, authfile
        )
        verified_source = _runtime._inspect_remote_digest(
            target.source_reference, authfile
        )
        _runtime._require(
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
    _runtime._write_state(root / "publication.json", {"targets": results})
    return {
        "result": "published",
        "manifest_digests_json": json.dumps(
            {
                key: row["manifest_digest"]
                for key, row in sorted(results.items())
            },
            separators=(",", ":"),
        ),
        "replayed": str(any_replayed).lower(),
        "failure_code": "",
    }


def verify(plan: PublishPlan, environment: Mapping[str, str]) -> dict[str, str]:
    """Project detailed verified evidence onto the registered public outputs."""

    values = _runtime.verify(plan, environment)
    try:
        repositories = json.loads(values["repositories_json"])
        versions = json.loads(values["version_references_json"])
        sources = json.loads(values["source_references_json"])
        manifests = json.loads(values["manifest_digests_json"])
    except (KeyError, TypeError, json.JSONDecodeError) as error:
        raise OciPublishError("publication_state_missing") from error
    target_ids = {target.target_id for target in plan.targets}
    if not all(
        isinstance(item, Mapping) and set(item) == target_ids
        for item in (repositories, versions, sources, manifests)
    ):
        raise OciPublishError("publication_state_missing")
    immutable: dict[str, Any] = {
        "targets": {
            target.target_id: {
                "repository": repositories[target.target_id],
                "version": versions[target.target_id],
                "source_sha": sources[target.target_id],
                "manifest_digest": manifests[target.target_id],
            }
            for target in plan.targets
        },
        "release": {
            "source_sha": plan.admitted_sha,
            "version": plan.release_version,
        },
    }
    if plan.flux_asset:
        immutable["flux"] = {
            "canary_id": plan.canary_id,
            "previous_known_good": plan.previous_known_good,
            "rollback_id": plan.rollback_id,
        }
    values["image_digest"] = values["manifest_digests_json"]
    values["immutable_references_json"] = json.dumps(
        immutable, sort_keys=True, separators=(",", ":")
    )
    return values
