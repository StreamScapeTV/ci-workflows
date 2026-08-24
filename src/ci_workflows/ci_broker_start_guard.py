"""Fail closed and terminalize broker dependency-admission failures."""
from __future__ import annotations

from typing import Any, Mapping

from .ci_broker import (
    BrokerConfig,
    BrokerError,
    BrokerServer,
    CiBroker,
    _safe_repository,
    _safe_sha,
    _uuid,
)
from .ci_broker_dependencies import DependencyCiBroker, _profile_from_payload


def _exact_commit_identity(
    source_github: Any,
    repository: str,
    sha: str,
    token: str,
) -> dict[str, object]:
    """Read one exact Git commit object without the oversized REST files payload."""

    repository = _safe_repository(repository)
    sha = _safe_sha(sha)
    request = getattr(source_github, "_request", None)
    if not callable(request):
        raise BrokerError("private_dependency_identity_unavailable", 500)
    _status, value = request(
        "GET",
        f"/repos/{repository}/git/commits/{sha}",
        token=token,
    )
    if not isinstance(value, dict):
        raise BrokerError("github_commit_missing", 502)
    return value


class GuardedDependencyCiBroker(DependencyCiBroker):
    """Dependency broker that never leaves a claimed agent request queued on admission failure."""

    def action_start(
        self,
        raw: bytes,
        headers: Mapping[str, str],
    ) -> dict[str, object]:
        _claims, _request, envelope = self._action_identity(raw, headers)
        profile = _profile_from_payload(envelope.get("profile"))
        dependency = profile.private_dependency
        dependency_token = ""

        dedupe = envelope.get("dedupe")
        ci_run_id: str | None = None
        if isinstance(dedupe, dict) and dedupe.get("kind") == "agent_request":
            ci_run_id = _uuid(dedupe.get("ci_run_id"))

        if dependency is not None:
            try:
                dependency_token = self.source_github.repository_token(dependency.repository)
                observed = _exact_commit_identity(
                    self.source_github,
                    dependency.repository,
                    dependency.sha,
                    dependency_token,
                )
                if _safe_sha(observed.get("sha")) != dependency.sha:
                    raise BrokerError("private_dependency_source_mismatch", 409)
            except BrokerError as error:
                if ci_run_id is not None:
                    self._fail_claimed(ci_run_id, error.code)
                raise
            except Exception:
                if ci_run_id is not None:
                    self._fail_claimed(ci_run_id, "private_dependency_admission_internal")
                raise BrokerError("private_dependency_admission_internal", 500) from None

        # Bypass DependencyCiBroker.action_start because the dependency admission
        # above is the guarded implementation of that extra step.
        result = CiBroker.action_start(self, raw, headers)
        if dependency is None:
            result["private_dependency"] = None
        else:
            result["private_dependency"] = {
                **dependency.as_payload(),
                "token": dependency_token,
            }
        return result


def serve(config: BrokerConfig | None = None) -> None:
    selected = config or BrokerConfig.from_environment()
    broker = GuardedDependencyCiBroker(selected)
    server = BrokerServer(("0.0.0.0", selected.port), broker)
    server.serve_forever(poll_interval=0.5)


__all__ = ("GuardedDependencyCiBroker", "_exact_commit_identity", "serve")
