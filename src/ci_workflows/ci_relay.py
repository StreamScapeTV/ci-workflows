"""Thin Agent State webhook to fixed Central workflow relay.

The relay authenticates one Agent State INSERT, claims the request exactly once,
and dispatches the fixed Central workflow. It owns no source admission, checkout,
product configuration, dependency handling, workflow lifecycle, or diagnostics.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import hmac
import json
import os
import re
from typing import Any, Mapping
import urllib.parse

from .ci_broker import (
    AgentStateClient,
    BrokerError,
    CENTRAL_REF,
    CENTRAL_REPOSITORY,
    CENTRAL_WORKFLOW,
    GitHubAppClient,
    _header,
    _json_object,
    _require,
    _safe_profile,
    _safe_project,
    _safe_repository,
    _safe_workflow_key,
    _uuid,
)

_INPUT_KEY = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}\Z")
_MAX_INPUTS = 16
_MAX_INPUT_VALUE_BYTES = 512
_MAX_INPUTS_JSON_BYTES = 8 * 1024
_SUPPORTED_WORKFLOW_KEY = "validation.apple"
_SUPPORTED_PROFILE = "host"


def _required(environment: Mapping[str, str], name: str) -> str:
    value = environment.get(name, "")
    _require(bool(value), f"missing_{name.lower()}", 500)
    return value


@dataclass(frozen=True)
class RelayConfig:
    dispatch_app_id: int
    dispatch_app_private_key: str
    agent_state_url: str
    agent_state_secret_key: str
    agent_state_webhook_secret: str
    port: int = 8080

    @classmethod
    def from_environment(
        cls,
        environment: Mapping[str, str] = os.environ,
    ) -> "RelayConfig":
        app_id = _required(environment, "GITHUB_DISPATCH_APP_ID")
        _require(app_id.isdigit() and int(app_id) > 0, "invalid_github_dispatch_app_id", 500)
        url = _required(environment, "AGENT_STATE_SUPABASE_URL").rstrip("/")
        parsed = urllib.parse.urlsplit(url)
        _require(
            parsed.scheme == "https"
            and bool(parsed.netloc)
            and parsed.path in ("", "/")
            and not parsed.query
            and not parsed.fragment,
            "invalid_agent_state_url",
            500,
        )
        port_text = environment.get("CI_BROKER_PORT", "8080")
        _require(
            port_text.isdigit() and 1 <= int(port_text) <= 65535,
            "invalid_ci_broker_port",
            500,
        )
        return cls(
            dispatch_app_id=int(app_id),
            dispatch_app_private_key=_required(
                environment, "GITHUB_DISPATCH_APP_PRIVATE_KEY"
            ),
            agent_state_url=url,
            agent_state_secret_key=_required(
                environment, "AGENT_STATE_SUPABASE_SECRET_KEY"
            ),
            agent_state_webhook_secret=_required(
                environment, "AGENT_STATE_WEBHOOK_SECRET"
            ),
            port=int(port_text),
        )


def _human_ref(value: object) -> str:
    _require(isinstance(value, str), "invalid_ref", 422)
    text = value.strip()
    _require(
        0 < len(text.encode("utf-8")) <= 512
        and "\x00" not in text
        and "\r" not in text
        and "\n" not in text
        and not text.startswith("refs/heads/")
        and not text.startswith("refs/tags/"),
        "invalid_ref",
        422,
    )
    return text


def _is_tag(value: object) -> bool:
    _require(isinstance(value, bool), "invalid_is_tag", 422)
    return value


def _bounded_inputs(value: object) -> dict[str, str | bool | int]:
    if value is None:
        return {}
    _require(
        isinstance(value, dict) and len(value) <= _MAX_INPUTS,
        "invalid_ci_inputs",
        422,
    )
    result: dict[str, str | bool | int] = {}
    for raw_key, raw_value in value.items():
        _require(
            isinstance(raw_key, str) and _INPUT_KEY.fullmatch(raw_key) is not None,
            "invalid_ci_inputs",
            422,
        )
        _require(
            isinstance(raw_value, (str, bool, int)) and not isinstance(raw_value, float),
            "invalid_ci_inputs",
            422,
        )
        if isinstance(raw_value, str):
            _require(
                len(raw_value.encode("utf-8")) <= _MAX_INPUT_VALUE_BYTES
                and "\x00" not in raw_value
                and "\r" not in raw_value
                and "\n" not in raw_value,
                "invalid_ci_inputs",
                422,
            )
        if isinstance(raw_value, int) and not isinstance(raw_value, bool):
            _require(
                -(2**63) <= raw_value <= 2**63 - 1,
                "invalid_ci_inputs",
                422,
            )
        result[raw_key] = raw_value
    encoded = json.dumps(
        result,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    _require(
        len(encoded.encode("utf-8")) <= _MAX_INPUTS_JSON_BYTES,
        "invalid_ci_inputs",
        422,
    )
    return result


def active_identity_key(
    *,
    repository: object,
    ref: object,
    is_tag: object,
    workflow_key: object,
    profile: object,
) -> str:
    """Return a bounded deterministic concurrency key for one active CI identity."""
    payload = {
        "is_tag": _is_tag(is_tag),
        "profile": _safe_profile(profile),
        "ref": _human_ref(ref),
        "repository": _safe_repository(repository),
        "workflow_key": _safe_workflow_key(workflow_key),
    }
    raw = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


@dataclass(frozen=True)
class RelayRequest:
    ci_run_id: str
    project_key: str
    repository: str
    ref: str
    is_tag: bool
    workflow_key: str
    profile: str
    inputs: dict[str, str | bool | int]

    @classmethod
    def from_claimed_run(cls, value: Mapping[str, object]) -> "RelayRequest":
        _require(value.get("origin") == "agent_request", "invalid_ci_origin", 422)
        _require(value.get("status") == "accepted", "invalid_ci_status", 409)
        requested_sha = value.get("requested_source_sha")
        _require(
            requested_sha in (None, ""),
            "requested_source_sha_unsupported",
            422,
        )
        workflow_key = _safe_workflow_key(value.get("workflow_key"))
        profile = _safe_profile(value.get("test_profile"))
        _require(
            workflow_key == _SUPPORTED_WORKFLOW_KEY and profile == _SUPPORTED_PROFILE,
            "unsupported_ci_intent",
            422,
        )
        return cls(
            ci_run_id=_uuid(value.get("ci_run_id")),
            project_key=_safe_project(value.get("project_key")),
            repository=_safe_repository(value.get("repository")),
            ref=_human_ref(value.get("ref")),
            is_tag=_is_tag(value.get("is_tag")),
            workflow_key=workflow_key,
            profile=profile,
            inputs=_bounded_inputs(value.get("inputs")),
        )

    @property
    def active_key(self) -> str:
        return active_identity_key(
            repository=self.repository,
            ref=self.ref,
            is_tag=self.is_tag,
            workflow_key=self.workflow_key,
            profile=self.profile,
        )

    def workflow_inputs(self) -> dict[str, str]:
        return {
            "active_key": self.active_key,
            "ci_run_id": self.ci_run_id,
            "project_key": self.project_key,
            "repository": self.repository,
            "ref": self.ref,
            "is_tag": "true" if self.is_tag else "false",
            "workflow_key": self.workflow_key,
            "profile": self.profile,
            "inputs_json": json.dumps(
                self.inputs,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
            ),
        }


class RelayGitHubClient(GitHubAppClient):
    """GitHub App client whose workflow dispatch is intentionally fire-and-forget."""

    def dispatch_relay(
        self,
        *,
        repository: str,
        workflow: str,
        ref: str,
        inputs: Mapping[str, str],
        token: str,
    ) -> None:
        repository = _safe_repository(repository)
        workflow_path = urllib.parse.quote(workflow, safe="")
        self._request(
            "POST",
            f"/repos/{repository}/actions/workflows/{workflow_path}/dispatches",
            token=token,
            body={"ref": ref, "inputs": dict(inputs)},
            expected=(204,),
        )


class ThinCiRelay:
    """Authenticate one Agent State INSERT, claim it once, and dispatch Central."""

    def __init__(
        self,
        config: RelayConfig,
        *,
        agent_state: AgentStateClient | None = None,
        dispatch_github: RelayGitHubClient | None = None,
    ) -> None:
        self.config = config
        self.agent_state = agent_state or AgentStateClient(
            config.agent_state_url,
            config.agent_state_secret_key,
        )
        self.dispatch_github = dispatch_github or RelayGitHubClient(
            config.dispatch_app_id,
            config.dispatch_app_private_key,
        )

    @staticmethod
    def _run_from_result(value: Mapping[str, object]) -> dict[str, Any]:
        run = value.get("run")
        _require(
            value.get("ok") is True and isinstance(run, dict),
            "agent_state_ci_rejected",
            409,
        )
        return run

    def _cancel(self, ci_run_id: str, code: str) -> None:
        try:
            self.agent_state.transition(
                ci_run_id,
                {"status": "cancelled", "error_summary": code[:128]},
            )
        except BrokerError:
            pass

    def _dispatch(self, request: RelayRequest) -> None:
        token = self.dispatch_github.repository_token(CENTRAL_REPOSITORY)
        self.dispatch_github.dispatch_relay(
            repository=CENTRAL_REPOSITORY,
            workflow=CENTRAL_WORKFLOW,
            ref=CENTRAL_REF,
            inputs=request.workflow_inputs(),
            token=token,
        )

    def handle_agent_state_webhook(
        self,
        raw: bytes,
        headers: Mapping[str, str],
    ) -> dict[str, object]:
        supplied = _header(headers, "X-StreamScape-Webhook-Secret")
        _require(
            bool(supplied)
            and hmac.compare_digest(
                supplied.encode("utf-8"),
                self.config.agent_state_webhook_secret.encode("utf-8"),
            ),
            "agent_state_webhook_unauthorized",
            401,
        )
        payload = _json_object(raw)
        _require(
            payload.get("type") == "INSERT",
            "agent_state_webhook_ignored",
            202,
        )
        _require(
            payload.get("schema") == "agent_private"
            and payload.get("table") == "ci_runs",
            "agent_state_webhook_ignored",
            202,
        )
        record = payload.get("record")
        _require(isinstance(record, dict), "agent_state_webhook_invalid")
        if record.get("origin") != "agent_request" or record.get("status") != "requested":
            return {"ok": True, "ignored": True}

        ci_run_id = _uuid(record.get("ci_run_id"))
        claim = self.agent_state.claim(ci_run_id)
        run = self._run_from_result(claim)
        if claim.get("replayed") is True and run.get("status") != "accepted":
            return {"ok": True, "replayed": True}

        try:
            request = RelayRequest.from_claimed_run(run)
            self._dispatch(request)
        except BrokerError as error:
            self._cancel(ci_run_id, error.code)
            raise
        return {
            "ok": True,
            "dispatched": True,
            "recovered": claim.get("replayed") is True,
        }


__all__ = (
    "RelayConfig",
    "RelayGitHubClient",
    "RelayRequest",
    "ThinCiRelay",
    "active_identity_key",
)
