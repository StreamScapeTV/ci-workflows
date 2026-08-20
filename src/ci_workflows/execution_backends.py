"""Bounded execution-backend selection layered over semantic runner resolution."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

CONTRACT_PATH = Path("contracts/runner-execution-backends.json")
MAPPINGS_PATH = Path("generated/runner-execution-backends.json")
_SAFE_CODE = re.compile(r"^[a-z][a-z0-9_]{2,95}$")
_EXPECTED_BACKENDS = ("organization", "github-hosted")
_EXPECTED_HOSTED_RUNS_ON = ("ubuntu-latest",)
_EXPECTED_HOSTED_PROFILES = {
    "source.resolve": ("general-tiny",),
    "validation.node": ("general-small",),
    "validation.python": ("general-small",),
}


class ExecutionBackendError(RuntimeError):
    """Fail-closed backend-selection error carrying one stable code."""

    def __init__(self, code: str) -> None:
        if _SAFE_CODE.fullmatch(code) is None:
            raise ValueError("execution backend error code must be safe")
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class ExecutionBackendResolution:
    """One exact backend selection after semantic profile resolution."""

    execution_backend: str
    execution_profile: str
    runs_on: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        labels = list(self.runs_on)
        return {
            "execution_backend": self.execution_backend,
            "execution_profile": self.execution_profile,
            "runs_on": labels,
            "runs_on_json": json.dumps(labels, separators=(",", ":")),
        }


def _require(condition: bool, code: str = "invalid_execution_backend_contract") -> None:
    if not condition:
        raise ExecutionBackendError(code)


def _string_list(value: object) -> tuple[str, ...]:
    _require(isinstance(value, list) and bool(value))
    _require(all(isinstance(item, str) and item for item in value))
    values = tuple(value)
    _require(len(values) == len(set(values)))
    return values


def validate_execution_backend_contract(contract: Mapping[str, Any]) -> None:
    """Validate the small hosted-scheduling contract against the reviewed #405 scope."""

    _require(contract.get("schema_version") == 1)
    _require(contract.get("default_backend") == "organization")
    _require(_string_list(contract.get("allowed_backends")) == _EXPECTED_BACKENDS)

    organization = contract.get("organization")
    _require(isinstance(organization, Mapping))
    _require(set(organization) == {"selector_authority", "preserve_semantic_selector"})
    _require(organization.get("selector_authority") == "contracts/runner-profiles.json")
    _require(organization.get("preserve_semantic_selector") is True)

    hosted = contract.get("github-hosted")
    _require(isinstance(hosted, Mapping))
    _require(set(hosted) == {"runs_on", "supported_workflow_profiles"})
    _require(_string_list(hosted.get("runs_on")) == _EXPECTED_HOSTED_RUNS_ON)
    mappings = hosted.get("supported_workflow_profiles")
    _require(isinstance(mappings, Mapping))
    _require(set(mappings) == set(_EXPECTED_HOSTED_PROFILES))
    for workflow_api, expected_profiles in _EXPECTED_HOSTED_PROFILES.items():
        _require(_string_list(mappings.get(workflow_api)) == expected_profiles)


def load_execution_backend_contract(root: Path) -> dict[str, Any]:
    """Load and validate the canonical backend contract from one repository root."""

    try:
        value = json.loads((root.resolve() / CONTRACT_PATH).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ExecutionBackendError("invalid_execution_backend_contract") from error
    _require(isinstance(value, dict))
    validate_execution_backend_contract(value)
    return value


def generate_execution_backend_mapping(contract: Mapping[str, Any]) -> dict[str, Any]:
    """Render the deterministic consumer/debug mapping derived from the contract."""

    validate_execution_backend_contract(contract)
    organization = contract["organization"]
    hosted = contract["github-hosted"]
    return {
        "schema_version": 1,
        "generated_from": CONTRACT_PATH.as_posix(),
        "default_backend": contract["default_backend"],
        "backends": {
            "organization": {
                "selector_authority": organization["selector_authority"],
                "preserve_semantic_selector": organization["preserve_semantic_selector"],
            },
            "github-hosted": {
                "runs_on": list(hosted["runs_on"]),
                "supported_workflow_profiles": {
                    key: list(value)
                    for key, value in hosted["supported_workflow_profiles"].items()
                },
            },
        },
    }


def validate_generated_mapping(root: Path, contract: Mapping[str, Any]) -> None:
    """Fail when the checked-in generated backend mapping drifts from authority."""

    try:
        actual = json.loads((root.resolve() / MAPPINGS_PATH).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ExecutionBackendError("execution_backend_generated_drift") from error
    if actual != generate_execution_backend_mapping(contract):
        raise ExecutionBackendError("execution_backend_generated_drift")


def resolve_execution_backend(
    *,
    contract: Mapping[str, Any],
    workflow_api: str,
    execution_backend: str,
    execution_profile: str,
    organization_runs_on: Sequence[str],
) -> ExecutionBackendResolution:
    """Resolve a bounded compute backend without changing workload semantics."""

    validate_execution_backend_contract(contract)
    backend = execution_backend.strip()
    if backend not in contract["allowed_backends"]:
        raise ExecutionBackendError("invalid_execution_backend")

    organization = tuple(str(label) for label in organization_runs_on)
    if not organization or any(not label for label in organization):
        raise ExecutionBackendError("invalid_organization_runner")
    if "self-hosted" in organization:
        raise ExecutionBackendError("invalid_organization_runner")

    if backend == "organization":
        return ExecutionBackendResolution(
            execution_backend=backend,
            execution_profile=execution_profile,
            runs_on=organization,
        )

    hosted = contract["github-hosted"]
    mappings = hosted["supported_workflow_profiles"]
    supported = tuple(mappings.get(workflow_api, ()))
    if execution_profile not in supported:
        raise ExecutionBackendError("unsupported_execution_backend_profile")
    return ExecutionBackendResolution(
        execution_backend=backend,
        execution_profile=execution_profile,
        runs_on=tuple(hosted["runs_on"]),
    )


__all__ = (
    "CONTRACT_PATH",
    "MAPPINGS_PATH",
    "ExecutionBackendError",
    "ExecutionBackendResolution",
    "generate_execution_backend_mapping",
    "load_execution_backend_contract",
    "resolve_execution_backend",
    "validate_execution_backend_contract",
    "validate_generated_mapping",
)
