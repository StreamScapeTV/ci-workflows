"""Strict trust, dependency, and release guards for Flux infrastructure assets.

The original issue-33 composer was intentionally parallel to issues #16-#19.
This module closes the trust and interface gaps discovered during recovery while
keeping the public workflow surface unchanged. It preserves nested OCI platform
read-back evidence instead of flattening or discarding it.
"""
from __future__ import annotations

import json
import re
from collections.abc import Sequence
from typing import Any, Mapping

from .flux_assets import (
    FluxAssetError,
    ReleasePlan,
    build_handoff,
    build_release_plan,
    canonical_json,
    canonical_sha256,
    validate_runtime_probe,
)

_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_SHA = re.compile(r"^[0-9a-f]{40}$")
_PACKAGE_SHA = re.compile(r"^[0-9a-f]{64}$")
_SAFE_BRANCH = re.compile(r"^[A-Za-z0-9._/-]{1,255}$")
_GHCR_REPOSITORY = re.compile(r"^ghcr[.]io/streamscapetv/[a-z0-9._/-]+$")
_SUCCESS_RESULTS = {"success", "verified", "published", "read-back", "replayed"}


def _fail(code: str, message: str) -> None:
    raise FluxAssetError(code, message)


def _require(condition: bool, code: str, message: str) -> None:
    if not condition:
        _fail(code, message)


def _json_object(value: Any, *, name: str) -> dict[str, Any]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as error:
            raise FluxAssetError(
                "invalid_dependency_output", f"{name} is not valid JSON"
            ) from error
    _require(
        isinstance(value, Mapping),
        "invalid_dependency_output",
        f"{name} must be an object",
    )
    return dict(value)


def _contains_latest(value: Any) -> bool:
    if isinstance(value, str):
        return bool(re.search(r"(^|[:/@])latest($|[:/@])", value.casefold()))
    if isinstance(value, Mapping):
        return any(_contains_latest(item) for item in value.values())
    if isinstance(value, Sequence) and not isinstance(
        value, (str, bytes, bytearray)
    ):
        return any(_contains_latest(item) for item in value)
    return False


def validate_operation_context(
    *,
    operation: str,
    event_name: str,
    ref_type: str,
    ref_name: str,
    default_branch: str,
    release_version: str,
) -> None:
    """Bind privileged operations to the exact caller event/ref class.

    `release` is tag-push only. `verify-only` is manual default-branch only and
    therefore cannot be repurposed into a publication path. `plan` is
    credential-free and intentionally has no privileged event requirement.
    """

    if operation == "plan":
        return
    if operation == "release":
        _require(
            event_name == "push",
            "release_event_forbidden",
            "release requires a tag push caller event",
        )
        _require(
            ref_type == "tag" and ref_name in {release_version, f"v{release_version}"},
            "release_ref_mismatch",
            "release requires the exact immutable version tag",
        )
        return
    if operation == "verify-only":
        _require(
            event_name == "workflow_dispatch",
            "verify_event_forbidden",
            "verify-only requires workflow_dispatch",
        )
        _require(
            bool(default_branch) and _SAFE_BRANCH.fullmatch(default_branch) is not None,
            "verify_ref_mismatch",
            "verify-only requires a bounded default branch identity",
        )
        _require(
            ref_type == "branch" and ref_name == default_branch,
            "verify_ref_mismatch",
            "verify-only must run from the caller default branch",
        )
        return
    _fail("unsupported_operation", f"unsupported operation {operation!r}")


def _validate_digest_map(value: Any, *, name: str) -> dict[str, str]:
    payload = _json_object(value, name=name)
    _require(bool(payload), "invalid_digest", f"{name} must not be empty")
    result: dict[str, str] = {}
    for key, digest in payload.items():
        _require(
            isinstance(key, str)
            and bool(key)
            and isinstance(digest, str)
            and _DIGEST.fullmatch(digest) is not None,
            "invalid_digest",
            f"{name} contains an invalid digest",
        )
        result[key] = digest
    return result


def _validate_platform_evidence(value: Any, *, name: str) -> dict[str, Any]:
    payload = _json_object(value, name=name)
    _require(bool(payload), "invalid_dependency_output", f"{name} is empty")
    _require(
        not _contains_latest(payload),
        "mutable_reference",
        f"{name} contains a mutable latest reference",
    )
    return payload


def _normalize_dependency_outputs(
    plan: ReleasePlan, evidence: Mapping[str, Any]
) -> dict[str, Any]:
    expected = {dependency.api_name for dependency in plan.dependencies}
    _require(
        set(str(key) for key in evidence) == expected,
        "dependency_identity_mismatch",
        "dependency evidence set must exactly match the checked-in operation plan",
    )
    normalized: dict[str, Any] = {}
    for dependency in plan.dependencies:
        raw = evidence.get(dependency.api_name)
        _require(
            isinstance(raw, Mapping),
            "missing_dependency_evidence",
            f"{dependency.api_name} evidence is required",
        )
        assert isinstance(raw, Mapping)
        missing = set(dependency.required_outputs) - set(str(key) for key in raw)
        _require(
            not missing,
            "missing_dependency_output",
            f"{dependency.api_name} is missing {sorted(missing)}",
        )
        _require(
            str(raw.get("result", "")) in _SUCCESS_RESULTS,
            "dependency_failed",
            f"{dependency.api_name} did not return a successful result",
        )
        entry: dict[str, Any] = {}
        for key in dependency.required_outputs:
            value = raw[key]
            if key == "image_digest":
                value = _validate_digest_map(
                    value, name=f"{dependency.api_name}.{key}"
                )
            elif key == "chart_digest":
                _require(
                    isinstance(value, str) and _DIGEST.fullmatch(value) is not None,
                    "invalid_digest",
                    f"{dependency.api_name}.{key} is invalid",
                )
            elif key == "platform_digests_json":
                value = _validate_platform_evidence(
                    value, name=f"{dependency.api_name}.{key}"
                )
            elif key == "immutable_references_json":
                value = _json_object(
                    value, name=f"{dependency.api_name}.{key}"
                )
            if _contains_latest(value):
                _fail(
                    "mutable_reference",
                    f"{dependency.api_name}.{key} contains latest",
                )
            entry[key] = value
        normalized[dependency.api_name] = entry
    return normalized


def _validate_oci_publication(plan: ReleasePlan, row: Mapping[str, Any]) -> None:
    digests = row.get("image_digest")
    platforms = row.get("platform_digests_json")
    immutable = row.get("immutable_references_json")
    _require(
        isinstance(digests, Mapping)
        and isinstance(platforms, Mapping)
        and isinstance(immutable, Mapping),
        "invalid_dependency_output",
        "OCI publication outputs are malformed",
    )
    assert isinstance(digests, Mapping)
    assert isinstance(platforms, Mapping)
    assert isinstance(immutable, Mapping)
    _require(
        set(immutable) == {"targets", "release", "flux"},
        "dependency_identity_mismatch",
        "Flux OCI publication must include exact targets, release, and Flux handoff",
    )
    release = _json_object(immutable.get("release"), name="oci.publish.release")
    targets = _json_object(immutable.get("targets"), name="oci.publish.targets")
    flux = _json_object(immutable.get("flux"), name="oci.publish.flux")
    _require(
        set(release) == {"source_sha", "version"}
        and release.get("source_sha") == plan.admitted_sha
        and release.get("version") == plan.release_version
        and _SHA.fullmatch(str(release.get("source_sha", ""))) is not None,
        "dependency_identity_mismatch",
        "OCI publication release identity does not match the Flux asset request",
    )
    _require(
        set(targets) == set(digests) == set(platforms) and len(targets) == 2,
        "dependency_identity_mismatch",
        "Flux runner publication must contain exactly the two inventory targets",
    )
    for target_id, raw in targets.items():
        target = _json_object(raw, name=f"oci.publish.targets.{target_id}")
        _require(
            set(target)
            == {"repository", "version", "source_sha", "manifest_digest"},
            "dependency_identity_mismatch",
            f"OCI target {target_id!r} has an unexpected identity shape",
        )
        repository = target.get("repository")
        digest = target.get("manifest_digest")
        _require(
            isinstance(repository, str)
            and _GHCR_REPOSITORY.fullmatch(repository) is not None
            and isinstance(digest, str)
            and _DIGEST.fullmatch(digest) is not None
            and digests[target_id] == digest
            and target.get("version") == f"{repository}:{plan.release_version}"
            and target.get("source_sha") == f"{repository}:sha-{plan.admitted_sha}",
            "dependency_identity_mismatch",
            f"OCI target {target_id!r} does not match the exact request",
        )
        target_platforms = platforms.get(target_id)
        _require(
            isinstance(target_platforms, Mapping) and bool(target_platforms),
            "dependency_identity_mismatch",
            f"OCI target {target_id!r} lacks platform read-back evidence",
        )
    _require(
        set(flux) == {"canary_id", "previous_known_good", "rollback_id"}
        and flux.get("canary_id") == plan.canary_id
        and flux.get("previous_known_good") == plan.previous_known_good_policy
        and flux.get("rollback_id") == plan.rollback_id,
        "dependency_identity_mismatch",
        "OCI Flux canary/rollback identities do not match the checked-in policy",
    )


def _validate_oci_build(plan: ReleasePlan, row: Mapping[str, Any]) -> None:
    if plan.product_id != "flux-runner-images":
        return
    digests = row.get("image_digest")
    platforms = row.get("platform_digests_json")
    _require(
        isinstance(digests, Mapping)
        and isinstance(platforms, Mapping)
        and set(digests) == set(platforms)
        and len(digests) == 2,
        "dependency_identity_mismatch",
        "OCI build evidence must contain exactly the two runner targets",
    )


def _validate_helm_publication(plan: ReleasePlan, row: Mapping[str, Any]) -> None:
    digest = row.get("chart_digest")
    immutable = row.get("immutable_references_json")
    _require(
        isinstance(digest, str)
        and _DIGEST.fullmatch(digest) is not None
        and isinstance(immutable, Mapping),
        "invalid_dependency_output",
        "Helm publication outputs are malformed",
    )
    assert isinstance(immutable, Mapping)
    _require(
        set(immutable) == {"chart", "chart_digest", "package_sha256"},
        "dependency_identity_mismatch",
        "Helm publication immutable reference shape is invalid",
    )
    chart = immutable.get("chart")
    _require(
        isinstance(chart, str)
        and chart.startswith("oci://")
        and chart.endswith(f":{plan.release_version}")
        and ":latest" not in chart.casefold()
        and immutable.get("chart_digest") == digest
        and isinstance(immutable.get("package_sha256"), str)
        and _PACKAGE_SHA.fullmatch(str(immutable.get("package_sha256"))) is not None,
        "dependency_identity_mismatch",
        "Helm publication identity does not match the exact release version/digest",
    )


def validate_dependency_evidence(
    plan: ReleasePlan, evidence: Mapping[str, Any]
) -> dict[str, Any]:
    """Validate dependency shape plus exact source/version/handoff identity."""

    normalized = _normalize_dependency_outputs(plan, evidence)
    if "oci.build" in normalized:
        _validate_oci_build(plan, normalized["oci.build"])
    if "oci.publish" in normalized:
        _validate_oci_publication(plan, normalized["oci.publish"])
    if "helm.publish" in normalized:
        _validate_helm_publication(plan, normalized["helm.publish"])
    return normalized


def _asset_references(normalized: Mapping[str, Any]) -> dict[str, Any]:
    references: dict[str, Any] = {}
    for api_name, output in normalized.items():
        if isinstance(output, Mapping) and "immutable_references_json" in output:
            references[api_name] = output["immutable_references_json"]
    return references


def compose_guarded_release(
    contract: Mapping[str, Any],
    *,
    request: Mapping[str, str],
    dependency_outputs: Mapping[str, Any],
) -> dict[str, str]:
    """Compose a plan/release after strict dependency identity validation."""

    plan = build_release_plan(
        contract,
        admitted_sha=request["admitted_sha"],
        product_id=request["product_id"],
        release_version=request["release_version"],
        operation=request["operation"],
        policy_path=request["policy_path"],
        request_id=request["request_id"],
        source_ref_type=request.get("source_ref_type", ""),
        source_ref_name=request.get("source_ref_name", ""),
    )
    if plan.operation == "plan":
        references = {
            "version_identity": plan.version_identity,
            "source_identity": plan.source_identity,
            "handoff": {
                "canary_id": plan.canary_id,
                "previous_known_good_policy": plan.previous_known_good_policy,
                "rollback_id": plan.rollback_id,
                "review_required": True,
                "mutation_authorized": False,
            },
        }
        manifest = {
            "schema_version": 1,
            "state": "planned",
            "plan": plan.as_dict(),
            "asset_references": references,
        }
        return {
            "result": "planned",
            "immutable_references_json": canonical_json(references),
            "release_manifest_sha256": canonical_sha256(manifest),
            "request_id": plan.request_id,
        }

    normalized = validate_dependency_evidence(plan, dependency_outputs)
    references = _asset_references(normalized)
    manifest = {
        "schema_version": 1,
        "state": "verified",
        "request": {
            "request_id": plan.request_id,
            "product_id": plan.product_id,
            "source_sha": plan.admitted_sha,
            "release_version": plan.release_version,
            "operation": plan.operation,
        },
        "dependencies": normalized,
        "asset_references": references,
        "bootstrap": dict(plan.bootstrap_policy),
        "selection_policy": {
            "canary_id": plan.canary_id,
            "previous_known_good_policy": plan.previous_known_good_policy,
            "rollback_id": plan.rollback_id,
            "review_required": True,
        },
    }
    digest = canonical_sha256(manifest)
    handoff = build_handoff(
        plan, manifest_sha256=digest, asset_references=references
    )
    immutable = {
        "version_identity": plan.version_identity,
        "source_identity": plan.source_identity,
        "assets": references,
        "flux_handoff": handoff,
    }
    return {
        "result": "verified",
        "immutable_references_json": canonical_json(immutable),
        "release_manifest_sha256": digest,
        "request_id": plan.request_id,
    }


def validate_runtime_probe_strict(
    expected: Mapping[str, Any], probe: Mapping[str, Any]
) -> dict[str, Any]:
    """Extend the base runtime probe with required label/storage identity checks."""

    validate_runtime_probe(expected, probe)
    required_labels = expected.get("required_labels", [])
    _require(
        isinstance(required_labels, list)
        and all(isinstance(item, str) and item for item in required_labels),
        "invalid_runtime_probe",
        "required_labels contract is invalid",
    )
    labels = probe.get("labels", {})
    _require(
        isinstance(labels, Mapping),
        "runtime_capability_mismatch",
        "runtime probe labels must be an object",
    )
    missing = [
        label
        for label in required_labels
        if not isinstance(labels.get(label), str) or not str(labels.get(label)).strip()
    ]
    _require(
        not missing,
        "runtime_capability_mismatch",
        f"runtime probe is missing required OCI labels: {missing}",
    )
    subordinate_id = expected.get("subordinate_id")
    if subordinate_id is not None:
        _require(
            probe.get("subordinate_id") == subordinate_id,
            "runtime_capability_mismatch",
            "runtime subordinate-ID configuration differs",
        )
    storage_driver = expected.get("storage_driver")
    if storage_driver is not None:
        _require(
            probe.get("storage_driver") == storage_driver,
            "runtime_capability_mismatch",
            "runtime storage driver differs",
        )
    result: dict[str, Any] = {
        "os": probe.get("os"),
        "architecture": probe.get("architecture"),
        "tools": dict(probe.get("tools", {})),
        "labels": {label: str(labels[label]) for label in required_labels},
    }
    if subordinate_id is not None:
        result["subordinate_id"] = subordinate_id
    if storage_driver is not None:
        result["storage_driver"] = storage_driver
    return result
