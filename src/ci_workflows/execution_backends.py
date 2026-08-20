"""Small bounded execution-backend selector for reviewed Linux work."""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Sequence

_HOSTED_RUNS_ON = ("ubuntu-latest",)
_HOSTED_PROFILE_BY_WORKFLOW = {
    "source.resolve": frozenset({"general-tiny"}),
    "validation.node": frozenset({"general-small"}),
    "validation.python": frozenset({"general-small"}),
    "release.native-image-chart": frozenset({"buildah-high"}),
}
_LEGACY_HOSTED_PROFILES = frozenset({"general-tiny", "general-small"})


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
    workflow_api: str | None = None,
) -> ExecutionBackendResolution:
    """Preserve organization scheduling or use fixed hosted Linux for reviewed work."""

    backend = execution_backend.strip()
    organization = tuple(str(label) for label in organization_runs_on)
    if not organization or any(not label for label in organization) or "self-hosted" in organization:
        raise ExecutionBackendError("invalid_organization_runner")
    if backend == "organization":
        return ExecutionBackendResolution(backend, execution_profile, organization)
    if backend != "github-hosted":
        raise ExecutionBackendError("invalid_execution_backend")

    allowed_profiles = (
        _LEGACY_HOSTED_PROFILES
        if workflow_api is None
        else _HOSTED_PROFILE_BY_WORKFLOW.get(workflow_api, frozenset())
    )
    if execution_profile not in allowed_profiles:
        raise ExecutionBackendError("unsupported_execution_backend_profile")
    return ExecutionBackendResolution(backend, execution_profile, _HOSTED_RUNS_ON)


__all__ = ("ExecutionBackendError", "ExecutionBackendResolution", "resolve_execution_backend")
