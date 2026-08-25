"""Opaque private Apple CI execution with R2 as the detailed log authority."""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence, TextIO

from .central_profile import CentralProfileResolution, resolve_profile
from .ci_lifecycle import AgentStateCiClient, WorkflowIdentity
from .ci_relay import RelayRequest
from .ciw_apple import execute_apple_validate
from .ciw_types import CIWContext
from .dependencies import DependencyResult, checkout_private_dependency
from .github_app_token import GitHubAppRepositoryTokenClient
from .private_release_asset import (
    PrivateReleaseAssetResult,
    cleanup_private_release_asset,
    materialize_private_release_asset,
)
from .r2_diagnostics import R2DiagnosticError, R2DiagnosticResult, upload_private_diagnostic
from .source_admission import _resolve_tag
from .source_github import GitHubSourceProvider
from .workspace import WorkspaceContext, cleanup_workspace, prepare_workspace

_STATE_DIRECTORY = "central-private-ci"
_STATE_FILE = "state.json"
_LOG_FILE = "private.log"
_SHA = re.compile(r"[0-9a-f]{40}\Z")


class PrivateAppleCiError(RuntimeError):
    """Stable public-safe failure for the opaque private executor."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise PrivateAppleCiError(code)


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
    _require(value.is_absolute() and value.is_dir() and not value.is_symlink(), "invalid_runner_temp")
    return value


def _workspace(environment: Mapping[str, str]) -> Path:
    value = Path(_required(environment, "GITHUB_WORKSPACE")).resolve()
    _require(value.is_absolute() and value.is_dir() and not value.is_symlink(), "invalid_workspace")
    return value


def _state_root(environment: Mapping[str, str], ci_run_id: str) -> Path:
    root = (_runner_temp(environment) / _STATE_DIRECTORY / ci_run_id).resolve()
    expected_parent = (_runner_temp(environment) / _STATE_DIRECTORY).resolve()
    _require(root.parent == expected_parent, "invalid_private_state")
    expected_parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    root.mkdir(mode=0o700, exist_ok=True)
    _require(root.is_dir() and not root.is_symlink(), "invalid_private_state")
    return root


def _state_path(environment: Mapping[str, str], ci_run_id: str) -> Path:
    return _state_root(environment, ci_run_id) / _STATE_FILE


def _log_path(environment: Mapping[str, str], ci_run_id: str) -> Path:
    return _state_root(environment, ci_run_id) / _LOG_FILE


def _write_state(environment: Mapping[str, str], ci_run_id: str, value: Mapping[str, object]) -> None:
    path = _state_path(environment, ci_run_id)
    temporary = path.with_suffix(".tmp")
    raw = json.dumps(dict(value), sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    temporary.write_text(raw, encoding="utf-8")
    temporary.chmod(0o600)
    temporary.replace(path)
    path.chmod(0o600)


def _read_state(environment: Mapping[str, str], ci_run_id: str) -> dict[str, Any] | None:
    path = _state_path(environment, ci_run_id)
    if not path.exists():
        return None
    _require(path.is_file() and not path.is_symlink(), "invalid_private_state")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise PrivateAppleCiError("invalid_private_state") from None
    _require(isinstance(value, dict), "invalid_private_state")
    return value


def _ensure_log(environment: Mapping[str, str], ci_run_id: str) -> Path:
    path = _log_path(environment, ci_run_id)
    if not path.exists():
        path.touch(mode=0o600, exist_ok=False)
    _require(path.is_file() and not path.is_symlink(), "invalid_private_log")
    path.chmod(0o600)
    return path


def _append(log: TextIO, message: str) -> None:
    log.write(message.rstrip("\n") + "\n")
    log.flush()


def _stable_code(error: BaseException) -> str:
    code = getattr(error, "code", None)
    if isinstance(code, str) and re.fullmatch(r"[a-z][a-z0-9_-]{1,127}", code):
        return code
    return "private_ci_internal_error"


def _git_environment(token: str, environment: Mapping[str, str]) -> dict[str, str]:
    import base64

    _require(bool(token), "source_token_missing")
    basic = base64.b64encode(f"x-access-token:{token}".encode("utf-8")).decode("ascii")
    result = dict(environment)
    result.update(
        GIT_CONFIG_COUNT="1",
        GIT_CONFIG_KEY_0="http.https://github.com/.extraheader",
        GIT_CONFIG_VALUE_0=f"AUTHORIZATION: basic {basic}",
        GIT_TERMINAL_PROMPT="0",
    )
    return result


def _remove_no_follow(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink(missing_ok=True)
        return
    if path.exists():
        shutil.rmtree(path)


def _run_private(
    argv: Sequence[str],
    *,
    cwd: Path,
    environment: Mapping[str, str],
    log: TextIO,
    timeout: int,
) -> None:
    try:
        completed = subprocess.run(
            list(argv),
            cwd=cwd,
            env=dict(environment),
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            text=True,
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        raise PrivateAppleCiError("private_command_timeout") from None
    except OSError:
        raise PrivateAppleCiError("private_command_unavailable") from None
    _require(completed.returncode == 0, "private_command_failed")


def _resolve_source_sha(
    request: RelayRequest,
    token: str,
) -> str:
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
    environment: Mapping[str, str],
    log: TextIO,
) -> Path:
    source = workspace / "source"
    _remove_no_follow(source)
    source.mkdir(mode=0o700)
    git_environment = _git_environment(token, environment)
    _append(log, "[source] checkout started")
    commands = (
        ("git", "init", "-q"),
        ("git", "remote", "add", "origin", f"https://github.com/{repository}.git"),
        ("git", "fetch", "--quiet", "--no-tags", "--depth=1", "origin", source_sha),
        ("git", "checkout", "--quiet", "--detach", "FETCH_HEAD"),
        ("git", "remote", "remove", "origin"),
    )
    for command in commands:
        _run_private(command, cwd=source, environment=git_environment, log=log, timeout=180)
    try:
        observed = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=source,
            env=dict(environment),
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=10,
        ).strip()
    except (OSError, subprocess.SubprocessError):
        raise PrivateAppleCiError("source_checkout_failed") from None
    _require(observed == source_sha, "source_sha_mismatch")
    _append(log, "[source] exact checkout completed")
    return source


def _append_state_logs(root: Path, log: TextIO) -> None:
    if not root.exists() or root.is_symlink():
        return
    candidates = sorted(path for path in root.rglob("*.log") if path.is_file() and not path.is_symlink())
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
    token = token_client.repository_contents_read_token(resolution.private_dependency_repository)
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
            INPUT_PRIVATE_DEPENDENCY_REMOTES_ERASED="true" if dependency.remotes_erased else "false",
            INPUT_PRIVATE_DEPENDENCY_CREDENTIALS_ERASED="true" if dependency.credentials_erased else "false",
            INPUT_PRIVATE_DEPENDENCY_HEAD_SHA=dependency.head_sha,
            INPUT_PRIVATE_DEPENDENCY_CHECKOUT_REPOSITORY=dependency.repository,
            INPUT_PRIVATE_DEPENDENCY_CHECKOUT_ID=dependency.dependency_id,
            INPUT_PRIVATE_DEPENDENCY_EXPECTED_SUBPATH=dependency.expected_subpath,
        )
    return result


def _execute_validation(
    *,
    request: RelayRequest,
    source_sha: str,
    resolution: CentralProfileResolution,
    token_client: GitHubAppRepositoryTokenClient,
    environment: Mapping[str, str],
    log: TextIO,
) -> tuple[bool, bool]:
    workspace = _workspace(environment)
    runner_temp = _runner_temp(environment)
    run_id = _positive_int(environment, "GITHUB_RUN_ID", 2**63 - 1)
    run_attempt = _positive_int(environment, "GITHUB_RUN_ATTEMPT", 1000)
    _require(environment.get("RUNNER_OS") == "macOS", "private_apple_runner_required")
    job = _required(environment, "GITHUB_JOB")

    workspace_state = prepare_workspace(
        WorkspaceContext(
            workspace=workspace,
            runner_temp=runner_temp,
            repository=request.repository,
            run_id=str(run_id),
            run_attempt=run_attempt,
            job=job,
            runner_os="macOS",
        ),
        profile="apple",
        cache_mode="disabled",
        source_sha=source_sha,
        trust_mode="trusted-exact",
        contract_root=workspace,
    )
    dependency: DependencyResult | None = None
    validation_ok = False
    cleanup_ok = True
    try:
        dependency = _dependency_receipts(
            resolution=resolution,
            token_client=token_client,
            workspace_state_root=workspace_state.root,
            contract_root=workspace,
            log=log,
        )
        execution_environment = _execution_environment(
            request=request,
            source_sha=source_sha,
            resolution=resolution,
            dependency=dependency,
            base=environment,
            workspace_environment=workspace_state.environment,
        )
        context = CIWContext(root=workspace, environment=execution_environment, stdout=log, stderr=log)
        with contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
            result = execute_apple_validate(
                argparse.Namespace(phase="execute", source_root="source"),
                context,
            )
        validation_ok = result.outputs.get("result") == "success"
        _append(log, "[apple] validation completed")
    except BaseException as error:
        _append(log, f"[apple] validation error={_stable_code(error)}")
        validation_ok = False
    finally:
        _append_state_logs(workspace_state.root, log)
        execution_environment = locals().get("execution_environment", dict(environment))
        context = CIWContext(root=workspace, environment=execution_environment, stdout=log, stderr=log)
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
                workspace_state.root,
                expected_state_id=workspace_state.state_id,
                contract_root=workspace,
            )
        except BaseException as error:
            cleanup_ok = False
            _append(log, f"[workspace] cleanup error={_stable_code(error)}")
    return validation_ok, cleanup_ok


def _r2_upload(
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


def _claim_request(client: AgentStateCiClient, ci_run_id: str) -> RelayRequest:
    result = client._rpc("claim_ci_run", {"p_ci_run_id": ci_run_id})
    run = client._run(result)
    return RelayRequest.from_claimed_run(run)


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


def execute_private_apple(
    environment: Mapping[str, str] = os.environ,
) -> bool:
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
            _require(resolution.capability == "apple-host-test", "unsupported_ci_capability")
            _append(log, "[profile] bounded private profile resolved")

            release_spec = resolution.release_asset()
            if release_spec is not None:
                _append(log, "[release-asset] verified materialization started")
                release_token = token_client.repository_contents_read_token(release_spec.repository)
                release_asset = materialize_private_release_asset(
                    spec=release_spec,
                    token=release_token,
                    source_root=source,
                    state_root=_state_root(environment, ci_run_id),
                )
                _append(log, "[release-asset] verified materialization completed")

            validation_ok, cleanup_ok = _execute_validation(
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
                error_summary = "apple_cleanup_failed"
            else:
                error_summary = "apple_validation_failed"
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
        diagnostic_status = "uploaded" if upload is not None else "failed"
        client.finish(
            ci_run_id,
            status=status,
            error_summary=error_summary,
            diagnostic_status=diagnostic_status,
            diagnostic_key=_receipt(upload) if upload is not None else None,
        )
        state.update(
            terminalized=True,
            status=status,
            error_summary=error_summary or "",
        )
        _write_state(environment, ci_run_id, state)
    return status == "succeeded" and upload is not None


def recover_private_apple(environment: Mapping[str, str] = os.environ) -> None:
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
        except (R2DiagnosticError, PrivateAppleCiError):
            upload = None
        client.finish(
            ci_run_id,
            status="failed",
            error_summary="private_ci_interrupted",
            diagnostic_status="uploaded" if upload is not None else "failed",
            diagnostic_key=_receipt(upload) if upload is not None else None,
        )
    root = _state_root(environment, ci_run_id)
    _remove_no_follow(root)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Opaque private Apple CI executor")
    result.add_argument("phase", choices=("execute", "recover"))
    return result


def main(
    argv: Sequence[str] | None = None,
    environment: Mapping[str, str] = os.environ,
) -> int:
    args = parser().parse_args(list(argv) if argv is not None else None)
    try:
        if args.phase == "recover":
            recover_private_apple(environment)
            print("private_ci_recovery_complete")
            return 0
        success = execute_private_apple(environment)
        print("private_ci_succeeded" if success else "private_ci_failed")
        return 0 if success else 1
    except BaseException:
        print("private_ci_failed", file=sys.stderr)
        return 1


__all__ = (
    "PrivateAppleCiError",
    "execute_private_apple",
    "main",
    "recover_private_apple",
)