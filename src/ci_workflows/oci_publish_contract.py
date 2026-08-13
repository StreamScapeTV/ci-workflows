"""Public oci.publish contract projection over guarded registry publication."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from . import oci_publish as _runtime
from . import oci_publish_guards as _guards

OciPublishError = _runtime.OciPublishError
PublishTarget = _runtime.PublishTarget
PublishPlan = _runtime.PublishPlan
authenticate = _guards.authenticate
cleanup = _guards.cleanup
inspect_layout = _guards.inspect_layout
publication_state_root = _guards.publication_state_root
publish = _guards.publish
read_back = _guards.read_back
replay_decision = _guards.replay_decision
residue = _guards.residue

_PRODUCT = re.compile(r"^[a-z0-9]+(?:[._-][a-z0-9]+)*$")
_SUPPORTED_PRODUCTS = frozenset(
    {"iptv-backend-image", "agent-state-image", "flux-runner-images"}
)


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
    if base.product_id not in _SUPPORTED_PRODUCTS:
        raise OciPublishError("unsupported_product")
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


def resolve_plan(repository_root: Path, request: PublishRequest) -> PublishPlan:
    """Resolve the runtime plan after optional checked-in platform confirmation."""

    _confirm_platform_set(repository_root, request)
    return _runtime.resolve_plan(
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


def verify(plan: PublishPlan, environment: Mapping[str, str]) -> dict[str, str]:
    """Project detailed verified evidence onto the registered public outputs."""

    values = _runtime.verify(plan, environment)
    try:
        repositories = json.loads(values["repositories_json"])
        versions = json.loads(values["version_references_json"])
        sources = json.loads(values["source_references_json"])
        manifests = json.loads(values["manifest_digests_json"])
        resolved_inputs = json.loads(values.pop("resolved_inputs_json"))
        assertion_evidence = json.loads(values.pop("assertion_evidence_json"))
    except (KeyError, TypeError, json.JSONDecodeError) as error:
        raise OciPublishError("publication_state_missing") from error
    target_ids = {target.target_id for target in plan.targets}
    if not all(
        isinstance(item, Mapping) and set(item) == target_ids
        for item in (
            repositories,
            versions,
            sources,
            manifests,
            resolved_inputs,
            assertion_evidence,
        )
    ):
        raise OciPublishError("publication_state_missing")
    resolved_inputs = {
        target.target_id: _runtime._validate_resolved_input_evidence(  # noqa: SLF001
            resolved_inputs[target.target_id],
            target,
            "publication_state_missing",
        )
        for target in plan.targets
    }
    immutable: dict[str, Any] = {
        "targets": {
            target.target_id: {
                "repository": repositories[target.target_id],
                "version": versions[target.target_id],
                "source_reference": sources[target.target_id],
                "manifest_digest": manifests[target.target_id],
                "resolved_inputs": resolved_inputs[target.target_id],
                "assertions": assertion_evidence[target.target_id],
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
    values["immutable_references_json"] = json.dumps(
        immutable, sort_keys=True, separators=(",", ":")
    )
    return values
