"""Bounded execution-backend selection layered over semantic runner resolution."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Sequence

_SAFE_CODE = re.compile(r"^[a-z][a-z0-9_]{2,95}$")
_ALLOWED_BACKENDS = frozenset({"organization", "github-hosted"})
_GITHUB_HOSTED_RUNS_ON = ("ubuntu-latest",)
_GITHUB_HOSTED_PROFILES = {
    "source.resolve": frozenset({"general-tiny"}),
    "validation.node": frozenset({"general-small"}),
    "validation.python": frozenset({"general-small"}),
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


def resolve_execution_backend(
    *,
    workflow_api: str,
    execution_backend: str,
    execution_profile: str,
    organization_runs_on: Sequence[str],
) -> ExecutionBackendResolution:
    """Resolve a bounded compute backend without changing workload semantics."""

    backend = execution_backend.strip()
    if backend not in _ALLOWED_BACKENDS:
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

    supported = _GITHUB_HOSTED_PROFILES.get(workflow_api, frozenset())
    if execution_profile not in supported:
        raise ExecutionBackendError("unsupported_execution_backend_profile")
    return ExecutionBackendResolution(
        execution_backend=backend,
        execution_profile=execution_profile,
        runs_on=_GITHUB_HOSTED_RUNS_ON,
    )


__all__ = (
    "ExecutionBackendError",
    "ExecutionBackendResolution",
    "resolve_execution_backend",
)
