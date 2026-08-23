"""Small bounded execution-backend selector for portable Linux work."""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Sequence

_HOSTED_RUNS_ON = ("ubuntu-latest",)
_HOSTED_PROFILES = {"general-tiny", "general-small"}


class ExecutionBackendError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class ExecutionBackendResolution:
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
    execution_backend: str,
    execution_profile: str,
    organization_runs_on: Sequence[str],
) -> ExecutionBackendResolution:
    """Preserve organization scheduling or use fixed hosted Linux for portable work."""

    backend = execution_backend.strip()
    organization = tuple(str(label) for label in organization_runs_on)
    if not organization or any(not label for label in organization) or "self-hosted" in organization:
        raise ExecutionBackendError("invalid_organization_runner")
    if backend == "organization":
        return ExecutionBackendResolution(backend, execution_profile, organization)
    if backend != "github-hosted":
        raise ExecutionBackendError("invalid_execution_backend")
    if execution_profile not in _HOSTED_PROFILES:
        raise ExecutionBackendError("unsupported_execution_backend_profile")
    return ExecutionBackendResolution(backend, execution_profile, _HOSTED_RUNS_ON)


__all__ = ("ExecutionBackendError", "ExecutionBackendResolution", "resolve_execution_backend")
