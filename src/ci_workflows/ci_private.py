"""Product-neutral opaque private Central CI execution.

The public workflow passes only an opaque CI UUID. This module privately reclaims
the canonical Agent State request, resolves the human ref, reads the bounded
product-owned Central profile from the exact source checkout, and delegates
validation to existing technology implementation functions. Detailed output stays
runner-local and terminal state is written only after R2 upload/read-back digest
verification.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
from pathlib import Path
import sys
from typing import Mapping, Sequence, TextIO

from .central_profile import CentralProfileResolution, resolve_profile
from .ci_lifecycle import AgentStateCiClient
from .ci_private_apple import (
    PrivateAppleCiError,
    _append,
    _append_state_logs,
    _checkout_source,
    _claim_request,
    _dependency_receipts,
    _ensure_log,
    _execution_environment,
    _log_path,
    _positive_int,
    _r2_upload,
    _read_state,
    _receipt,
    _remove_no_follow,
    _runner_temp,
    _source_token_client,
    _resolve_source_sha,
    _stable_code,
    _start_lifecycle,
    _state_root,
    _workspace,
    _write_state,
    _execute_validation as _execute_apple_validation,
)
from .ci_relay import RelayRequest
from .ciw_android import execute_android_validate
from .ciw_python import execute_python_validate
from .ciw_types import CIWContext
from .dependencies import DependencyResult
from .private_release_asset import (
    PrivateReleaseAssetResult,
    cleanup_private_release_asset,
    materialize_private_release_asset,
)
from .r2_diagnostics import R2DiagnosticError, R2DiagnosticResult
from .workspace import WorkspaceContext, cleanup_workspace, prepare_workspace

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


def _family_for_request(request: RelayRequest) -> str:
    family = _RUNNER_FAMILY.get(request.workflow_key)
    _require(family is not None and request.profile == "host", "unsupported_ci_intent")
    return family


def plan_private_ci(environment: Mapping[str, str] = os.environ) -> str:
    """Privately reclaim one request and expose only its coarse hosted runner family."""
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


def _runner_os(environment: Mapping[str, str]) -> str:
    value = environment.get("RUNNER_OS", "")
    _require(value in {"Linux", "macOS"}, "private_ci_runner_required")
    return value


def _verify_family(resolution: CentralProfileResolution, environment: Mapping[str, str]) -> None:
    expected = "macos" if _runner_os(environment) == "macOS" else "linux"
    _require(resolution.executor_family == expected, "private_ci_runner_mismatch")


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
        base=base,
        workspace_environment=workspace_environment,
    )
    result["INPUT_EXECUTION_BACKEND"] = "github-hosted"
    for key, value in resolution.canonical_inputs().items():
        result[f"INPUT_{key.upper()}"] = value
    package_token = base.get("CIW_MAVEN_PACKAGE_READ_TOKEN", "")
    if package_token:
        result["CIW_MAVEN_PACKAGE_READ_TOKEN"] = package_token
    return result


def _workspace_state(
    *,
    request: RelayRequest,
    source_sha: str,
    environment: Mapping[str, str],
    profile: str,
):
    workspace = _workspace(environment)
    runner_temp = _runner_temp(environment)
    runner_os = _runner_os(environment)
    return prepare_workspace(
        WorkspaceContext(
            workspace=workspace,
            runner_temp=runner_temp,
            repository=request.repository,
            run_id=str(_positive_int(environment, "GITHUB_RUN_ID", 2**63 - 1)),
            run_attempt=_positive_int(environment, "GITHUB_RUN_ATTEMPT", 1000),
            job=_required(environment, "GITHUB_JOB"),
            runner_os=runner_os,
        ),
        profile=profile,
        cache_mode="disabled",
        source_sha=source_sha,
        trust_mode="trusted-exact",
        contract_root=workspace,
    )


def _execute_android(
    *,
    request: RelayRequest,
    source_sha: str,
    resolution: CentralProfileResolution,
    token_client,
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
    dependency: DependencyResult | None = None
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
        context = CIWContext(root=workspace, environment=execution_environment, stdout=log, stderr=log)
        with contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
            plan = execute_android_validate(argparse.Namespace(phase="plan", source_root="source"), context)
        _require(plan.outputs.get("runner_profile") == "mobile", "android_plan_invalid")

        prebuild = resolution.canonical_inputs().get("dependency_prebuild_plan_json", "")
        if prebuild:
            prebuild_environment = dict(execution_environment)
            prebuild_environment["INPUT_VALIDATION_PLAN_JSON"] = prebuild
            prebuild_context = CIWContext(root=workspace, environment=prebuild_environment, stdout=log, stderr=log)
            with contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
                execute_android_validate(argparse.Namespace(phase="plan", source_root="source"), prebuild_context)
                prebuild_result = execute_android_validate(argparse.Namespace(phase="execute", source_root="source"), prebuild_context)
                execute_android_validate(argparse.Namespace(phase="cleanup", source_root="source"), prebuild_context)
                execute_android_validate(argparse.Namespace(phase="residue", source_root="source"), prebuild_context)
            _require(prebuild_result.outputs.get("result") == "success", "android_prebuild_failed")

        with contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
            result = execute_android_validate(argparse.Namespace(phase="execute", source_root="source"), context)
        validation_ok = result.outputs.get("result") == "success"
        _append(log, "[android] canonical validation completed")
    except BaseException as error:
        _append(log, f"[android] validation error={_stable_code(error)}")
        validation_ok = False
    finally:
        _append_state_logs(state.root, log)
        if execution_environment is not None:
            context = CIWContext(root=workspace, environment=execution_environment, stdout=log, stderr=log)
            try:
                with contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
                    execute_android_validate(argparse.Namespace(phase="cleanup", source_root="source"), context)
                    execute_android_validate(argparse.Namespace(phase="residue", source_root="source"), context)
            except BaseException as error:
                cleanup_ok = False
                _append(log, f"[android] cleanup error={_stable_code(error)}")
        try:
            cleanup_workspace(state.root, expected_state_id=state.state_id, contract_root=workspace)
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
    plan_context = CIWContext(root=workspace, environment=base_environment, stdout=log, stderr=log)
    with contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
        plan = execute_python_validate(argparse.Namespace(phase="plan", source_root="source"), plan_context)
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
        context = CIWContext(root=workspace, environment=execution_environment, stdout=log, stderr=log)
        with contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
            result = execute_python_validate(argparse.Namespace(phase="execute", source_root="source"), context)
        validation_ok = result.outputs.get("result") == "success"
        _append(log, "[python] canonical validation completed")
    except BaseException as error:
        _append(log, f"[python] validation error={_stable_code(error)}")
        validation_ok = False
    finally:
        _append_state_logs(state.root, log)
        try:
            cleanup_workspace(state.root, expected_state_id=state.state_id, contract_root=workspace)
        except BaseException as error:
            cleanup_ok = False
            _append(log, f"[workspace] cleanup error={_stable_code(error)}")
    return validation_ok, cleanup_ok


def _execute_family(
    *,
    request: RelayRequest,
    source_sha: str,
    resolution: CentralProfileResolution,
    token_client,
    environment: Mapping[str, str],
    log: TextIO,
) -> tuple[bool, bool]:
    _verify_family(resolution, environment)
    if request.workflow_key == "validation.apple":
        return _execute_apple_validation(
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
        _require(not resolution.private_dependency_repository, "python_private_dependency_unsupported")
        return _execute_python(
            request=request,
            source_sha=source_sha,
            resolution=resolution,
            environment=environment,
            log=log,
        )
    raise PrivateCiError("unsupported_ci_intent")


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
            _require(_family_for_request(request) == ("macos" if _runner_os(environment) == "macOS" else "linux"), "private_ci_runner_mismatch")
            _start_lifecycle(client, request, environment)
            state["started"] = True
            _write_state(environment, ci_run_id, state)
            _append(log, "[lifecycle] running registered")

            token_client = _source_token_client(environment)
            source_token = token_client.repository_contents_read_token(request.repository)
            source_sha = _resolve_source_sha(request, source_token)
            source = _checkout_source(
                repository=request.repository,
                source_sha=source_sha,
                token=source_token,
                workspace=_workspace(environment),
                environment=environment,
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
                _require(request.workflow_key == "validation.apple", "release_asset_workflow_unsupported")
                _append(log, "[release-asset] verified materialization started")
                release_token = token_client.repository_contents_read_token(release_spec.repository)
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
                    _append(log, f"[release-asset] cleanup error={_stable_code(error)}")
            if source is not None:
                try:
                    _remove_no_follow(source)
                except OSError:
                    status = "failed"
                    error_summary = "source_cleanup_failed"
                    _append(log, "[source] cleanup failed")
            try:
                upload = _r2_upload(ci_run_id=ci_run_id, log_path=log_path, environment=environment)
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
        state.update(terminalized=True, status=status, error_summary=error_summary or "")
        _write_state(environment, ci_run_id, state)
    return status == "succeeded" and upload is not None


def recover_private_ci(environment: Mapping[str, str] = os.environ) -> None:
    ci_run_id = _required(environment, "INPUT_CI_RUN_ID").lower()
    state = _read_state(environment, ci_run_id)
    if state is None:
        return
    log_path = _log_path(environment, ci_run_id)
    if state.get("terminalized") is not True and state.get("started") is True and log_path.exists():
        client = AgentStateCiClient.from_environment(environment)
        upload: R2DiagnosticResult | None = None
        try:
            upload = _r2_upload(ci_run_id=ci_run_id, log_path=log_path, environment=environment)
        except (R2DiagnosticError, PrivateAppleCiError, PrivateCiError):
            upload = None
        client.finish(
            ci_run_id,
            status="failed",
            error_summary="private_ci_interrupted",
            diagnostic_status="uploaded" if upload is not None else "failed",
            diagnostic_key=_receipt(upload) if upload is not None else None,
        )
    _remove_no_follow(_state_root(environment, ci_run_id))


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Opaque product-neutral private Central CI executor")
    result.add_argument("phase", choices=("plan", "execute", "recover"))
    return result


def main(argv: Sequence[str] | None = None, environment: Mapping[str, str] = os.environ) -> int:
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
