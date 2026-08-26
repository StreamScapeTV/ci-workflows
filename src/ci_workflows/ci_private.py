"""Product-neutral opaque private Central CI execution.

The public workflow passes only an opaque CI UUID. This module privately reclaims
the canonical Agent State request, resolves the human ref, checks out the exact
source through the shared source provider, resolves bounded product configuration,
and delegates to the existing technology validators. Detailed output stays
runner-local and terminal state is written only after private R2 upload/read-back
verification.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
from typing import Any, Mapping, Sequence, TextIO

from .central_profile import CentralProfileResolution, resolve_profile
from .ci_lifecycle import AgentStateCiClient, RelayRequest, WorkflowIdentity
from .ciw_android import execute_android_validate
from .ciw_apple import execute_apple_validate
from .ciw_python import execute_python_validate
from .ciw_types import CIWContext
from .dependencies import DependencyResult, checkout_private_dependency
from .github_app_token import GitHubAppRepositoryTokenClient
from .private_release_asset import (
    PrivateReleaseAssetResult,
    cleanup_private_release_asset,
    materialize_private_release_asset,
)
from .r2_diagnostics import (
    R2DiagnosticError,
    R2DiagnosticResult,
    upload_private_diagnostic,
)
from .source_admission import _resolve_tag
from .source_checkout import exact_checkout
from .source_github import GitHubSourceProvider
from .workspace import WorkspaceContext, cleanup_workspace, prepare_workspace

_STATE_DIRECTORY = "central-private-ci"
_STATE_FILE = "state.json"
_LOG_FILE = "private.log"
_SHA = re.compile(r"[0-9a-f]{40}\Z")
_RUNNER_FAMILY = {
    "validation.apple": "macos",
    "validation.android": "linux",
    "validation.python": "linux",
}


class PrivateCiError(RuntimeError):
    """Stable public-safe failure for product-neutral opaque execution."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise PrivateCiError(code)


def _required(environment: Mapping[str, str], name: str) -> str:
    value = environment.get(name, "")
    _require(bool(value), f"missing_{name.lower()}")
    return value


def _positive_int(environment: Mapping[str, str], name: str, maximum: int) -> int:
    value = environment.get(name, "")
    _require(value.isdigit(), f"invalid_{name.lower()}")
    parsed = int(value)
    _require(1 <= parsed <= maximum, f"invalid_{name.lower()}")
    return parsed


def _runner_temp(environment: Mapping[str, str]) -> Path:
    value = Path(_required(environment, "RUNNER_TEMP")).resolve()
    _require(
        value.is_absolute() and value.is_dir() and not value.is_symlink(),
        "invalid_runner_temp",
    )
    return value


def _workspace(environment: Mapping[str, str]) -> Path:
    value = Path(_required(environment, "GITHUB_WORKSPACE")).resolve()
    _require(
        value.is_absolute() and value.is_dir() and not value.is_symlink(),
        "invalid_workspace",
    )
    return value


def _state_root(environment: Mapping[str, str], ci_run_id: str) -> Path:
    runner_temp = _runner_temp(environment)
    parent = (runner_temp / _STATE_DIRECTORY).resolve()
    root = (parent / ci_run_id).resolve()
    _require(root.parent == parent, "invalid_private_state")
    parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    root.mkdir(mode=0o700, exist_ok=True)
    _require(root.is_dir() and not root.is_symlink(), "invalid_private_state")
    return root


def _state_path(environment: Mapping[str, str], ci_run_id: str) -> Path:
    return _state_root(environment, ci_run_id) / _STATE_FILE


def _log_path(environment: Mapping[str, str], ci_run_id: str) -> Path:
    return _state_root(environment, ci_run_id) / _LOG_FILE


def _write_state(
    environment: Mapping[str, str],
    ci_run_id: str,
    value: Mapping[str, object],
) -> None:
    path = _state_path(environment, ci_run_id)
    temporary = path.with_suffix(".tmp")
    raw = json.dumps(dict(value), sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    try:
        temporary.write_text(raw, encoding="utf-8")
        temporary.chmod(0o600)
        temporary.replace(path)
        path.chmod(0o600)
    except OSError:
        raise PrivateCiError("private_state_unavailable") from None


def _read_state(
    environment: Mapping[str, str],
    ci_run_id: str,
) -> dict[str, Any] | None:
    path = _state_path(environment, ci_run_id)
    if not path.exists():
        return None
    _require(path.is_file() and not path.is_symlink(), "invalid_private_state")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise PrivateCiError("invalid_private_state") from None
    _require(isinstance(value, dict), "invalid_private_state")
    return value


def _ensure_log(environment: Mapping[str, str], ci_run_id: str) -> Path:
    path = _log_path(environment, ci_run_id)
    try:
        if not path.exists():
            path.touch(mode=0o600, exist_ok=False)
        _require(path.is_file() and not path.is_symlink(), "invalid_private_log")
        path.chmod(0o600)
    except OSError:
        raise PrivateCiError("invalid_private_log") from None
    return path


def _append(log: TextIO, message: str) -> None:
    log.write(message.rstrip("\n") + "\n")
    log.flush()


def _stable_code(error: BaseException) -> str:
    code = getattr(error, "code", None)
    if isinstance(code, str) and re.fullmatch(r"[a-z][a-z0-9_-]{1,127}", code):
        return code
    return "private_ci_internal_error"


def _remove_path(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink(missing_ok=True)
    elif path.exists():
        shutil.rmtree(path)


def _append_state_logs(root: Path, log: TextIO) -> None:
    if not root.exists() or root.is_symlink():
        return
    candidates = sorted(
        path for path in root.rglob("*.log") if path.is_file() and not path.is_symlink()
    )
    for path in candidates:
        try:
            relative = path.relative_to(root)
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        _append(log, f"[captured-log] {relative.as_posix()}")
        if text:
            log.write(text)
            if not text.endswith("\n"):
                log.write("\n")
            log.flush()


def _claim_request(client: AgentStateCiClient, ci_run_id: str) -> RelayRequest:
    result = client._rpc("claim_ci_run", {"p_ci_run_id": ci_run_id})
    return RelayRequest.from_claimed_run(client._run(result))


def _start_lifecycle(
    client: AgentStateCiClient,
    request: RelayRequest,
    environment: Mapping[str, str],
) -> None:
    identity = WorkflowIdentity.from_values(
        project_key=request.project_key,
        repository=request.repository,
        ref=request.ref,
        is_tag=request.is_tag,
        workflow_key=request.workflow_key,
        profile=request.profile,
        environment=environment,
    )
    client.start(identity, request.ci_run_id)


def _source_token_client(environment: Mapping[str, str]) -> GitHubAppRepositoryTokenClient:
    return GitHubAppRepositoryTokenClient(
        _required(environment, "SOURCE_APP_ID"),
        _required(environment, "SOURCE_APP_PRIVATE_KEY"),
    )


def _resolve_source_sha(request: RelayRequest, token: str) -> str:
    provider = GitHubSourceProvider(token)
    if request.is_tag:
        _tag_object, source_sha = _resolve_tag(provider, request.repository, request.ref)
    else:
        source_sha = provider.branch_sha(request.repository, request.ref)
    _require(_SHA.fullmatch(source_sha) is not None, "invalid_observed_sha")
    provider.commit(request.repository, source_sha)
    return source_sha


def _checkout_source(
    *,
    repository: str,
    source_sha: str,
    token: str,
    workspace: Path,
    log: TextIO,
) -> Path:
    source = workspace / "source"
    _remove_path(source)
    _append(log, "[source] checkout started")
    result = exact_checkout(
        repository=repository,
        admitted_sha=source_sha,
        path="source",
        fetch_depth=1,
        token=token,
        workspace=workspace,
    )
    _require(result.get("head_sha") == source_sha, "source_sha_mismatch")
    _require(result.get("verified") == "true", "source_checkout_failed")
    _append(log, "[source] exact checkout completed")
    return source


def _dependency_receipts(
    *,
    resolution: CentralProfileResolution,
    token_client: GitHubAppRepositoryTokenClient,
    workspace_state_root: Path,
    contract_root: Path,
    log: TextIO,
) -> DependencyResult | None:
    if not resolution.private_dependency_repository:
        return None
    _append(log, "[dependency] exact private dependency checkout started")
    token = token_client.repository_contents_read_token(
        resolution.private_dependency_repository
    )
    result = checkout_private_dependency(
        state_root=workspace_state_root,
        repository=resolution.private_dependency_repository,
        admitted_sha=resolution.private_dependency_sha,
        dependency_id=resolution.private_dependency_id,
        expected_subpath=resolution.private_dependency_subdirectory,
        fetch_depth=1,
        token=token,
        contract_root=contract_root,
    )
    _append(log, "[dependency] exact private dependency checkout completed")
    return result


def _execution_environment(
    *,
    request: RelayRequest,
    source_sha: str,
    resolution: CentralProfileResolution,
    dependency: DependencyResult | None,
    base: Mapping[str, str],
    workspace_environment: Mapping[str, str],
) -> dict[str, str]:
    result = dict(base)
    for command_file in ("GITHUB_OUTPUT", "GITHUB_ENV", "GITHUB_STEP_SUMMARY"):
        result.pop(command_file, None)
    result.update(workspace_environment)
    result.update(
        GITHUB_REPOSITORY=request.repository,
        INPUT_ADMITTED_SHA=source_sha,
        INPUT_SOURCE_REPOSITORY=request.repository,
        INPUT_VALIDATION_SCOPE=resolution.validation_scope,
        INPUT_VALIDATION_PLAN_JSON=resolution.validation_plan_json,
        INPUT_SOURCE_TRUST="trusted-exact",
        INPUT_PRIVATE_DEPENDENCY_REPOSITORY=resolution.private_dependency_repository,
        INPUT_PRIVATE_DEPENDENCY_SHA=resolution.private_dependency_sha,
        INPUT_PRIVATE_DEPENDENCY_SUBDIRECTORY=resolution.private_dependency_subdirectory,
        INPUT_PRIVATE_DEPENDENCY_ID=resolution.private_dependency_id,
    )
    if dependency is not None:
        result.update(
            INPUT_PRIVATE_DEPENDENCY_VERIFIED="true",
            INPUT_PRIVATE_DEPENDENCY_REMOTES_ERASED=(
                "true" if dependency.remotes_erased else "false"
            ),
            INPUT_PRIVATE_DEPENDENCY_CREDENTIALS_ERASED=(
                "true" if dependency.credentials_erased else "false"
            ),
            INPUT_PRIVATE_DEPENDENCY_HEAD_SHA=dependency.head_sha,
            INPUT_PRIVATE_DEPENDENCY_CHECKOUT_REPOSITORY=dependency.repository,
            INPUT_PRIVATE_DEPENDENCY_CHECKOUT_ID=dependency.dependency_id,
            INPUT_PRIVATE_DEPENDENCY_EXPECTED_SUBPATH=dependency.expected_subpath,
        )
    return result


def _product_base_environment(base: Mapping[str, str]) -> dict[str, str]:
    """Remove opaque transport/action inputs before entering product validators."""
    return {key: value for key, value in base.items() if not key.startswith("INPUT_")}


def _canonical_environment(
    *,
    request: RelayRequest,
    source_sha: str,
    resolution: CentralProfileResolution,
    dependency: DependencyResult | None,
    base: Mapping[str, str],
    workspace_environment: Mapping[str, str],
) -> dict[str, str]:
    result = _execution_environment(
        request=request,
        source_sha=source_sha,
        resolution=resolution,
        dependency=dependency,
        base=_product_base_environment(base),
        workspace_environment=workspace_environment,
    )
    result["INPUT_EXECUTION_BACKEND"] = "github-hosted"
    for key, value in resolution.canonical_inputs().items():
        result[f"INPUT_{key.upper()}"] = value
    package_token = base.get("CIW_MAVEN_PACKAGE_READ_TOKEN", "")
    if package_token:
        result["CIW_MAVEN_PACKAGE_READ_TOKEN"] = package_token
    return result


def _runner_os(environment: Mapping[str, str]) -> str:
    value = environment.get("RUNNER_OS", "")
    _require(value in {"Linux", "macOS"}, "private_ci_runner_required")
    return value


def _workspace_state(
    *,
    request: RelayRequest,
    source_sha: str,
    environment: Mapping[str, str],
    profile: str,
):
    workspace = _workspace(environment)
    return prepare_workspace(
        WorkspaceContext(
            workspace=workspace,
            runner_temp=_runner_temp(environment),
            repository=request.repository,
            run_id=str(_positive_int(environment, "GITHUB_RUN_ID", 2**63 - 1)),
            run_attempt=_positive_int(environment, "GITHUB_RUN_ATTEMPT", 1000),
            job=_required(environment, "GITHUB_JOB"),
            runner_os=_runner_os(environment),
        ),
        profile=profile,
        cache_mode="disabled",
        source_sha=source_sha,
        trust_mode="trusted-exact",
        contract_root=workspace,
    )


def _family_for_request(request: RelayRequest) -> str:
    family = _RUNNER_FAMILY.get(request.workflow_key)
    _require(family is not None and request.profile == "host", "unsupported_ci_intent")
    return family


def _verify_family(
    resolution: CentralProfileResolution,
    environment: Mapping[str, str],
) -> None:
    expected = "macos" if _runner_os(environment) == "macOS" else "linux"
    _require(resolution.executor_family == expected, "private_ci_runner_mismatch")


def plan_private_ci(environment: Mapping[str, str] = os.environ) -> str:
    """Privately reclaim one request and expose only its coarse hosted family."""
    ci_run_id = _required(environment, "INPUT_CI_RUN_ID").lower()
    client = AgentStateCiClient.from_environment(environment)
    request = _claim_request(client, ci_run_id)
    family = _family_for_request(request)
    output = Path(_required(environment, "GITHUB_OUTPUT"))
    _require(output.is_absolute(), "invalid_github_output")
    try:
        with output.open("a", encoding="utf-8") as handle:
            handle.write(f"executor_family={family}\n")
    except OSError:
        raise PrivateCiError("github_output_unavailable") from None
    return family


def _execute_apple(
    *,
    request: RelayRequest,
    source_sha: str,
    resolution: CentralProfileResolution,
    token_client: GitHubAppRepositoryTokenClient,
    environment: Mapping[str, str],
    log: TextIO,
) -> tuple[bool, bool]:
    _require(_runner_os(environment) == "macOS", "private_apple_runner_required")
    workspace = _workspace(environment)
    state = _workspace_state(
        request=request,
        source_sha=source_sha,
        environment=environment,
        profile="apple",
    )
    validation_ok = False
    cleanup_ok = True
    execution_environment: dict[str, str] | None = None
    try:
        dependency = _dependency_receipts(
            resolution=resolution,
            token_client=token_client,
            workspace_state_root=state.root,
            contract_root=workspace,
            log=log,
        )
        apple_base = _product_base_environment(environment)
        for key, value in resolution.canonical_inputs().items():
            apple_base[f"INPUT_{key.upper()}"] = value
        execution_environment = _execution_environment(
            request=request,
            source_sha=source_sha,
            resolution=resolution,
            dependency=dependency,
            base=apple_base,
            workspace_environment=state.environment,
        )
        context = CIWContext(
            root=workspace,
            environment=execution_environment,
            stdout=log,
            stderr=log,
        )
        with contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
            result = execute_apple_validate(
                argparse.Namespace(phase="execute", source_root="source"),
                context,
            )
        validation_ok = result.outputs.get("result") == "success"
        _append(log, "[apple] validation completed")
    except BaseException as error:
        _append(log, f"[apple] validation error={_stable_code(error)}")
    finally:
        _append_state_logs(state.root, log)
        cleanup_environment = (
            execution_environment
            if execution_environment is not None
            else _product_base_environment(environment)
        )
        context = CIWContext(
            root=workspace,
            environment=cleanup_environment,
            stdout=log,
            stderr=log,
        )
        try:
            with contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
                execute_apple_validate(
                    argparse.Namespace(phase="cleanup", source_root="source"),
                    context,
                )
                execute_apple_validate(
                    argparse.Namespace(phase="residue", source_root="source"),
                    context,
                )
        except BaseException as error:
            cleanup_ok = False
            _append(log, f"[apple] cleanup error={_stable_code(error)}")
        try:
            cleanup_workspace(
                state.root,
                expected_state_id=state.state_id,
                contract_root=workspace,
            )
        except BaseException as error:
            cleanup_ok = False
            _append(log, f"[workspace] cleanup error={_stable_code(error)}")
    return validation_ok, cleanup_ok


def _execute_android(
    *,
    request: RelayRequest,
    source_sha: str,
    resolution: CentralProfileResolution,
    token_client: GitHubAppRepositoryTokenClient,
    environment: Mapping[str, str],
    log: TextIO,
) -> tuple[bool, bool]:
    _require(_runner_os(environment) == "Linux", "private_linux_runner_required")
    workspace = _workspace(environment)
    state = _workspace_state(
        request=request,
        source_sha=source_sha,
        environment=environment,
        profile="gradle",
    )
    validation_ok = False
    cleanup_ok = True
    execution_environment: dict[str, str] | None = None
    try:
        dependency = _dependency_receipts(
            resolution=resolution,
            token_client=token_client,
            workspace_state_root=state.root,
            contract_root=workspace,
            log=log,
        )
        execution_environment = _canonical_environment(
            request=request,
            source_sha=source_sha,
            resolution=resolution,
            dependency=dependency,
            base=environment,
            workspace_environment=state.environment,
        )
        context = CIWContext(
            root=workspace,
            environment=execution_environment,
            stdout=log,
            stderr=log,
        )
        with contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
            plan = execute_android_validate(
                argparse.Namespace(phase="plan", source_root="source"), context
            )
        _require(plan.outputs.get("runner_profile") == "mobile", "android_plan_invalid")

        prebuild = resolution.canonical_inputs().get(
            "dependency_prebuild_plan_json", ""
        )
        if prebuild:
            prebuild_environment = dict(execution_environment)
            prebuild_environment["INPUT_VALIDATION_PLAN_JSON"] = prebuild
            prebuild_context = CIWContext(
                root=workspace,
                environment=prebuild_environment,
                stdout=log,
                stderr=log,
            )
            with contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
                execute_android_validate(
                    argparse.Namespace(phase="plan", source_root="source"),
                    prebuild_context,
                )
                prebuild_result = execute_android_validate(
                    argparse.Namespace(phase="execute", source_root="source"),
                    prebuild_context,
                )
                execute_android_validate(
                    argparse.Namespace(phase="cleanup", source_root="source"),
                    prebuild_context,
                )
                execute_android_validate(
                    argparse.Namespace(phase="residue", source_root="source"),
                    prebuild_context,
                )
            _require(
                prebuild_result.outputs.get("result") == "success",
                "android_prebuild_failed",
            )

        with contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
            result = execute_android_validate(
                argparse.Namespace(phase="execute", source_root="source"), context
            )
        validation_ok = result.outputs.get("result") == "success"
        _append(log, "[android] canonical validation completed")
    except BaseException as error:
        _append(log, f"[android] validation error={_stable_code(error)}")
    finally:
        _append_state_logs(state.root, log)
        if execution_environment is not None:
            context = CIWContext(
                root=workspace,
                environment=execution_environment,
                stdout=log,
                stderr=log,
            )
            try:
                with contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
                    execute_android_validate(
                        argparse.Namespace(phase="cleanup", source_root="source"), context
                    )
                    execute_android_validate(
                        argparse.Namespace(phase="residue", source_root="source"), context
                    )
            except BaseException as error:
                cleanup_ok = False
                _append(log, f"[android] cleanup error={_stable_code(error)}")
        try:
            cleanup_workspace(
                state.root,
                expected_state_id=state.state_id,
                contract_root=workspace,
            )
        except BaseException as error:
            cleanup_ok = False
            _append(log, f"[workspace] cleanup error={_stable_code(error)}")
    return validation_ok, cleanup_ok


def _execute_python(
    *,
    request: RelayRequest,
    source_sha: str,
    resolution: CentralProfileResolution,
    environment: Mapping[str, str],
    log: TextIO,
) -> tuple[bool, bool]:
    _require(_runner_os(environment) == "Linux", "private_linux_runner_required")
    workspace = _workspace(environment)
    base_environment = _canonical_environment(
        request=request,
        source_sha=source_sha,
        resolution=resolution,
        dependency=None,
        base=environment,
        workspace_environment={},
    )
    plan_context = CIWContext(
        root=workspace,
        environment=base_environment,
        stdout=log,
        stderr=log,
    )
    with contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
        plan = execute_python_validate(
            argparse.Namespace(phase="plan", source_root="source"), plan_context
        )
    workspace_profile = plan.outputs.get("workspace_profile", "")
    _require(bool(workspace_profile), "python_plan_invalid")
    state = _workspace_state(
        request=request,
        source_sha=source_sha,
        environment=environment,
        profile=workspace_profile,
    )
    validation_ok = False
    cleanup_ok = True
    try:
        execution_environment = _canonical_environment(
            request=request,
            source_sha=source_sha,
            resolution=resolution,
            dependency=None,
            base=environment,
            workspace_environment=state.environment,
        )
        context = CIWContext(
            root=workspace,
            environment=execution_environment,
            stdout=log,
            stderr=log,
        )
        with contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
            result = execute_python_validate(
                argparse.Namespace(phase="execute", source_root="source"), context
            )
        validation_ok = result.outputs.get("result") == "success"
        _append(log, "[python] canonical validation completed")
    except BaseException as error:
        _append(log, f"[python] validation error={_stable_code(error)}")
    finally:
        _append_state_logs(state.root, log)
        try:
            cleanup_workspace(
                state.root,
                expected_state_id=state.state_id,
                contract_root=workspace,
            )
        except BaseException as error:
            cleanup_ok = False
            _append(log, f"[workspace] cleanup error={_stable_code(error)}")
    return validation_ok, cleanup_ok


def _execute_family(
    *,
    request: RelayRequest,
    source_sha: str,
    resolution: CentralProfileResolution,
    token_client: GitHubAppRepositoryTokenClient,
    environment: Mapping[str, str],
    log: TextIO,
) -> tuple[bool, bool]:
    _verify_family(resolution, environment)
    if request.workflow_key == "validation.apple":
        return _execute_apple(
            request=request,
            source_sha=source_sha,
            resolution=resolution,
            token_client=token_client,
            environment=environment,
            log=log,
        )
    if request.workflow_key == "validation.android":
        return _execute_android(
            request=request,
            source_sha=source_sha,
            resolution=resolution,
            token_client=token_client,
            environment=environment,
            log=log,
        )
    if request.workflow_key == "validation.python":
        _require(
            not resolution.private_dependency_repository,
            "python_private_dependency_unsupported",
        )
        return _execute_python(
            request=request,
            source_sha=source_sha,
            resolution=resolution,
            environment=environment,
            log=log,
        )
    raise PrivateCiError("unsupported_ci_intent")


def _upload_private_log(
    *,
    ci_run_id: str,
    log_path: Path,
    environment: Mapping[str, str],
) -> R2DiagnosticResult:
    return upload_private_diagnostic(
        diagnostic_path=log_path,
        request_id=ci_run_id,
        run_id=_positive_int(environment, "GITHUB_RUN_ID", 2**63 - 1),
        attempt=_positive_int(environment, "GITHUB_RUN_ATTEMPT", 1000),
        account_id=_required(environment, "R2_ACCOUNT_ID"),
        bucket=_required(environment, "R2_BUCKET"),
        access_key_id=_required(environment, "R2_ACCESS_KEY_ID"),
        secret_access_key=_required(environment, "R2_SECRET_ACCESS_KEY"),
    )


def _receipt(result: R2DiagnosticResult) -> str:
    value = f"r2:{result.object_key}#sha256={result.sha256}"
    _require(len(value.encode("utf-8")) <= 512, "private_log_receipt_too_large")
    return value


def execute_private_ci(environment: Mapping[str, str] = os.environ) -> bool:
    ci_run_id = _required(environment, "INPUT_CI_RUN_ID").lower()
    log_path = _ensure_log(environment, ci_run_id)
    state: dict[str, object] = {
        "ci_run_id": ci_run_id,
        "started": False,
        "terminalized": False,
        "status": "failed",
        "error_summary": "private_ci_interrupted",
    }
    _write_state(environment, ci_run_id, state)
    client = AgentStateCiClient.from_environment(environment)
    status = "failed"
    error_summary: str | None = "private_ci_failed"
    upload: R2DiagnosticResult | None = None
    source: Path | None = None
    release_asset: PrivateReleaseAssetResult | None = None

    with log_path.open("a", encoding="utf-8", buffering=1) as log:
        try:
            request = _claim_request(client, ci_run_id)
            _require(request.inputs == {}, "unsupported_ci_inputs")
            expected_family = "macos" if _runner_os(environment) == "macOS" else "linux"
            _require(
                _family_for_request(request) == expected_family,
                "private_ci_runner_mismatch",
            )
            _start_lifecycle(client, request, environment)
            state["started"] = True
            _write_state(environment, ci_run_id, state)
            _append(log, "[lifecycle] running registered")

            token_client = _source_token_client(environment)
            source_token = token_client.repository_contents_read_token(
                request.repository
            )
            source_sha = _resolve_source_sha(request, source_token)
            source = _checkout_source(
                repository=request.repository,
                source_sha=source_sha,
                token=source_token,
                workspace=_workspace(environment),
                log=log,
            )
            client.evidence(ci_run_id, source_sha)
            _append(log, "[lifecycle] observed source evidence recorded")

            resolution = resolve_profile(
                source_root=str(source),
                project_key=request.project_key,
                workflow_key=request.workflow_key,
                test_profile=request.profile,
                source_repository=request.repository,
                admitted_sha=source_sha,
            )
            _verify_family(resolution, environment)
            _append(log, "[profile] bounded private profile resolved")

            release_spec = resolution.release_asset()
            if release_spec is not None:
                _require(
                    request.workflow_key == "validation.apple",
                    "release_asset_workflow_unsupported",
                )
                _append(log, "[release-asset] verified materialization started")
                release_token = token_client.repository_contents_read_token(
                    release_spec.repository
                )
                release_asset = materialize_private_release_asset(
                    spec=release_spec,
                    token=release_token,
                    source_root=source,
                    state_root=_state_root(environment, ci_run_id),
                )
                _append(log, "[release-asset] verified materialization completed")

            validation_ok, cleanup_ok = _execute_family(
                request=request,
                source_sha=source_sha,
                resolution=resolution,
                token_client=token_client,
                environment=environment,
                log=log,
            )
            if validation_ok and cleanup_ok:
                status = "succeeded"
                error_summary = None
            elif not cleanup_ok:
                error_summary = "private_ci_cleanup_failed"
            else:
                error_summary = "private_ci_validation_failed"
        except BaseException as error:
            error_summary = _stable_code(error)
            _append(log, f"[private-ci] error={error_summary}")
        finally:
            if release_asset is not None:
                try:
                    cleanup_private_release_asset(release_asset)
                    _append(log, "[release-asset] cleanup completed")
                except BaseException as error:
                    status = "failed"
                    error_summary = "release_asset_cleanup_failed"
                    _append(
                        log,
                        f"[release-asset] cleanup error={_stable_code(error)}",
                    )
            if source is not None:
                try:
                    _remove_path(source)
                except OSError:
                    status = "failed"
                    error_summary = "source_cleanup_failed"
                    _append(log, "[source] cleanup failed")
            try:
                upload = _upload_private_log(
                    ci_run_id=ci_run_id,
                    log_path=log_path,
                    environment=environment,
                )
                _append(log, "[logs] R2 upload and read-back verification completed")
            except BaseException as error:
                status = "failed"
                error_summary = "private_log_upload_failed"
                _append(log, f"[logs] upload error={_stable_code(error)}")

    if state.get("started") is True:
        client.finish(
            ci_run_id,
            status=status,
            error_summary=error_summary,
            diagnostic_status="uploaded" if upload is not None else "failed",
            diagnostic_key=_receipt(upload) if upload is not None else None,
        )
        state.update(
            terminalized=True,
            status=status,
            error_summary=error_summary or "",
        )
        _write_state(environment, ci_run_id, state)
    return status == "succeeded" and upload is not None


def recover_private_ci(environment: Mapping[str, str] = os.environ) -> None:
    ci_run_id = _required(environment, "INPUT_CI_RUN_ID").lower()
    state = _read_state(environment, ci_run_id)
    if state is None:
        return
    log_path = _log_path(environment, ci_run_id)
    if (
        state.get("terminalized") is not True
        and state.get("started") is True
        and log_path.exists()
    ):
        client = AgentStateCiClient.from_environment(environment)
        upload: R2DiagnosticResult | None = None
        try:
            upload = _upload_private_log(
                ci_run_id=ci_run_id,
                log_path=log_path,
                environment=environment,
            )
        except (R2DiagnosticError, PrivateCiError):
            upload = None
        client.finish(
            ci_run_id,
            status="failed",
            error_summary="private_ci_interrupted",
            diagnostic_status="uploaded" if upload is not None else "failed",
            diagnostic_key=_receipt(upload) if upload is not None else None,
        )
    _remove_path(_state_root(environment, ci_run_id))


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="Opaque product-neutral private Central CI executor"
    )
    result.add_argument("phase", choices=("plan", "execute", "recover"))
    return result


def main(
    argv: Sequence[str] | None = None,
    environment: Mapping[str, str] = os.environ,
) -> int:
    args = parser().parse_args(list(argv) if argv is not None else None)
    try:
        if args.phase == "plan":
            family = plan_private_ci(environment)
            print(f"private_ci_planned_{family}")
            return 0
        if args.phase == "recover":
            recover_private_ci(environment)
            print("private_ci_recovery_complete")
            return 0
        success = execute_private_ci(environment)
        print("private_ci_succeeded" if success else "private_ci_failed")
        return 0 if success else 1
    except BaseException:
        print("private_ci_failed", file=sys.stderr)
        return 1


__all__ = (
    "PrivateCiError",
    "execute_private_ci",
    "main",
    "plan_private_ci",
    "recover_private_ci",
)
