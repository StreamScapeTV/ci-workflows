"""Deterministic structured evidence with bounded redaction semantics."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .foundation_types import (
    FoundationError,
    atomic_write_text,
    canonical_json,
    full_sha,
    load_contract,
    require,
    safe_id,
    safe_name,
    stable_identifier,
)

EVIDENCE_CONTRACT = "contracts/evidence-policy.json"
_TOKEN = re.compile(
    r"(?:gh[pousr]_[A-Za-z0-9_]{24,}|github_pat_[A-Za-z0-9_]{40,}|AKIA[0-9A-Z]{16}|eyJ[A-Za-z0-9_-]{12,}\.[A-Za-z0-9_-]{12,}\.[A-Za-z0-9_-]{12,})"
)
_URL = re.compile(r"https?://[^\s\]\[(){}<>\"']+")
_UNIX_PATH = re.compile(r"(?<![A-Za-z0-9_.-])/(?:home|Users|private|tmp|var|opt|runner|github)/[^\s\]\[(){}<>\"']+")
_WINDOWS_PATH = re.compile(r"\b[A-Za-z]:\\[^\r\n\t\"']+")
_ENV_ASSIGNMENT = re.compile(
    r"(?i)\b[A-Z0-9_]*(?:TOKEN|SECRET|PASSWORD|AUTH|PRIVATE_KEY|KUBECONFIG)[A-Z0-9_]*\s*=\s*[^\s]+"
)


@dataclass(frozen=True)
class EvidenceResult:
    evidence_id: str
    payload: Mapping[str, Any]
    json_text: str

    def output_values(self) -> dict[str, str]:
        return {
            "evidence_id": self.evidence_id,
            "evidence_json": self.json_text,
            "redacted": "true",
        }


def redact_text(value: str, *, contract_root: Path) -> str:
    """Redact tokens, private URLs, runner paths, and secret assignments."""

    contract = load_contract(contract_root, EVIDENCE_CONTRACT)
    placeholders = contract.get("redaction_placeholders")
    require(isinstance(placeholders, dict), "evidence_contract_invalid")
    result = _ENV_ASSIGNMENT.sub(str(placeholders.get("environment")), value)
    result = _TOKEN.sub(str(placeholders.get("token")), result)
    result = _URL.sub(str(placeholders.get("url")), result)
    result = _UNIX_PATH.sub(str(placeholders.get("path")), result)
    result = _WINDOWS_PATH.sub(str(placeholders.get("path")), result)
    return result


def _toolchain(raw: Mapping[str, Any], contract: Mapping[str, Any]) -> dict[str, str]:
    maximum_count = contract.get("maximum_tool_count")
    maximum_length = contract.get("maximum_value_length")
    require(isinstance(maximum_count, int) and isinstance(maximum_length, int), "evidence_contract_invalid")
    require(len(raw) <= maximum_count, "evidence_toolchain_too_large")
    result: dict[str, str] = {}
    for key, value in raw.items():
        identifier = safe_id(key, "invalid_evidence_tool_id")
        require(isinstance(value, str) and 0 < len(value) <= maximum_length, "invalid_evidence_tool_version")
        require(re.fullmatch(r"[0-9A-Za-z][0-9A-Za-z.+_-]{0,127}", value) is not None, "invalid_evidence_tool_version")
        result[identifier] = value
    return dict(sorted(result.items()))


def parse_toolchain_json(raw: str) -> Mapping[str, str]:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as error:
        raise FoundationError("evidence_toolchain_invalid") from error
    require(isinstance(value, dict), "evidence_toolchain_invalid")
    return value


def build_evidence(
    *,
    source_sha: str,
    workflow_release: str,
    runner_profile: str,
    toolchain: Mapping[str, Any],
    command_profile: str,
    result: str,
    cleanup_state: str,
    cleanup_removed_paths: int,
    contract_root: Path,
) -> EvidenceResult:
    """Build one canonical evidence record from bounded reviewed fields."""

    contract = load_contract(contract_root, EVIDENCE_CONTRACT)
    source_sha = full_sha(source_sha)
    workflow_release = safe_name(workflow_release, "invalid_workflow_release")
    runner_profile = safe_id(runner_profile, "invalid_runner_profile")
    command_profile = safe_id(command_profile, "invalid_command_profile")
    require(result in contract.get("allowed_results", []), "invalid_evidence_result")
    require(cleanup_state in contract.get("allowed_cleanup_states", []), "invalid_cleanup_state")
    require(
        isinstance(cleanup_removed_paths, int)
        and not isinstance(cleanup_removed_paths, bool)
        and 0 <= cleanup_removed_paths <= 10000,
        "invalid_cleanup_count",
    )
    normalized_tools = _toolchain(toolchain, contract)
    payload = {
        "schema_version": 1,
        "source_sha": source_sha,
        "workflow_release": workflow_release,
        "runner_profile": runner_profile,
        "toolchain": normalized_tools,
        "command_profile": command_profile,
        "result": result,
        "cleanup": {
            "state": cleanup_state,
            "removed_paths": cleanup_removed_paths,
        },
        "redacted": True,
    }
    evidence_id = stable_identifier("evidence", payload, length=28)
    payload = {**payload, "evidence_id": evidence_id}
    return EvidenceResult(
        evidence_id=evidence_id,
        payload=payload,
        json_text=canonical_json(payload),
    )


def write_evidence(state_root: Path, evidence: EvidenceResult) -> Path:
    root = state_root.resolve()
    target = root / "evidence" / "evidence.json"
    require(root in target.resolve(strict=False).parents, "evidence_path_escape")
    require(target.parent.is_dir() and not target.parent.is_symlink(), "evidence_root_unavailable")
    atomic_write_text(target, evidence.json_text + "\n", mode=0o600)
    return target
