"""GitHub Actions-side executor for one broker-admitted Central validation."""
from __future__ import annotations

import argparse
import base64
import json
import os
import shutil
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Mapping, TextIO

from .ciw_apple import execute_apple_validate
from .ciw_types import CIWContext
from .r2_diagnostics import R2DiagnosticError, upload_private_diagnostic
from .workspace import WorkspaceContext, cleanup_workspace, prepare_workspace

OIDC_AUDIENCE = "streamscape-ci-broker"
MAX_CALLBACK_BYTES = 256 * 1024
_TIMEOUT_SECONDS = 30
_STATE_FILE = "ci-broker-state.json"


class BrokerActionError(RuntimeError):
    """Stable Actions-side failure with no private payload material."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise BrokerActionError(code)


def _canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode(
        "utf-8"
    )


def _json_object(raw: bytes, code: str) -> dict[str, Any]:
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise BrokerActionError(code) from None
    _require(isinstance(value, dict), code)
    return value


def _broker_url(environment: Mapping[str, str]) -> str:
    value = environment.get("CI_BROKER_URL", "").rstrip("/")
    parsed = urllib.parse.urlsplit(value)
    _require(
        parsed.scheme == "https"
        and bool(parsed.netloc)
        and parsed.path in ("", "/")
        and not parsed.query
        and not parsed.fragment,
        "broker_url_invalid",
    )
    return value


def _positive_int(environment: Mapping[str, str], name: str, maximum: int) -> int:
    value = environment.get(name, "")
    _require(value.isdigit(), f"{name.lower()}_invalid")
    number = int(value)
    _require(1 <= number <= maximum, f"{name.lower()}_invalid")
    return number


def _request_oidc_token(
    environment: Mapping[str, str], opener: Any = urllib.request.urlopen
) -> str:
    request_url = environment.get("ACTIONS_ID_TOKEN_REQUEST_URL", "")
    request_token = environment.get("ACTIONS_ID_TOKEN_REQUEST_TOKEN", "")
    _require(request_url.startswith("https://") and bool(request_token), "oidc_environment_missing")
    separator = "&" if "?" in request_url else "?"
    url = request_url + separator + urllib.parse.urlencode({"audience": OIDC_AUDIENCE})
    request = urllib.request.Request(
        url,
        method="GET",
        headers={"Authorization": f"Bearer {request_token}", "Accept": "application/json"},
    )
    try:
        with opener(request, timeout=_TIMEOUT_SECONDS) as response:
            raw = response.read(MAX_CALLBACK_BYTES + 1)
    except (OSError, urllib.error.URLError, urllib.error.HTTPError, ValueError):
        raise BrokerActionError("oidc_request_failed") from None
    _require(len(raw) <= MAX_CALLBACK_BYTES, "oidc_response_too_large")
    value = _json_object(raw, "oidc_response_invalid")
    token = value.get("value")
    _require(isinstance(token, str) and token.count(".") == 2, "oidc_response_invalid")
    return token


def _broker_post(
    path: str,
    payload: Mapping[str, object],
    environment: Mapping[str, str],
    opener: Any = urllib.request.urlopen,
) -> dict[str, Any]:
    _require(path in ("/actions/start", "/actions/finish"), "broker_callback_invalid")
    oidc = _request_oidc_token(environment, opener)
    request = urllib.request.Request(
        _broker_url(environment) + path,
        data=_canonical(dict(payload)),
        method="POST",
        headers={
            "Authorization": f"Bearer {oidc}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    try:
        with opener(request, timeout=_TIMEOUT_SECONDS) as response:
            raw = response.read(MAX_CALLBACK_BYTES + 1)
            status = int(getattr(response, "status", response.getcode()))
    except urllib.error.HTTPError as error:
        raise BrokerActionError(f"broker_http_{error.code}") from None
    except (OSError, urllib.error.URLError, ValueError):
        raise BrokerActionError("broker_unavailable") from None
    _require(status == 200, f"broker_http_{status}")
    _require(len(raw) <= MAX_CALLBACK_BYTES, "broker_response_too_large")
    value = _json_object(raw, "broker_response_invalid")
    _require(value.get("ok") is True, "broker_response_rejected")
    return value


def _state_root(environment: Mapping[str, str]) -> Path:
    runner_temp = Path(environment.get("RUNNER_TEMP", "")).resolve()
    _require(runner_temp.is_absolute() and runner_temp.exists(), "runner_temp_invalid")
    root = (runner_temp / "central-ci-broker").resolve()
    _require(root.parent == runner_temp, "state_root_invalid")
    return root


def _workspace_root(environment: Mapping[str, str]) -> Path:
    workspace = Path(environment.get("GITHUB_WORKSPACE", "")).resolve()
    _require(workspace.is_absolute() and workspace.exists(), "workspace_invalid")
    return workspace


def _write_state(environment: Mapping[str, str], ci_run_id: str) -> None:
    root = _state_root(environment)
    root.mkdir(mode=0o700, parents=False, exist_ok=True)
    _require(root.is_dir() and not root.is_symlink(), "state_root_invalid")
    path = root / _STATE_FILE
    path.write_text(json.dumps({"ci_run_id": ci_run_id}, separators=(",", ":")), encoding="utf-8")
    path.chmod(0o600)


def _read_state(environment: Mapping[str, str]) -> str | None:
    path = _state_root(environment) / _STATE_FILE
    if not path.exists():
        return None
    _require(path.is_file() and not path.is_symlink(), "state_file_invalid")
    value = _json_object(path.read_bytes(), "state_file_invalid")
    ci_run_id = value.get("ci_run_id")
    _require(isinstance(ci_run_id, str) and len(ci_run_id) == 36, "state_file_invalid")
    return ci_run_id


def _remove_bounded(path: Path, root: Path) -> None:
    resolved_root = root.resolve()
    candidate = path if path.is_absolute() else resolved_root / path
    try:
        candidate.absolute().relative_to(resolved_root)
    except ValueError:
        raise BrokerActionError("cleanup_path_invalid") from None
    if candidate.is_symlink() or candidate.is_file():
        candidate.unlink(missing_ok=True)
    elif candidate.exists():
        shutil.rmtree(candidate)


def cleanup(environment: Mapping[str, str] = os.environ) -> None:
    workspace = _workspace_root(environment)
    runner_temp = Path(environment.get("RUNNER_TEMP", "")).resolve()
    targets = (
        workspace / "source",
        runner_temp / "central-ci-diagnostic.log",
        runner_temp / "central-ci-broker",
    )
    for target in targets:
        root = workspace if target == workspace / "source" else runner_temp
        _remove_bounded(target, root)
    for target in targets:
        _require(not target.exists() and not target.is_symlink(), "cleanup_residue")


def _git_environment(token: str, environment: Mapping[str, str]) -> dict[str, str]:
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


def _run_private(
    argv: list[str],
    *,
    cwd: Path,
    diagnostic: Any,
    environment: Mapping[str, str],
    timeout: int,
) -> int:
    try:
        completed = subprocess.run(
            argv,
            cwd=cwd,
            env=dict(environment),
            stdin=subprocess.DEVNULL,
            stdout=diagnostic,
            stderr=subprocess.STDOUT,
            check=False,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired):
        return 124
    return int(completed.returncode)


def _checkout_source(
    *,
    repository: str,
    sha: str,
    token: str,
    source: Path,
    diagnostic: Any,
    environment: Mapping[str, str],
) -> None:
    _remove_bounded(source, source.parent)
    source.mkdir(mode=0o700)
    git_env = _git_environment(token, environment)
    commands = (
        ["git", "init", "-q"],
        ["git", "remote", "add", "origin", f"https://github.com/{repository}.git"],
        ["git", "fetch", "--quiet", "--no-tags", "--depth=1", "origin", sha],
        ["git", "checkout", "--quiet", "--detach", "FETCH_HEAD"],
        ["git", "remote", "remove", "origin"],
    )
    for command in commands:
        _require(
            _run_private(
                command,
                cwd=source,
                diagnostic=diagnostic,
                environment=git_env,
                timeout=180,
            )
            == 0,
            "source_checkout_failed",
        )
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
        raise BrokerActionError("source_checkout_failed") from None
    _require(observed == sha, "source_sha_mismatch")


def _apple_validation_plan(workspace: str, scheme: str, test_target: str) -> str:
    return json.dumps(
        {
            "stages": [
                {
                    "id": "broker-host-test",
                    "platform": "macos",
                    "operation": "test",
                    "working_directory": ".",
                    "container": {"kind": "workspace", "path": workspace},
                    "scheme": scheme,
                    "configuration": "Debug",
                    "test_plan": "",
                    "package_resolution_mode": "resolve-only",
                    "resolved_files": [],
                    "script": None,
                    "xcodebuild_arguments": [],
                    "test_selectors": [test_target],
                    "expected_outputs": [],
                    "cleanup_paths": [],
                }
            ]
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def _shared_apple_environment(
    *,
    repository: str,
    source_sha: str,
    source_token: str,
    workspace: str,
    scheme: str,
    test_target: str,
    environment: Mapping[str, str],
) -> dict[str, str]:
    result = _git_environment(source_token, environment)
    for command_file in ("GITHUB_OUTPUT", "GITHUB_ENV", "GITHUB_STEP_SUMMARY"):
        result.pop(command_file, None)
    result.update(
        GITHUB_REPOSITORY=repository,
        INPUT_ADMITTED_SHA=source_sha,
        INPUT_VALIDATION_SCOPE="protected-full",
        INPUT_VALIDATION_PLAN_JSON=_apple_validation_plan(workspace, scheme, test_target),
        INPUT_SOURCE_TRUST="trusted-exact",
        INPUT_PRIVATE_DEPENDENCY_REPOSITORY="",
        INPUT_PRIVATE_DEPENDENCY_SHA="",
        INPUT_PRIVATE_DEPENDENCY_SUBDIRECTORY=".",
        INPUT_PRIVATE_DEPENDENCY_ID="",
    )
    return result


def _execute_shared_apple_validation(
    *,
    repository: str,
    source_sha: str,
    source_token: str,
    workspace: str,
    scheme: str,
    test_target: str,
    run_id: int,
    run_attempt: int,
    workspace_root: Path,
    runner_temp: Path,
    diagnostic: TextIO,
    environment: Mapping[str, str],
) -> bool:
    runner_os = environment.get("RUNNER_OS", "")
    job = environment.get("GITHUB_JOB", "")
    _require(runner_os == "macOS", "runner_os_invalid")
    _require(bool(job), "github_job_invalid")

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


def _r2_environment(environment: Mapping[str, str]) -> tuple[str, str, str, str]:
    names = (
        "R2_ACCOUNT_ID",
        "R2_BUCKET",
        "R2_ACCESS_KEY_ID",
        "R2_SECRET_ACCESS_KEY",
    )
    values = tuple(environment.get(name, "") for name in names)
    _require(all(values), "r2_writer_environment_missing")
    return values  # type: ignore[return-value]


def _finish(
    *,
    environment: Mapping[str, str],
    ci_run_id: str,
    status: str,
    error_summary: str | None,
    logs_status: str | None,
    logs_object_key: str | None = None,
    logs_sha256: str | None = None,
    opener: Any = urllib.request.urlopen,
) -> None:
    payload: dict[str, object] = {
        "dispatch_id": environment.get("CI_DISPATCH_ID", ""),
        "dispatch_token": environment.get("CI_DISPATCH_TOKEN", ""),
        "ci_run_id": ci_run_id,
        "status": status,
    }
    if logs_status is not None:
        payload["logs_status"] = logs_status
    if error_summary:
        payload["error_summary"] = error_summary
    if logs_object_key is not None:
        payload["logs_object_key"] = logs_object_key
    if logs_sha256 is not None:
        payload["logs_sha256"] = logs_sha256
    _broker_post("/actions/finish", payload, environment, opener)


def execute_apple_host(
    environment: Mapping[str, str] = os.environ,
    opener: Any = urllib.request.urlopen,
) -> None:
    dispatch_id = environment.get("CI_DISPATCH_ID", "")
    dispatch_token = environment.get("CI_DISPATCH_TOKEN", "")
    _require(20 <= len(dispatch_id) <= 128 and bool(dispatch_token), "dispatch_environment_missing")
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
    _require(start.get("capability") == "apple-host-test", "capability_rejected")
    ci_run_id = start.get("ci_run_id")
    repository = start.get("repository")
    source_sha = start.get("source_sha")
    source_token = start.get("source_token")
    workspace_name = start.get("workspace")
    scheme = start.get("scheme")
    test_target = start.get("test_target")
    _require(isinstance(ci_run_id, str) and len(ci_run_id) == 36, "start_response_invalid")
    _require(isinstance(repository, str) and 3 <= len(repository) <= 256, "start_response_invalid")
    _require(isinstance(source_sha, str) and len(source_sha) == 40, "start_response_invalid")
    _require(isinstance(source_token, str) and bool(source_token), "start_response_invalid")
    _require(
        isinstance(workspace_name, str) and workspace_name.endswith(".xcworkspace"),
        "start_response_invalid",
    )
    _require(isinstance(scheme, str) and bool(scheme), "start_response_invalid")
    _require(isinstance(test_target, str) and bool(test_target), "start_response_invalid")
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


def cancel_if_active(
    environment: Mapping[str, str] = os.environ,
    opener: Any = urllib.request.urlopen,
) -> None:
    ci_run_id = _read_state(environment)
    if ci_run_id is None:
        return
    try:
        _finish(
            environment=environment,
            ci_run_id=ci_run_id,
            status="cancelled",
            error_summary="github_job_cancelled",
            logs_status=None,
            opener=opener,
        )
    except BrokerActionError:
        return


__all__ = (
    "BrokerActionError",
    "OIDC_AUDIENCE",
    "cancel_if_active",
    "cleanup",
    "execute_apple_host",
)
