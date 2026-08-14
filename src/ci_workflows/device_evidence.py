"""Deterministic redacted device evidence construction and validation."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Mapping, Sequence

from .device_contract_common import require
from .device_types import (
    DevicePlan,
    LockReceipt,
    LockReleaseReceipt,
    SelectedDevice,
    canonical_json,
)
from .physical_log_policy import validate_stable_evidence

EVIDENCE_NAME = re.compile(r"^[a-z][a-z0-9._-]{1,95}$")
MEDIA_TYPE = re.compile(r"^(?:application/json|text/plain)$")


def _timestamp(value: int) -> str:
    require(isinstance(value, int) and value >= 0, "evidence_policy_failed")
    return f"epoch:{value}"


def _artifact_inventory(
    retained: Sequence[Mapping[str, object]],
) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for item in retained:
        require(
            set(item) == {"name", "media_type", "bytes", "sha256"},
            "evidence_policy_failed",
        )
        name, media_type, byte_count, digest = (
            item["name"],
            item["media_type"],
            item["bytes"],
            item["sha256"],
        )
        require(
            isinstance(name, str)
            and EVIDENCE_NAME.fullmatch(name) is not None
            and isinstance(media_type, str)
            and MEDIA_TYPE.fullmatch(media_type) is not None
            and isinstance(byte_count, int)
            and 0 <= byte_count <= 8 * 1024 * 1024
            and isinstance(digest, str)
            and re.fullmatch(r"[0-9a-f]{64}", digest) is not None,
            "evidence_policy_failed",
        )
        result.append(dict(item))
    return sorted(result, key=lambda item: str(item["name"]))


def _scope_map(
    evidence_contract: Mapping[str, object],
    *,
    synthetic: bool,
) -> Mapping[str, object]:
    key = (
        "synthetic_certification_scope_by_family"
        if synthetic
        else "certification_scope_by_family"
    )
    scopes = evidence_contract.get(key)
    require(isinstance(scopes, Mapping), "evidence_policy_failed")
    return scopes


def _limitations(
    evidence_contract: Mapping[str, object],
    *,
    synthetic: bool,
) -> list[str]:
    required = evidence_contract.get("required_limitations")
    require(
        isinstance(required, list)
        and all(isinstance(item, str) and item for item in required),
        "evidence_policy_failed",
    )
    result = list(required)
    if synthetic:
        synthetic_required = evidence_contract.get("synthetic_required_limitations")
        require(
            isinstance(synthetic_required, list)
            and synthetic_required
            and all(isinstance(item, str) and item for item in synthetic_required),
            "evidence_policy_failed",
        )
        result.extend(item for item in synthetic_required if item not in result)
    return result


def build_evidence_packet(
    *,
    plan: DevicePlan,
    selected: SelectedDevice,
    lock_receipt: LockReceipt,
    release_receipt: LockReleaseReceipt | None,
    evidence_contract: Mapping[str, object],
    started_at: int,
    ended_at: int,
    result: str,
    failure_code: str,
    assertions: Sequence[str],
    restoration: str,
    cleanup: str,
    retained_evidence: Sequence[Mapping[str, object]] = (),
    certification_scope: str | None = None,
    synthetic: bool = False,
) -> dict[str, object]:
    require(result in {"success", "failure"}, "evidence_policy_failed")
    require(ended_at >= started_at, "evidence_policy_failed")
    scopes = _scope_map(evidence_contract, synthetic=synthetic)
    expected_scope = str(scopes[selected.family.value])
    actual_scope = certification_scope or expected_scope
    require(actual_scope == expected_scope, "evidence_overclaim")
    packet = {
        "packet_version": evidence_contract["packet_version"],
        "request_id": plan.request.request_id,
        "issue_number": plan.request.issue_number,
        "repository": plan.request.repository,
        "source_sha": plan.request.admitted_sha,
        "device_family": selected.family.value,
        "device_profile": plan.profile.profile_id,
        "device_identity_hash": selected.identity_hash,
        "classification": {
            "family": selected.family.value,
            "os_or_api": selected.os_or_api,
            "model_class": selected.model_class,
            "capabilities": list(selected.capabilities),
            "connection_class": selected.connection_class,
        },
        "serialization": {
            "backend": plan.serialization_backend,
            "concurrency_group": plan.concurrency_group,
            "cancel_in_progress": False,
            "cross_run_fencing_claimed": False,
        },
        "lock": {
            "adapter": "in-memory-tests-only",
            "accepted": lock_receipt.accepted,
            "epoch": lock_receipt.epoch,
            "owner_hash": lock_receipt.owner_hash,
            "resource_key_hash": hashlib.sha256(
                lock_receipt.resource_key.encode()
            ).hexdigest(),
            "release_receipt": (
                release_receipt.release_receipt if release_receipt else ""
            ),
        },
        "command_profiles": {
            "prepare": plan.profile.command_profile.prepare_script,
            "test": plan.profile.command_profile.test_script,
            "evidence": plan.profile.command_profile.evidence_script,
            "cleanup": plan.profile.command_profile.cleanup_script,
        },
        "started_at": _timestamp(started_at),
        "ended_at": _timestamp(ended_at),
        "duration_seconds": ended_at - started_at,
        "result": result,
        "failure_code": failure_code,
        "assertions": sorted(set(assertions)),
        "restoration": restoration,
        "cleanup": cleanup,
        "artifact_exception_id": plan.request.evidence_exception_id or "",
        "retained_evidence": _artifact_inventory(retained_evidence),
        "certification_scope": actual_scope,
        "limitations": _limitations(evidence_contract, synthetic=synthetic),
    }
    validate_evidence_packet(
        packet,
        evidence_contract,
        raw_identifier=selected._raw_identifier,
    )
    return packet


def _walk_keys(value: object) -> Sequence[str]:
    keys: list[str] = []
    if isinstance(value, Mapping):
        for key, nested in value.items():
            keys.append(str(key))
            keys.extend(_walk_keys(nested))
    elif isinstance(value, list):
        for nested in value:
            keys.extend(_walk_keys(nested))
    return keys


def validate_evidence_packet(
    packet: Mapping[str, object],
    evidence_contract: Mapping[str, object],
    *,
    raw_identifier: str,
) -> None:
    required = set(evidence_contract["required_fields"])
    allowed = set(evidence_contract["allowed_fields"])
    require(set(packet) == required == allowed, "evidence_policy_failed")
    forbidden = {
        str(value).casefold() for value in evidence_contract["forbidden_fields"]
    }
    require(
        all(key.casefold() not in forbidden for key in _walk_keys(packet)),
        "evidence_policy_failed",
    )
    serialized = canonical_json(packet)
    require(
        len(serialized.encode()) <= int(evidence_contract["maximum_packet_bytes"]),
        "evidence_policy_failed",
    )
    require(raw_identifier not in serialized, "evidence_policy_failed")
    for pattern in evidence_contract["forbidden_value_patterns"]:
        require(
            re.search(str(pattern), serialized) is None,
            "evidence_policy_failed",
        )
    require(
        re.fullmatch(
            str(evidence_contract["identity_hash_regex"]),
            str(packet["device_identity_hash"]),
        )
        is not None,
        "evidence_policy_failed",
    )
    assertions = packet["assertions"]
    allowed_assertions = evidence_contract["allowed_assertions"]
    require(
        isinstance(assertions, list)
        and all(isinstance(assertion, str) for assertion in assertions)
        and assertions == sorted(set(assertions))
        and len(assertions) <= int(evidence_contract["maximum_assertions"])
        and isinstance(allowed_assertions, list)
        and set(assertions) <= set(allowed_assertions),
        "evidence_policy_failed",
    )
    require(
        isinstance(packet["retained_evidence"], list)
        and len(packet["retained_evidence"])
        <= int(evidence_contract["maximum_retained_files"]),
        "evidence_policy_failed",
    )

    family = str(packet["device_family"])
    physical_scopes = _scope_map(evidence_contract, synthetic=False)
    synthetic_scopes = _scope_map(evidence_contract, synthetic=True)
    physical_scope = str(physical_scopes[family])
    synthetic_scope = str(synthetic_scopes[family])
    scope = str(packet["certification_scope"])
    require(scope in {physical_scope, synthetic_scope}, "evidence_overclaim")

    limitations = packet["limitations"]
    required_limitations = evidence_contract["required_limitations"]
    require(
        isinstance(limitations, list)
        and all(isinstance(item, str) and item for item in limitations)
        and isinstance(required_limitations, list)
        and set(required_limitations) <= set(limitations),
        "evidence_policy_failed",
    )
    synthetic_required = evidence_contract["synthetic_required_limitations"]
    require(isinstance(synthetic_required, list), "evidence_policy_failed")
    if scope == synthetic_scope:
        require(set(synthetic_required) <= set(limitations), "evidence_overclaim")
    else:
        require(
            not (set(synthetic_required) & set(limitations)),
            "evidence_overclaim",
        )
        lock = packet["lock"]
        serialization = packet["serialization"]
        require(
            isinstance(lock, Mapping)
            and lock.get("adapter") != "in-memory-tests-only"
            and isinstance(serialization, Mapping)
            and serialization.get("cross_run_fencing_claimed") is True,
            "evidence_overclaim",
        )


def build_stable_physical_evidence(
    *,
    contract_root: Path,
    repository: str,
    source_sha: str,
    workflow_run_id: int,
    job_id: int,
    device_family: str,
    request_id: str,
    result: str,
    cleanup_result: str,
    evidence_id: str,
    validation_profile: str | None = None,
    toolchain_profile: str | None = None,
) -> dict[str, object]:
    """Route any durable physical proof through the shared #137 allowlist."""

    payload: dict[str, object] = {
        "repository": repository,
        "source_sha": source_sha,
        "workflow_run_id": workflow_run_id,
        "job_id": job_id,
        "device_family": device_family,
        "request_id": request_id,
        "result": result,
        "cleanup_result": cleanup_result,
        "evidence_id": evidence_id,
    }
    if validation_profile is not None:
        payload["validation_profile"] = validation_profile
    if toolchain_profile is not None:
        payload["toolchain_profile"] = toolchain_profile
    return validate_stable_evidence(payload, contract_root=contract_root)


def evidence_id(packet: Mapping[str, object]) -> str:
    return hashlib.sha256(canonical_json(packet).encode()).hexdigest()
