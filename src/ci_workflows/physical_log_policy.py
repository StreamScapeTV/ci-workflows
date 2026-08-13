"""Fail-closed durable evidence policy for physical-device CI."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Mapping

from .foundation_types import (
    FoundationError,
    bounded_int,
    canonical_json,
    full_sha,
    load_contract,
    repository_name,
    require,
    safe_id,
)

POLICY_PATH = "contracts/physical-log-policy.json"


def load_physical_log_policy(root: Path) -> Mapping[str, Any]:
    policy = load_contract(root, POLICY_PATH)
    stable = policy.get("stable_evidence")
    platform = policy.get("platform_generated_logs")
    require(isinstance(stable, dict), "physical_log_policy_invalid")
    require(isinstance(platform, dict), "physical_log_policy_invalid")
    return policy


def _policy_list(value: Any) -> tuple[str, ...]:
    require(
        isinstance(value, list)
        and all(isinstance(item, str) and item for item in value),
        "physical_log_policy_invalid",
    )
    return tuple(value)


def _validate_durable_text(text: str, stable: Mapping[str, Any]) -> None:
    require(isinstance(text, str), "physical_evidence_invalid")
    patterns = _policy_list(stable.get("forbidden_value_patterns"))
    for pattern in patterns:
        try:
            matched = re.search(pattern, text) is not None
        except re.error as error:
            raise FoundationError("physical_log_policy_invalid") from error
        require(not matched, "physical_evidence_private_metadata")


def validate_durable_text(text: str, *, contract_root: Path) -> str:
    policy = load_physical_log_policy(contract_root)
    stable = policy["stable_evidence"]
    _validate_durable_text(text, stable)
    return text


def validate_stable_evidence(
    payload: Mapping[str, Any], *, contract_root: Path
) -> dict[str, Any]:
    """Validate the only durable product evidence shape allowed for physical CI."""
    require(isinstance(payload, Mapping), "physical_evidence_invalid")
    policy = load_physical_log_policy(contract_root)
    stable = policy["stable_evidence"]
    required = set(_policy_list(stable.get("required_fields")))
    allowed = set(_policy_list(stable.get("allowed_fields")))
    keys = set(payload)
    require(required <= keys <= allowed, "physical_evidence_fields_invalid")

    forbidden_fragments = tuple(
        fragment.casefold()
        for fragment in _policy_list(stable.get("forbidden_field_fragments"))
    )
    for key in keys:
        require(isinstance(key, str), "physical_evidence_fields_invalid")
        lowered = key.casefold()
        require(
            not any(fragment in lowered for fragment in forbidden_fragments),
            "physical_evidence_private_metadata",
        )

    normalized: dict[str, Any] = {}
    normalized["repository"] = repository_name(
        payload["repository"], "physical_evidence_invalid"
    )
    normalized["source_sha"] = full_sha(
        payload["source_sha"], "physical_evidence_invalid"
    )
    normalized["workflow_run_id"] = bounded_int(
        payload["workflow_run_id"],
        minimum=1,
        maximum=2**63 - 1,
        instruction="physical_evidence_invalid",
    )
    normalized["job_id"] = bounded_int(
        payload["job_id"],
        minimum=1,
        maximum=2**63 - 1,
        instruction="physical_evidence_invalid",
    )

    families = set(_policy_list(stable.get("device_families")))
    family = payload["device_family"]
    require(isinstance(family, str) and family in families, "physical_evidence_invalid")
    normalized["device_family"] = family

    normalized["request_id"] = safe_id(
        payload["request_id"], "physical_evidence_invalid"
    )
    normalized["evidence_id"] = safe_id(
        payload["evidence_id"], "physical_evidence_invalid"
    )

    results = set(_policy_list(stable.get("results")))
    for field in ("result", "cleanup_result"):
        value = payload[field]
        require(isinstance(value, str) and value in results, "physical_evidence_invalid")
        normalized[field] = value

    for field in ("validation_profile", "toolchain_profile"):
        if field in payload:
            normalized[field] = safe_id(
                payload[field], "physical_evidence_invalid"
            )

    _validate_durable_text(canonical_json(normalized), stable)
    return normalized


def render_stable_evidence(
    payload: Mapping[str, Any], *, contract_root: Path
) -> str:
    return canonical_json(validate_stable_evidence(payload, contract_root=contract_root))


def platform_log_boundary(*, contract_root: Path) -> Mapping[str, str | bool]:
    policy = load_physical_log_policy(contract_root)
    platform = policy["platform_generated_logs"]
    required = {
        "workflow_source_can_suppress_runner_bootstrap_metadata": False,
        "retention_requirement": "shortest_repository_supported",
        "access_requirement": "repository_maintainers_only",
        "stable_evidence_reference": "repository_source_sha_run_id_job_id_only",
        "raw_log_excerpt_copy": "forbidden",
        "raw_log_attachment": "forbidden",
        "product_artifact_retention": "zero_by_default",
    }
    require(platform == required, "physical_log_policy_invalid")
    return dict(platform)
