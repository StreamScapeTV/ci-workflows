"""Canonical terminal supply evidence for trusted OCI publication."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .foundation_types import FoundationError, canonical_json, load_contract
from .oci_publish_contract import OciPublishError

_FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_FOUNDATION_EVIDENCE = re.compile(r"^evidence-[0-9a-f]{28}$")
_OUTCOMES = frozenset({"success", "failure", "skipped"})
_EXECUTION_RESULTS = frozenset({"success", "failure"})
_TOOL_CONTRACT = "contracts/tool-lock.json"


@dataclass(frozen=True)
class SupplyEvidence:
    """One deterministic redacted terminal supply record."""

    payload: Mapping[str, Any]
    evidence_id: str

    @property
    def json_text(self) -> str:
        return canonical_json(self.payload)

    @property
    def summary(self) -> str:
        return "\n".join(
            (
                "## Final OCI supply evidence",
                "",
                "```json",
                self.json_text,
                "```",
                "",
            )
        )

    def output_values(self) -> dict[str, str]:
        return {
            "result": "success",
            "supply_evidence_id": self.evidence_id,
        }


def _require(condition: bool, code: str = "terminal_evidence_invalid") -> None:
    if not condition:
        raise OciPublishError(code)


def _exact_toolchain(raw: str, root: Path) -> Mapping[str, str]:
    try:
        actual = json.loads(raw)
        contract = load_contract(root, _TOOL_CONTRACT)
    except (json.JSONDecodeError, FoundationError) as error:
        raise OciPublishError("terminal_toolchain_invalid") from error
    _require(isinstance(actual, dict), "terminal_toolchain_invalid")
    tool_sets = contract.get("tool_sets")
    tools = contract.get("tools")
    _require(
        isinstance(tool_sets, Mapping) and isinstance(tools, Mapping),
        "terminal_toolchain_invalid",
    )
    identifiers = tool_sets.get("oci-builder")
    _require(
        isinstance(identifiers, list)
        and identifiers
        and all(isinstance(item, str) for item in identifiers),
        "terminal_toolchain_invalid",
    )
    expected: dict[str, str] = {}
    for identifier in identifiers:
        entry = tools.get(identifier)
        _require(isinstance(entry, Mapping), "terminal_toolchain_invalid")
        version = entry.get("required_version")
        _require(
            entry.get("version_policy") == "exact"
            and isinstance(version, str)
            and version,
            "terminal_toolchain_invalid",
        )
        expected[identifier] = version
    _require(actual == expected, "terminal_toolchain_mismatch")
    return dict(sorted(expected.items()))


def _outcome(value: str) -> str:
    _require(value in _OUTCOMES)
    return value


def build_supply_evidence(
    *,
    root: Path,
    central_workflow_sha: str,
    publication_helper_sha: str,
    builder_id: str,
    runner_profile: str,
    toolchain_json: str,
    publication_evidence_id: str,
    foundation_evidence_id: str,
    registry_write_policy_id: str,
    registry_write_policy_host: str,
    registry_write_policy_enforcement: str,
    registry_write_policy_authority_repository: str,
    registry_write_policy_authority_source_sha: str,
    registry_write_policy_evidence_id: str,
    source_sha: str,
    product_id: str,
    release_version: str,
    execution_result: str,
    build_cleanup_outcome: str,
    build_residue_outcome: str,
    publication_cleanup_outcome: str,
    publication_residue_outcome: str,
    workspace_cleanup_outcome: str,
) -> SupplyEvidence:
    """Validate and bind terminal identities, execution, and cleanup evidence."""

    _require(_FULL_SHA.fullmatch(central_workflow_sha) is not None)
    _require(_FULL_SHA.fullmatch(publication_helper_sha) is not None)
    _require(_FULL_SHA.fullmatch(source_sha) is not None)
    _require(_SHA256.fullmatch(publication_evidence_id) is not None)
    _require(_FOUNDATION_EVIDENCE.fullmatch(foundation_evidence_id) is not None)
    _require(
        re.fullmatch(
            r"[a-z0-9]+(?:[._-][a-z0-9]+)*", registry_write_policy_id
        )
        is not None
    )
    _require(
        re.fullmatch(
            r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
            r"(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+",
            registry_write_policy_host,
        )
        is not None
    )
    _require(
        registry_write_policy_enforcement
        == "server-side-create-only-tags-v1"
        and registry_write_policy_authority_repository == "StreamScapeTV/flux"
        and _FULL_SHA.fullmatch(registry_write_policy_authority_source_sha)
        is not None
        and re.fullmatch(
            r"sha256:[0-9a-f]{64}", registry_write_policy_evidence_id
        )
        is not None
    )
    _require(re.fullmatch(r"[a-z][a-z0-9-]{1,63}", builder_id) is not None)
    _require(re.fullmatch(r"[a-z][a-z0-9-]{1,63}", runner_profile) is not None)
    _require(re.fullmatch(r"[a-z0-9]+(?:[._-][a-z0-9]+)*", product_id) is not None)
    _require(
        re.fullmatch(
            r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)",
            release_version,
        )
        is not None
    )
    _require(execution_result in _EXECUTION_RESULTS)
    cleanup = {
        "build": {
            "cleanup": _outcome(build_cleanup_outcome),
            "residue": _outcome(build_residue_outcome),
        },
        "publication": {
            "cleanup": _outcome(publication_cleanup_outcome),
            "residue": _outcome(publication_residue_outcome),
        },
        "workspace": {"cleanup": _outcome(workspace_cleanup_outcome)},
    }
    cleanup["terminal_result"] = (
        "success"
        if all(
            outcome == "success"
            for group in (cleanup["build"], cleanup["publication"], cleanup["workspace"])
            for outcome in group.values()
        )
        else "failure"
    )
    record: dict[str, Any] = {
        "api": "oci.publish.supply-evidence",
        "schema_version": 1,
        "central": {
            "builder_id": builder_id,
            "publication_helper_sha": publication_helper_sha,
            "runner_profile": runner_profile,
            "workflow_sha": central_workflow_sha,
        },
        "cleanup": cleanup,
        "evidence": {
            "foundation_id": foundation_evidence_id,
            "publication_id": publication_evidence_id,
        },
        "execution": {"result": execution_result},
        "registry_write_policy": {
            "policy_id": registry_write_policy_id,
            "registry_host": registry_write_policy_host,
            "required_enforcement": registry_write_policy_enforcement,
            "status": "verified",
            "authority_repository": registry_write_policy_authority_repository,
            "authority_source_sha": registry_write_policy_authority_source_sha,
            "evidence_id": registry_write_policy_evidence_id,
        },
        "release": {
            "product_id": product_id,
            "source_sha": source_sha,
            "version": release_version,
        },
        "toolchain": _exact_toolchain(toolchain_json, root),
    }
    evidence_id = hashlib.sha256(canonical_json(record).encode("utf-8")).hexdigest()
    return SupplyEvidence(
        payload={**record, "supply_evidence_id": evidence_id},
        evidence_id=evidence_id,
    )
