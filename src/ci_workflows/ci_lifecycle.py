"""Small Agent State lifecycle client shared by canonical Central CI workflows."""
from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass
import re
import sys
from typing import Any, Mapping, Sequence
import urllib.error
import urllib.parse
import urllib.request
import uuid

_MAX_RESPONSE_BYTES = 64 * 1024
_HTTP_TIMEOUT_SECONDS = 30
_REPOSITORY = re.compile(r"[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}\Z")
_PROJECT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_WORKFLOW = re.compile(r"[a-z0-9][a-z0-9._-]{0,127}\Z")
_PROFILE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")
_SHA = re.compile(r"[0-9a-f]{40}\Z")
_DIAGNOSTIC_STATUS = re.compile(r"[a-z][a-z0-9_-]{0,31}\Z")
_R2_RECEIPT = re.compile(
    r"r2:ci-diagnostics/"
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/"
    r"[1-9][0-9]{0,18}-[1-9][0-9]{0,3}\.log\.gz#sha256=[0-9a-f]{64}\Z"
)
_TERMINAL = {"succeeded", "failed", "cancelled", "timed_out"}


class CiLifecycleError(RuntimeError):
    """Stable non-sensitive lifecycle failure."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise CiLifecycleError(code)


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode(
        "utf-8"
    )


def _uuid(value: object) -> str:
    _require(isinstance(value, str), "invalid_ci_run_id")
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError):
        raise CiLifecycleError("invalid_ci_run_id") from None
    _require(str(parsed) == value.lower(), "invalid_ci_run_id")
    return str(parsed)


def _safe_project(value: object) -> str:
    _require(isinstance(value, str) and _PROJECT.fullmatch(value) is not None, "invalid_project_key")
    return value


def _safe_repository(value: object) -> str:
    _require(
        isinstance(value, str) and _REPOSITORY.fullmatch(value) is not None,
        "invalid_repository",
    )
    return value


def _safe_ref(value: object) -> str:
    _require(isinstance(value, str), "invalid_ref")
    text = value.strip()
    _require(0 < len(text.encode("utf-8")) <= 512, "invalid_ref")
    _require(
        "\x00" not in text
        and "\r" not in text
        and "\n" not in text
        and not text.startswith("refs/heads/")
        and not text.startswith("refs/tags/"),
        "invalid_ref",
    )
    return text


def _safe_workflow(value: object) -> str:
    _require(
        isinstance(value, str) and _WORKFLOW.fullmatch(value) is not None,
        "invalid_workflow_key",
    )
    return value


def _safe_profile(value: object) -> str:
    _require(
        isinstance(value, str) and _PROFILE.fullmatch(value) is not None,
        "invalid_test_profile",
    )
    return value


def _safe_sha(value: object) -> str:
    _require(isinstance(value, str) and _SHA.fullmatch(value) is not None, "invalid_observed_sha")
    return value


def _safe_optional_scalar(value: object, code: str, limit: int = 256) -> str | None:
    if value in (None, ""):
        return None
    _require(isinstance(value, str), code)
    _require(
        0 < len(value.encode("utf-8")) <= limit
        and "\x00" not in value
        and "\r" not in value
        and "\n" not in value,
        code,
    )
    return value


def _bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered == "true":
            return True
        if lowered == "false":
            return False
    raise CiLifecycleError("invalid_is_tag")


@dataclass(frozen=True)
class WorkflowIdentity:
    project_key: str
    repository: str
    ref: str
    is_tag: bool
    workflow_key: str
    profile: str
    execution_repository: str
    run_id: int
    run_attempt: int
    run_url: str

    @classmethod
    def from_values(
        cls,
        *,
        project_key: object,
        repository: object,
        ref: object,
        is_tag: object,
        workflow_key: object,
        profile: object,
        environment: Mapping[str, str] = os.environ,
    ) -> "WorkflowIdentity":
        execution_repository = _safe_repository(environment.get("GITHUB_REPOSITORY"))
        run_id_text = environment.get("GITHUB_RUN_ID", "")
        attempt_text = environment.get("GITHUB_RUN_ATTEMPT", "")
        server_url = environment.get("GITHUB_SERVER_URL", "")
        _require(run_id_text.isdigit() and int(run_id_text) > 0, "invalid_github_run_id")
        _require(attempt_text.isdigit() and 1 <= int(attempt_text) <= 1000, "invalid_github_run_attempt")
        parsed = urllib.parse.urlsplit(server_url)
        _require(
            parsed.scheme == "https"
            and bool(parsed.netloc)
            and parsed.path in ("", "/")
            and not parsed.query
            and not parsed.fragment,
            "invalid_github_server_url",
        )
        run_id = int(run_id_text)
        return cls(
            project_key=_safe_project(project_key),
            repository=_safe_repository(repository),
            ref=_safe_ref(ref),
            is_tag=_bool(is_tag),
            workflow_key=_safe_workflow(workflow_key),
            profile=_safe_profile(profile),
            execution_repository=execution_repository,
            run_id=run_id,
            run_attempt=int(attempt_text),
            run_url=f"{server_url.rstrip('/')}/{execution_repository}/actions/runs/{run_id}",
        )

    def registration(self) -> dict[str, object]:
        return {
            "project_key": self.project_key,
            "repository": self.repository,
            "ref": self.ref,
            "is_tag": self.is_tag,
            "workflow_key": self.workflow_key,
            "test_profile": self.profile,
            "external_repository": self.execution_repository,
            "external_run_id": self.run_id,
            "external_run_attempt": self.run_attempt,
            "external_run_url": self.run_url,
        }

    def running_patch(self) -> dict[str, object]:
        return {
            "status": "running",
            "repository": self.repository,
            "ref": self.ref,
            "is_tag": self.is_tag,
            "workflow_key": self.workflow_key,
            "test_profile": self.profile,
            "external_repository": self.execution_repository,
            "external_run_id": self.run_id,
            "external_run_attempt": self.run_attempt,
            "external_run_url": self.run_url,
        }


class AgentStateCiClient:
    """RPC-only CI lifecycle client for GitHub Actions."""

    def __init__(
        self,
        url: str,
        secret_key: str,
        *,
        opener: Any = urllib.request.urlopen,
        require_uploaded_diagnostic: bool = False,
    ) -> None:
        parsed = urllib.parse.urlsplit(url.rstrip("/"))
        _require(
            parsed.scheme == "https"
            and bool(parsed.netloc)
            and parsed.path in ("", "/")
            and not parsed.query
            and not parsed.fragment,
            "invalid_agent_state_url",
        )
        _require(bool(secret_key), "missing_agent_state_secret")
        self._url = url.rstrip("/")
        self._key = secret_key
        self._opener = opener
        self._require_uploaded_diagnostic = require_uploaded_diagnostic

    @classmethod
    def from_environment(
        cls,
        environment: Mapping[str, str] = os.environ,
        *,
        opener: Any = urllib.request.urlopen,
    ) -> "AgentStateCiClient":
        return cls(
            environment.get("AGENT_STATE_SUPABASE_URL", ""),
            environment.get("AGENT_STATE_SUPABASE_SECRET_KEY", ""),
            opener=opener,
            require_uploaded_diagnostic=bool(environment.get("CIW_PRIVATE_LOG_PATH", "")),
        )

    def _rpc(self, name: str, args: Mapping[str, object]) -> dict[str, Any]:
        _require(re.fullmatch(r"[a-z_]{3,64}", name) is not None, "invalid_rpc")
        request = urllib.request.Request(
            f"{self._url}/rest/v1/rpc/{name}",
            data=_canonical(dict(args)),
            method="POST",
            headers={
                "apikey": self._key,
                "Content-Type": "application/json",
                "Content-Profile": "agent_api",
                "Accept-Profile": "agent_api",
            },
        )
        try:
            with self._opener(request, timeout=_HTTP_TIMEOUT_SECONDS) as response:
                raw = response.read(_MAX_RESPONSE_BYTES + 1)
                status = int(getattr(response, "status", response.getcode()))
        except urllib.error.HTTPError as error:
            raise CiLifecycleError(f"agent_state_http_{int(error.code)}") from None
        except (OSError, urllib.error.URLError, ValueError):
            raise CiLifecycleError("agent_state_unavailable") from None
        _require(status == 200, f"agent_state_http_{status}")
        _require(len(raw) <= _MAX_RESPONSE_BYTES, "agent_state_response_too_large")
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise CiLifecycleError("agent_state_invalid_response") from None
        _require(isinstance(value, dict), "agent_state_invalid_response")
        return value

    @staticmethod
    def _run(value: Mapping[str, object]) -> dict[str, Any]:
        run = value.get("run")
        _require(value.get("ok") is True and isinstance(run, dict), "agent_state_ci_rejected")
        return run

    def start(self, identity: WorkflowIdentity, ci_run_id: str | None = None) -> str:
        if ci_run_id:
            selected_id = _uuid(ci_run_id)
            result = self._rpc(
                "transition_ci_run",
                {"p_ci_run_id": selected_id, "p_patch": identity.running_patch()},
            )
            self._run(result)
            return selected_id
        result = self._rpc(
            "register_ci_run",
            {"p_registration": identity.registration()},
        )
        run = self._run(result)
        return _uuid(run.get("ci_run_id"))

    def evidence(self, ci_run_id: str, observed_sha: str) -> None:
        result = self._rpc(
            "transition_ci_run",
            {
                "p_ci_run_id": _uuid(ci_run_id),
                "p_patch": {"observed_source_sha": _safe_sha(observed_sha)},
            },
        )
        self._run(result)

    def finish(
        self,
        ci_run_id: str,
        *,
        status: str,
        error_summary: str | None = None,
        diagnostic_status: str | None = None,
        diagnostic_key: str | None = None,
    ) -> None:
        _require(status in _TERMINAL, "invalid_terminal_status")
        patch: dict[str, object] = {"status": status}
        safe_error = _safe_optional_scalar(error_summary, "invalid_error_summary")
        safe_diagnostic_key = _safe_optional_scalar(
            diagnostic_key,
            "invalid_diagnostic_key",
            512,
        )
        if self._require_uploaded_diagnostic:
            _require(diagnostic_status == "uploaded", "private_log_not_uploaded")
            _require(
                safe_diagnostic_key is not None
                and _R2_RECEIPT.fullmatch(safe_diagnostic_key) is not None,
                "private_log_not_uploaded",
            )
        if safe_error is not None:
            patch["error_summary"] = safe_error
        if diagnostic_status not in (None, ""):
            _require(
                isinstance(diagnostic_status, str)
                and _DIAGNOSTIC_STATUS.fullmatch(diagnostic_status) is not None,
                "invalid_diagnostic_status",
            )
            patch["diagnostic_status"] = diagnostic_status
        if safe_diagnostic_key is not None:
            patch["diagnostic_key"] = safe_diagnostic_key
        result = self._rpc(
            "transition_ci_run",
            {"p_ci_run_id": _uuid(ci_run_id), "p_patch": patch},
        )
        self._run(result)


def _required_environment(environment: Mapping[str, str], name: str) -> str:
    value = environment.get(name, "")
    if not value:
        raise CiLifecycleError(f"missing_{name.lower()}")
    return value


def _write_output(name: str, value: str, environment: Mapping[str, str]) -> None:
    path = environment.get("GITHUB_OUTPUT", "")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(f"{name}={value}\n")


def _identity_from_environment(environment: Mapping[str, str]) -> WorkflowIdentity:
    return WorkflowIdentity.from_values(
        project_key=_required_environment(environment, "INPUT_PROJECT_KEY"),
        repository=_required_environment(environment, "INPUT_REPOSITORY"),
        ref=_required_environment(environment, "INPUT_REF"),
        is_tag=_required_environment(environment, "INPUT_IS_TAG"),
        workflow_key=_required_environment(environment, "INPUT_WORKFLOW_KEY"),
        profile=_required_environment(environment, "INPUT_PROFILE"),
        environment=environment,
    )


def lifecycle_start(environment: Mapping[str, str] = os.environ) -> None:
    client = AgentStateCiClient.from_environment(environment)
    ci_run_id = client.start(
        _identity_from_environment(environment),
        environment.get("INPUT_CI_RUN_ID", "") or None,
    )
    _write_output("ci_run_id", ci_run_id, environment)


def lifecycle_evidence(environment: Mapping[str, str] = os.environ) -> None:
    client = AgentStateCiClient.from_environment(environment)
    ci_run_id = _required_environment(environment, "INPUT_CI_RUN_ID")
    client.evidence(
        ci_run_id,
        _required_environment(environment, "INPUT_OBSERVED_SHA"),
    )
    _write_output("ci_run_id", ci_run_id, environment)


def lifecycle_finish(environment: Mapping[str, str] = os.environ) -> None:
    client = AgentStateCiClient.from_environment(environment)
    ci_run_id = _required_environment(environment, "INPUT_CI_RUN_ID")
    client.finish(
        ci_run_id,
        status=_required_environment(environment, "INPUT_TERMINAL_STATUS"),
        error_summary=environment.get("INPUT_ERROR_SUMMARY", "") or None,
        diagnostic_status=environment.get("INPUT_DIAGNOSTIC_STATUS", "") or None,
        diagnostic_key=environment.get("INPUT_DIAGNOSTIC_KEY", "") or None,
    )
    _write_output("ci_run_id", ci_run_id, environment)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Shared Central CI Agent State lifecycle")
    result.add_argument("phase", choices=("start", "evidence", "finish"))
    return result


def main(
    argv: Sequence[str] | None = None,
    environment: Mapping[str, str] = os.environ,
) -> int:
    args = parser().parse_args(list(argv) if argv is not None else None)
    try:
        if args.phase == "start":
            lifecycle_start(environment)
        elif args.phase == "evidence":
            lifecycle_evidence(environment)
        else:
            lifecycle_finish(environment)
    except CiLifecycleError as error:
        print(error.code, file=sys.stderr)
        return 1
    return 0


__all__ = (
    "AgentStateCiClient",
    "CiLifecycleError",
    "WorkflowIdentity",
    "lifecycle_evidence",
    "lifecycle_finish",
    "lifecycle_start",
    "main",
    "parser",
)
