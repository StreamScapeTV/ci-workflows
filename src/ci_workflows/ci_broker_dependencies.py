"""Generic exact private-dependency adaptation for broker-admitted Apple CI."""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import hmac
import json
import os
from pathlib import Path, PurePosixPath
import re
import time
from typing import Any, Mapping, TextIO
import urllib.request

from .ci_broker import (
    BrokerConfig,
    BrokerError,
    BrokerServer,
    CENTRAL_REPOSITORY,
    CiBroker,
    ProductProfile as BaseProductProfile,
    TOKEN_TTL_SECONDS,
    _SKIP_MARKERS,
    _SUPPORTED_CAPABILITIES,
    _header,
    _require,
    _safe_product_scalar,
    _safe_profile,
    _safe_project,
    _safe_ref,
    _safe_repository,
    _safe_sha,
    _safe_workflow_key,
    _safe_workspace,
    self_check as base_self_check,
)
from .ci_broker_action import (
    BrokerActionError,
    _broker_post,
    _checkout_source,
    _finish,
    _positive_int,
    _r2_environment,
    _remove_bounded,
    _require as action_require,
    _shared_apple_environment,
    _workspace_root,
    _write_state,
)
from .ciw_apple import execute_apple_validate
from .ciw_types import CIWContext
from .dependencies import DependencyResult, checkout_private_dependency
from .foundation_types import FoundationError
from .r2_diagnostics import R2DiagnosticError, upload_private_diagnostic
from .workspace import WorkspaceContext, cleanup_workspace, prepare_workspace

_DEPENDENCY_ID = re.compile(r"[a-z][a-z0-9-]{1,31}\Z")


def _safe_dependency_id(value: object) -> str:
    _require(
        isinstance(value, str) and _DEPENDENCY_ID.fullmatch(value) is not None,
        "invalid_dependency_id",
        422,
    )
    return value


def _safe_dependency_subdirectory(value: object) -> str:
    _require(isinstance(value, str), "invalid_dependency_subdirectory", 422)
    text = value.strip()
    _require(
        bool(text)
        and len(text.encode("utf-8")) <= 1024
        and "\\" not in text
        and "\x00" not in text
        and "\r" not in text
        and "\n" not in text,
        "invalid_dependency_subdirectory",
        422,
    )
    if text == ".":
        return text
    path = PurePosixPath(text)
    _require(
        not path.is_absolute()
        and ".." not in path.parts
        and all(part not in {"", "."} for part in path.parts),
        "invalid_dependency_subdirectory",
        422,
    )
    return path.as_posix()


@dataclass(frozen=True)
class BrokerPrivateDependency:
    repository: str
    sha: str
    subdirectory: str
    dependency_id: str

    @classmethod
    def parse(cls, value: Mapping[str, object]) -> "BrokerPrivateDependency":
        _require(
            set(value) == {"repository", "sha", "subdirectory", "id"},
            "private_ci_dependency_invalid",
            422,
        )
        repository = _safe_repository(value.get("repository"))
        _require(
            repository.startswith("StreamScapeTV/"),
            "private_ci_dependency_repository_unsupported",
            422,
        )
        return cls(
            repository=repository,
            sha=_safe_sha(value.get("sha")),
            subdirectory=_safe_dependency_subdirectory(value.get("subdirectory")),
            dependency_id=_safe_dependency_id(value.get("id")),
        )

    def as_payload(self) -> dict[str, str]:
        return {
            "repository": self.repository,
            "sha": self.sha,
            "subdirectory": self.subdirectory,
            "id": self.dependency_id,
        }


@dataclass(frozen=True)
class BrokerProductProfile(BaseProductProfile):
    private_dependency: BrokerPrivateDependency | None = None

    def as_payload(self) -> dict[str, object]:
        result: dict[str, object] = dict(super().as_payload())
        if self.private_dependency is not None:
            result["private_dependency"] = self.private_dependency.as_payload()
        return result


@dataclass(frozen=True)
class BrokerProductConfig:
    project_key: str
    profiles: dict[str, BrokerProductProfile]
    automatic: dict[str, str]

    @classmethod
    def parse(cls, value: Mapping[str, object]) -> "BrokerProductConfig":
        allowed = {"schema_version", "project_key", "profiles", "automatic"}
        _require(set(value).issubset(allowed), "private_ci_config_unsupported", 422)
        _require(value.get("schema_version") == 1, "private_ci_config_version", 422)
        project_key = _safe_project(value.get("project_key"))
        raw_profiles = value.get("profiles")
        _require(
            isinstance(raw_profiles, dict) and 1 <= len(raw_profiles) <= 16,
            "private_ci_profiles_invalid",
            422,
        )
        required_profile = {
            "workflow_key",
            "capability",
            "workspace",
            "scheme",
            "test_target",
        }
        allowed_profile = required_profile | {"private_dependency"}
        profiles: dict[str, BrokerProductProfile] = {}
        for raw_name, raw_profile in raw_profiles.items():
            name = _safe_profile(raw_name)
            _require(isinstance(raw_profile, dict), "private_ci_profile_invalid", 422)
            fields = set(raw_profile)
            _require(
                required_profile.issubset(fields) and fields.issubset(allowed_profile),
                "private_ci_profile_invalid",
                422,
            )
            capability = raw_profile.get("capability")
            _require(
                capability in _SUPPORTED_CAPABILITIES,
                "private_ci_capability_unsupported",
                422,
            )
            raw_dependency = raw_profile.get("private_dependency")
            dependency = None
            if raw_dependency is not None:
                _require(
                    isinstance(raw_dependency, dict),
                    "private_ci_dependency_invalid",
                    422,
                )
                dependency = BrokerPrivateDependency.parse(raw_dependency)
            profiles[name] = BrokerProductProfile(
                name=name,
                workflow_key=_safe_workflow_key(raw_profile.get("workflow_key")),
                capability=str(capability),
                workspace=_safe_workspace(raw_profile.get("workspace")),
                scheme=_safe_product_scalar(raw_profile.get("scheme"), "invalid_scheme"),
                test_target=_safe_product_scalar(
                    raw_profile.get("test_target"),
                    "invalid_test_target",
                ),
                private_dependency=dependency,
            )
        raw_automatic = value.get("automatic", {})
        _require(isinstance(raw_automatic, dict), "private_ci_automatic_invalid", 422)
        _require(
            set(raw_automatic).issubset({"push", "tag"}),
            "private_ci_automatic_invalid",
            422,
        )
        automatic: dict[str, str] = {}
        for event, profile_name in raw_automatic.items():
            profile_name = _safe_profile(profile_name)
            _require(
                profile_name in profiles,
                "private_ci_automatic_profile_missing",
                422,
            )
            automatic[event] = profile_name
        return cls(project_key=project_key, profiles=profiles, automatic=automatic)

    def profile(
        self,
        name: str,
        workflow_key: str | None = None,
    ) -> BrokerProductProfile:
        name = _safe_profile(name)
        _require(name in self.profiles, "private_ci_profile_missing", 422)
        profile = self.profiles[name]
        if workflow_key is not None:
            _require(
                profile.workflow_key == _safe_workflow_key(workflow_key),
                "workflow_profile_mismatch",
                422,
            )
        return profile


def _profile_from_payload(value: object) -> BrokerProductProfile:
    _require(isinstance(value, dict), "invalid_dispatch_profile")
    required = {
        "name",
        "workflow_key",
        "capability",
        "workspace",
        "scheme",
        "test_target",
    }
    fields = set(value)
    _require(
        required.issubset(fields) and fields.issubset(required | {"private_dependency"}),
        "invalid_dispatch_profile",
    )
    capability = value.get("capability")
    _require(capability in _SUPPORTED_CAPABILITIES, "unsupported_capability", 422)
    dependency = None
    raw_dependency = value.get("private_dependency")
    if raw_dependency is not None:
        _require(isinstance(raw_dependency, dict), "invalid_dispatch_profile")
        dependency = BrokerPrivateDependency.parse(raw_dependency)
    return BrokerProductProfile(
        name=_safe_profile(value.get("name")),
        workflow_key=_safe_workflow_key(value.get("workflow_key")),
        capability=str(capability),
        workspace=_safe_workspace(value.get("workspace")),
        scheme=_safe_product_scalar(value.get("scheme"), "invalid_scheme"),
        test_target=_safe_product_scalar(value.get("test_target"), "invalid_test_target"),
        private_dependency=dependency,
    )


class DependencyCiBroker(CiBroker):
    """Broker variant adding one bounded exact private dependency per profile."""

    def _source_and_profile(
        self,
        *,
        project_key: str,
        repository: str,
        ref: str,
        profile_name: str,
        workflow_key: str,
        requested_sha: str | None,
        installation_id: int | None = None,
    ) -> tuple[str, BrokerProductProfile]:
        source_token = (
            self.source_github.installation_token(installation_id)
            if installation_id is not None
            else self.source_github.repository_token(repository)
        )
        commit = self.source_github.get_commit(
            repository,
            requested_sha or ref,
            source_token,
        )
        sha = _safe_sha(commit.get("sha"))
        if requested_sha:
            _require(
                sha == _safe_sha(requested_sha),
                "requested_source_mismatch",
                409,
            )
        config = BrokerProductConfig.parse(
            self.source_github.get_private_config(repository, sha, source_token)
        )
        _require(
            config.project_key == _safe_project(project_key),
            "project_config_mismatch",
            409,
        )
        return sha, config.profile(profile_name, workflow_key)

    def handle_github_webhook(
        self,
        raw: bytes,
        headers: Mapping[str, str],
    ) -> dict[str, object]:
        signature = _header(headers, "X-Hub-Signature-256")
        expected = "sha256=" + hmac.new(
            self.config.source_webhook_secret.encode("utf-8"),
            raw,
            hashlib.sha256,
        ).hexdigest()
        _require(
            signature and hmac.compare_digest(signature, expected),
            "github_webhook_unauthorized",
            401,
        )
        event = _header(headers, "X-GitHub-Event")
        if event != "push":
            return {"ok": True, "ignored": True}
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise BrokerError("invalid_json") from None
        _require(isinstance(payload, dict), "invalid_json")
        if payload.get("deleted") is True:
            return {"ok": True, "ignored": True}
        repository_value = payload.get("repository")
        installation = payload.get("installation")
        _require(
            isinstance(repository_value, dict) and isinstance(installation, dict),
            "github_webhook_invalid",
        )
        repository = _safe_repository(repository_value.get("full_name"))
        ref = _safe_ref(payload.get("ref"))
        source_sha = _safe_sha(payload.get("after"))
        _require(source_sha != "0" * 40, "github_webhook_ignored", 202)
        installation_id = installation.get("id")
        _require(
            isinstance(installation_id, int) and installation_id > 0,
            "github_webhook_invalid",
        )
        source_token = self.source_github.installation_token(installation_id)
        config = BrokerProductConfig.parse(
            self.source_github.get_private_config(repository, source_sha, source_token)
        )
        if ref.startswith("refs/tags/"):
            trigger_kind = "tag"
        elif ref.startswith("refs/heads/"):
            trigger_kind = "push"
        else:
            return {"ok": True, "ignored": True}
        profile_name = config.automatic.get(trigger_kind)
        if not profile_name:
            return {"ok": True, "ignored": True}
        head_commit = payload.get("head_commit")
        message = head_commit.get("message", "") if isinstance(head_commit, dict) else ""
        if not message:
            commit = self.source_github.get_commit(repository, source_sha, source_token)
            nested = commit.get("commit")
            message = nested.get("message", "") if isinstance(nested, dict) else ""
        if isinstance(message, str) and any(
            marker in message.lower() for marker in _SKIP_MARKERS
        ):
            return {"ok": True, "ignored": True, "skip_ci": True}
        profile = config.profile(profile_name)
        dispatch_id, token = self._dispatch_payload(
            kind="workflow_registration",
            project_key=config.project_key,
            repository=repository,
            ref=ref,
            source_sha=source_sha,
            profile=profile,
            trigger_kind=trigger_kind,
        )
        self._dispatch_central(dispatch_id, token)
        return {"ok": True, "dispatched": True}

    def action_start(
        self,
        raw: bytes,
        headers: Mapping[str, str],
    ) -> dict[str, object]:
        _claims, _request, envelope = self._action_identity(raw, headers)
        profile = _profile_from_payload(envelope.get("profile"))
        dependency = profile.private_dependency
        dependency_token = ""
        if dependency is not None:
            dependency_token = self.source_github.repository_token(dependency.repository)
            observed = self.source_github.get_commit(
                dependency.repository,
                dependency.sha,
                dependency_token,
            )
            _require(
                _safe_sha(observed.get("sha")) == dependency.sha,
                "private_dependency_source_mismatch",
                409,
            )
        result = super().action_start(raw, headers)
        if dependency is None:
            result["private_dependency"] = None
        else:
            result["private_dependency"] = {
                **dependency.as_payload(),
                "token": dependency_token,
            }
        return result


@dataclass(frozen=True)
class ActionPrivateDependency:
    repository: str
    sha: str
    subdirectory: str
    dependency_id: str
    token: str

    @classmethod
    def parse(cls, value: object) -> "ActionPrivateDependency | None":
        if value is None:
            return None
        action_require(isinstance(value, dict), "start_response_invalid")
        action_require(
            set(value) == {"repository", "sha", "subdirectory", "id", "token"},
            "start_response_invalid",
        )
        try:
            dependency = BrokerPrivateDependency.parse(value)
        except BrokerError:
            raise BrokerActionError("start_response_invalid") from None
        token = value.get("token")
        action_require(isinstance(token, str) and bool(token), "start_response_invalid")
        return cls(
            repository=dependency.repository,
            sha=dependency.sha,
            subdirectory=dependency.subdirectory,
            dependency_id=dependency.dependency_id,
            token=token,
        )


def _dependency_environment(
    *,
    dependency: ActionPrivateDependency,
    checkout: DependencyResult,
    state_root: Path,
) -> dict[str, str]:
    target = state_root / checkout.relative_path
    outputs = checkout.output_values()
    return {
        "INPUT_PRIVATE_DEPENDENCY_REPOSITORY": dependency.repository,
        "INPUT_PRIVATE_DEPENDENCY_SHA": dependency.sha,
        "INPUT_PRIVATE_DEPENDENCY_SUBDIRECTORY": dependency.subdirectory,
        "INPUT_PRIVATE_DEPENDENCY_ID": dependency.dependency_id,
        "INPUT_PRIVATE_DEPENDENCY_VERIFIED": outputs["verified"],
        "INPUT_PRIVATE_DEPENDENCY_REMOTES_ERASED": outputs["remotes_erased"],
        "INPUT_PRIVATE_DEPENDENCY_CREDENTIALS_ERASED": outputs["credentials_erased"],
        "INPUT_PRIVATE_DEPENDENCY_HEAD_SHA": outputs["head_sha"],
        "INPUT_PRIVATE_DEPENDENCY_CHECKOUT_REPOSITORY": outputs["repository"],
        "INPUT_PRIVATE_DEPENDENCY_CHECKOUT_ID": outputs["dependency_id"],
        "INPUT_PRIVATE_DEPENDENCY_EXPECTED_SUBPATH": outputs["expected_subpath"],
        "CI_PRIVATE_DEPENDENCY_PATH": str(target),
    }


def _checkout_dependency(
    *,
    dependency: ActionPrivateDependency,
    state_root: Path,
    contract_root: Path,
    diagnostic: TextIO,
) -> dict[str, str]:
    try:
        checkout = checkout_private_dependency(
            state_root=state_root,
            repository=dependency.repository,
            admitted_sha=dependency.sha,
            dependency_id=dependency.dependency_id,
            expected_subpath=dependency.subdirectory,
            fetch_depth=1,
            token=dependency.token,
            contract_root=contract_root,
        )
    except FoundationError as error:
        diagnostic.write(
            f"Central private dependency checkout failed: {error.instruction}.\n"
        )
        raise BrokerActionError("private_dependency_checkout_failed") from None
    return _dependency_environment(
        dependency=dependency,
        checkout=checkout,
        state_root=state_root,
    )


def _execute_shared_apple_validation(
    *,
    repository: str,
    source_sha: str,
    source_token: str,
    workspace: str,
    scheme: str,
    test_target: str,
    private_dependency: ActionPrivateDependency | None,
    run_id: int,
    run_attempt: int,
    workspace_root: Path,
    runner_temp: Path,
    diagnostic: TextIO,
    environment: Mapping[str, str],
) -> bool:
    runner_os = environment.get("RUNNER_OS", "")
    job = environment.get("GITHUB_JOB", "")
    action_require(runner_os == "macOS", "runner_os_invalid")
    action_require(bool(job), "github_job_invalid")

    execution_environment = _shared_apple_environment(
        repository=repository,
        source_sha=source_sha,
        source_token=source_token,
        workspace=workspace,
        scheme=scheme,
        test_target=test_target,
        environment=environment,
    )
    workspace_state = None
    validation_ok = False
    cleanup_ok = True
    try:
        workspace_state = prepare_workspace(
            WorkspaceContext(
                workspace=workspace_root,
                runner_temp=runner_temp,
                repository=repository,
                run_id=str(run_id),
                run_attempt=run_attempt,
                job=job,
                runner_os=runner_os,
            ),
            profile="apple",
            cache_mode="disabled",
            source_sha=source_sha,
            trust_mode="trusted-exact",
            contract_root=workspace_root,
        )
        execution_environment.update(workspace_state.environment)
        if private_dependency is not None:
            execution_environment.update(
                _checkout_dependency(
                    dependency=private_dependency,
                    state_root=workspace_state.root,
                    contract_root=workspace_root,
                    diagnostic=diagnostic,
                )
            )
        context = CIWContext(
            root=workspace_root,
            environment=execution_environment,
            stdout=diagnostic,
            stderr=diagnostic,
        )
        result = execute_apple_validate(
            argparse.Namespace(phase="execute", source_root="source"),
            context,
        )
        validation_ok = result.outputs.get("result") == "success"
    except Exception:
        diagnostic.write("Central shared Apple validation failed.\n")
    finally:
        if workspace_state is not None:
            context = CIWContext(
                root=workspace_root,
                environment=execution_environment,
                stdout=diagnostic,
                stderr=diagnostic,
            )
            try:
                execute_apple_validate(
                    argparse.Namespace(phase="cleanup", source_root="source"),
                    context,
                )
                execute_apple_validate(
                    argparse.Namespace(phase="residue", source_root="source"),
                    context,
                )
            except Exception:
                cleanup_ok = False
                diagnostic.write("Central shared Apple cleanup failed.\n")
            try:
                cleanup_workspace(
                    workspace_state.root,
                    expected_state_id=workspace_state.state_id,
                    contract_root=workspace_root,
                )
            except Exception:
                cleanup_ok = False
                diagnostic.write("Central shared workspace cleanup failed.\n")
    return validation_ok and cleanup_ok


def execute_apple_host(
    environment: Mapping[str, str] = os.environ,
    opener: Any = urllib.request.urlopen,
) -> None:
    dispatch_id = environment.get("CI_DISPATCH_ID", "")
    dispatch_token = environment.get("CI_DISPATCH_TOKEN", "")
    action_require(
        20 <= len(dispatch_id) <= 128 and bool(dispatch_token),
        "dispatch_environment_missing",
    )
    run_id = _positive_int(environment, "GITHUB_RUN_ID", 9223372036854775807)
    run_attempt = _positive_int(environment, "GITHUB_RUN_ATTEMPT", 1000)
    start = _broker_post(
        "/actions/start",
        {
            "dispatch_id": dispatch_id,
            "dispatch_token": dispatch_token,
            "run_attempt": run_attempt,
        },
        environment,
        opener,
    )
    action_require(
        start.get("capability") == "apple-host-test",
        "capability_rejected",
    )
    ci_run_id = start.get("ci_run_id")
    repository = start.get("repository")
    source_sha = start.get("source_sha")
    source_token = start.get("source_token")
    workspace_name = start.get("workspace")
    scheme = start.get("scheme")
    test_target = start.get("test_target")
    private_dependency = ActionPrivateDependency.parse(start.get("private_dependency"))
    action_require(
        isinstance(ci_run_id, str) and len(ci_run_id) == 36,
        "start_response_invalid",
    )
    action_require(
        isinstance(repository, str) and 3 <= len(repository) <= 256,
        "start_response_invalid",
    )
    action_require(
        isinstance(source_sha, str) and len(source_sha) == 40,
        "start_response_invalid",
    )
    action_require(
        isinstance(source_token, str) and bool(source_token),
        "start_response_invalid",
    )
    action_require(
        isinstance(workspace_name, str) and workspace_name.endswith(".xcworkspace"),
        "start_response_invalid",
    )
    action_require(isinstance(scheme, str) and bool(scheme), "start_response_invalid")
    action_require(
        isinstance(test_target, str) and bool(test_target),
        "start_response_invalid",
    )
    _write_state(environment, ci_run_id)

    workspace_root = _workspace_root(environment)
    runner_temp = Path(environment.get("RUNNER_TEMP", "")).resolve()
    source = workspace_root / "source"
    diagnostic_path = runner_temp / "central-ci-diagnostic.log"
    _remove_bounded(diagnostic_path, runner_temp)

    validation_ok = False
    try:
        diagnostic_path.touch(mode=0o600, exist_ok=False)
        with diagnostic_path.open("ab", buffering=0) as diagnostic:
            _checkout_source(
                repository=repository,
                sha=source_sha,
                token=source_token,
                source=source,
                diagnostic=diagnostic,
                environment=environment,
            )
        with diagnostic_path.open("a", encoding="utf-8") as diagnostic:
            validation_ok = _execute_shared_apple_validation(
                repository=repository,
                source_sha=source_sha,
                source_token=source_token,
                workspace=workspace_name,
                scheme=scheme,
                test_target=test_target,
                private_dependency=private_dependency,
                run_id=run_id,
                run_attempt=run_attempt,
                workspace_root=workspace_root,
                runner_temp=runner_temp,
                diagnostic=diagnostic,
                environment=environment,
            )
    except BrokerActionError:
        validation_ok = False

    logs_status = "failed"
    object_key: str | None = None
    digest: str | None = None
    upload_error = False
    try:
        account_id, bucket, access_key, secret_key = _r2_environment(environment)
        result = upload_private_diagnostic(
            diagnostic_path=diagnostic_path,
            request_id=ci_run_id,
            run_id=run_id,
            attempt=run_attempt,
            account_id=account_id,
            bucket=bucket,
            access_key_id=access_key,
            secret_access_key=secret_key,
        )
        logs_status = "uploaded"
        object_key = result.object_key
        digest = result.sha256
    except (R2DiagnosticError, BrokerActionError):
        upload_error = True

    if validation_ok and not upload_error:
        status = "succeeded"
        error = None
    elif not validation_ok:
        status = "failed"
        error = "apple_host_validation_failed"
    else:
        status = "failed"
        error = "diagnostic_upload_failed"
    try:
        _finish(
            environment=environment,
            ci_run_id=ci_run_id,
            status=status,
            error_summary=error,
            logs_status=logs_status,
            logs_object_key=object_key,
            logs_sha256=digest,
            opener=opener,
        )
    except BrokerActionError:
        raise BrokerActionError("terminal_callback_failed") from None
    if status != "succeeded":
        raise BrokerActionError(error or "central_validation_failed")


def serve(config: BrokerConfig | None = None) -> None:
    selected = config or BrokerConfig.from_environment()
    broker = DependencyCiBroker(selected)
    server = BrokerServer(("0.0.0.0", selected.port), broker)
    server.serve_forever(poll_interval=0.5)


def self_check() -> dict[str, object]:
    base_self_check()
    parsed = BrokerProductConfig.parse(
        {
            "schema_version": 1,
            "project_key": "example",
            "profiles": {
                "host": {
                    "workflow_key": "validation.apple",
                    "capability": "apple-host-test",
                    "workspace": "Sample.xcworkspace",
                    "scheme": "Sample",
                    "test_target": "SampleTests/SelectedIntegrationTests",
                    "private_dependency": {
                        "repository": "StreamScapeTV/example-media",
                        "sha": "b" * 40,
                        "subdirectory": ".",
                        "id": "example-media",
                    },
                }
            },
            "automatic": {"push": "host"},
        }
    )
    profile = parsed.profile("host", "validation.apple")
    _require(
        profile.private_dependency is not None
        and profile.private_dependency.sha == "b" * 40,
        "self_check_failed",
        500,
    )
    return {"ok": True}


__all__ = (
    "ActionPrivateDependency",
    "BrokerPrivateDependency",
    "BrokerProductConfig",
    "BrokerProductProfile",
    "DependencyCiBroker",
    "execute_apple_host",
    "self_check",
    "serve",
)
