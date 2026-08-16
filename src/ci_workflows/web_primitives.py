"""Product-neutral static-web validation and Cloudflare Pages deployment primitives."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Protocol, Sequence
from urllib.parse import urlparse

_ACCOUNT_ID = re.compile(r"^[0-9a-fA-F]{32}$")
_PROJECT_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_COMMIT_SHA = re.compile(r"^[0-9a-f]{40}$")
_MAX_OUTPUT_RECORD_BYTES = 1024 * 1024
_AUTH_ENVIRONMENT_KEYS = {
    "CF_ACCOUNT_ID",
    "CF_API_TOKEN",
    "CF_API_KEY",
    "CF_EMAIL",
    "CLOUDFLARE_API_KEY",
    "CLOUDFLARE_EMAIL",
}
_CONTROL_ENVIRONMENT_KEYS = {
    "WRANGLER_LOG_PATH",
    "WRANGLER_OUTPUT_FILE_DIRECTORY",
    "CLOUDFLARE_ENV",
}


class WebPrimitiveError(RuntimeError):
    """Stable fail-closed web primitive error."""

    def __init__(self, code: str, *, cleanup_failed: bool = False) -> None:
        if re.fullmatch(r"[a-z][a-z0-9_]{2,95}", code) is None:
            raise ValueError("web primitive error code must be a safe identifier")
        self.code = code
        self.cleanup_failed = cleanup_failed
        super().__init__(code)


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise WebPrimitiveError(code)


@dataclass(frozen=True, slots=True)
class ProcessOutcome:
    returncode: int
    stdout: str = ""
    stderr: str = ""


class CommandRunner(Protocol):
    def run(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        env: Mapping[str, str],
        timeout_seconds: int,
    ) -> ProcessOutcome: ...


@dataclass(frozen=True, slots=True)
class StaticFile:
    path: str
    size: int
    sha256: str


@dataclass(frozen=True, slots=True)
class StaticManifest:
    files: tuple[StaticFile, ...]
    file_count: int
    total_bytes: int
    sha256: str

    def canonical_json(self) -> str:
        return json.dumps(
            {
                "file_count": self.file_count,
                "files": [
                    {"path": row.path, "sha256": row.sha256, "size": row.size}
                    for row in self.files
                ],
                "sha256": self.sha256,
                "total_bytes": self.total_bytes,
            },
            sort_keys=True,
            separators=(",", ":"),
        )


@dataclass(frozen=True, slots=True)
class VerificationResult:
    manifest: StaticManifest
    returncode: int


@dataclass(frozen=True, slots=True)
class PagesDeploymentResult:
    project_name: str
    branch: str
    deployment_id: str | None
    url: str
    manifest: StaticManifest


def _safe_cli_value(value: str, code: str, *, maximum: int = 255) -> str:
    _require(
        isinstance(value, str)
        and 0 < len(value) <= maximum
        and not value.startswith("-")
        and all(character not in value for character in ("\x00", "\r", "\n")),
        code,
    )
    return value


def _directory(path: Path, code: str) -> Path:
    candidate = Path(path)
    _require(not candidate.is_symlink(), code)
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as error:
        raise WebPrimitiveError(code) from error
    _require(resolved.is_dir(), code)
    return resolved


def _file_digest(path: Path) -> tuple[int, str]:
    try:
        metadata = path.stat(follow_symlinks=False)
    except OSError as error:
        raise WebPrimitiveError("static_output_invalid") from error
    _require(stat.S_ISREG(metadata.st_mode), "static_output_invalid")
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as error:
        raise WebPrimitiveError("static_output_invalid") from error
    return metadata.st_size, digest.hexdigest()


def inspect_static_output(
    directory: Path,
    *,
    maximum_files: int = 100_000,
    maximum_total_bytes: int | None = None,
) -> StaticManifest:
    """Validate a static output tree and return a deterministic content manifest."""

    root = _directory(directory, "static_output_invalid")
    _require(
        isinstance(maximum_files, int)
        and not isinstance(maximum_files, bool)
        and maximum_files > 0,
        "static_output_limit_invalid",
    )
    if maximum_total_bytes is not None:
        _require(
            isinstance(maximum_total_bytes, int)
            and not isinstance(maximum_total_bytes, bool)
            and maximum_total_bytes >= 0,
            "static_output_limit_invalid",
        )

    rows: list[StaticFile] = []
    total_bytes = 0
    try:
        for current, directory_names, file_names in os.walk(root, followlinks=False):
            current_path = Path(current)
            directory_names.sort()
            file_names.sort()
            for name in tuple(directory_names):
                entry = current_path / name
                metadata = entry.lstat()
                _require(not stat.S_ISLNK(metadata.st_mode), "static_output_symlink")
                _require(stat.S_ISDIR(metadata.st_mode), "static_output_invalid")
            for name in file_names:
                entry = current_path / name
                relative = entry.relative_to(root).as_posix()
                _require(
                    relative
                    and "\x00" not in relative
                    and "\r" not in relative
                    and "\n" not in relative,
                    "static_output_invalid",
                )
                metadata = entry.lstat()
                _require(not stat.S_ISLNK(metadata.st_mode), "static_output_symlink")
                _require(stat.S_ISREG(metadata.st_mode), "static_output_invalid")
                size, digest = _file_digest(entry)
                total_bytes += size
                rows.append(StaticFile(relative, size, digest))
                _require(len(rows) <= maximum_files, "static_output_too_large")
                if maximum_total_bytes is not None:
                    _require(
                        total_bytes <= maximum_total_bytes,
                        "static_output_too_large",
                    )
    except WebPrimitiveError:
        raise
    except OSError as error:
        raise WebPrimitiveError("static_output_invalid") from error

    _require(bool(rows), "static_output_empty")
    rows.sort(key=lambda row: row.path)
    manifest_payload = [
        {"path": row.path, "sha256": row.sha256, "size": row.size}
        for row in rows
    ]
    manifest_bytes = json.dumps(
        manifest_payload,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    summary = hashlib.sha256(manifest_bytes).hexdigest()
    return StaticManifest(tuple(rows), len(rows), total_bytes, summary)


def run_static_verification(
    directory: Path,
    command: Sequence[str],
    runner: CommandRunner,
    *,
    environment: Mapping[str, str],
    timeout_seconds: int = 600,
) -> VerificationResult:
    """Run one direct-argv product verifier and prove it did not mutate output."""

    root = _directory(directory, "static_output_invalid")
    _require(
        not isinstance(command, (str, bytes))
        and bool(command)
        and all(
            isinstance(item, str)
            and item
            and "\x00" not in item
            and "\r" not in item
            and "\n" not in item
            for item in command
        ),
        "verification_command_invalid",
    )
    _require(
        isinstance(timeout_seconds, int)
        and not isinstance(timeout_seconds, bool)
        and timeout_seconds > 0,
        "verification_timeout_invalid",
    )
    before = inspect_static_output(root)
    try:
        outcome = runner.run(
            tuple(command),
            cwd=root,
            env=dict(environment),
            timeout_seconds=timeout_seconds,
        )
    except Exception as error:
        raise WebPrimitiveError("verification_failed") from error
    _require(outcome.returncode == 0, "verification_failed")
    after = inspect_static_output(root)
    _require(after.sha256 == before.sha256, "verification_mutated_output")
    return VerificationResult(after, outcome.returncode)


def _state_parent(path: Path) -> Path:
    root = _directory(path, "web_state_parent_invalid")
    return root


def _remove_state(path: Path, *, parent: Path) -> None:
    boundary = _state_parent(parent)
    candidate = Path(path)
    try:
        resolved_parent = candidate.parent.resolve(strict=True)
    except OSError as error:
        raise WebPrimitiveError("web_state_cleanup_failed") from error
    _require(resolved_parent == boundary, "web_state_cleanup_rejected")
    _require(candidate.name.startswith("ciw-pages-"), "web_state_cleanup_rejected")
    if not candidate.exists() and not candidate.is_symlink():
        return
    _require(not candidate.is_symlink(), "web_state_cleanup_rejected")
    try:
        shutil.rmtree(candidate)
    except OSError as error:
        raise WebPrimitiveError("web_state_cleanup_failed") from error
    _require(not candidate.exists(), "web_state_cleanup_failed")


def _pages_environment(
    environment: Mapping[str, str],
    *,
    account_id: str,
    workspace: Path,
    output_file: Path,
) -> dict[str, str]:
    token = environment.get("CLOUDFLARE_API_TOKEN", "")
    _require(
        isinstance(token, str)
        and bool(token)
        and all(character not in token for character in ("\x00", "\r", "\n")),
        "cloudflare_token_required",
    )
    values = {
        str(key): str(value)
        for key, value in environment.items()
        if key not in _AUTH_ENVIRONMENT_KEYS
        and key not in _CONTROL_ENVIRONMENT_KEYS
        and key != "CLOUDFLARE_ACCOUNT_ID"
    }
    values.update(
        {
            "CLOUDFLARE_API_TOKEN": token,
            "CLOUDFLARE_ACCOUNT_ID": account_id,
            "CLOUDFLARE_AUTH_USE_KEYRING": "false",
            "WRANGLER_SEND_METRICS": "false",
            "WRANGLER_SEND_ERROR_REPORTS": "false",
            "WRANGLER_LOG": "error",
            "WRANGLER_LOG_SANITIZE": "true",
            "FORCE_COLOR": "0",
            "WRANGLER_CACHE_DIR": str(workspace / "cache"),
            "WRANGLER_OUTPUT_FILE_PATH": str(output_file),
            "HOME": str(workspace / "home"),
            "XDG_CONFIG_HOME": str(workspace / "config"),
        }
    )
    return values


def _pages_record(path: Path) -> Mapping[str, object]:
    _require(path.exists() and path.is_file() and not path.is_symlink(), "cloudflare_output_missing")
    try:
        _require(path.stat().st_size <= _MAX_OUTPUT_RECORD_BYTES, "cloudflare_output_invalid")
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError) as error:
        raise WebPrimitiveError("cloudflare_output_invalid") from error
    records: list[Mapping[str, object]] = []
    for line in lines:
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as error:
            raise WebPrimitiveError("cloudflare_output_invalid") from error
        _require(isinstance(value, dict), "cloudflare_output_invalid")
        if value.get("type") == "pages-deploy":
            records.append(value)
    _require(bool(records), "cloudflare_output_missing")
    return records[-1]


def _record_text(record: Mapping[str, object], names: Sequence[str]) -> str | None:
    for name in names:
        value = record.get(name)
        if isinstance(value, str) and value:
            return value
    return None


def _deployment_url(record: Mapping[str, object]) -> str:
    url = _record_text(record, ("url", "deployment_url", "deploymentUrl"))
    _require(url is not None, "cloudflare_output_invalid")
    parsed = urlparse(url)
    _require(
        parsed.scheme == "https"
        and bool(parsed.netloc)
        and parsed.username is None
        and parsed.password is None,
        "cloudflare_output_invalid",
    )
    return url


def deploy_cloudflare_pages(
    directory: Path,
    runner: CommandRunner,
    *,
    project_name: str,
    account_id: str,
    branch: str,
    environment: Mapping[str, str],
    state_parent: Path,
    commit_hash: str | None = None,
    timeout_seconds: int = 1800,
) -> PagesDeploymentResult:
    """Deploy a validated static directory with isolated Wrangler state."""

    root = _directory(directory, "static_output_invalid")
    project = _safe_cli_value(project_name, "pages_project_invalid", maximum=128)
    _require(_PROJECT_NAME.fullmatch(project) is not None, "pages_project_invalid")
    _require(
        isinstance(account_id, str) and _ACCOUNT_ID.fullmatch(account_id) is not None,
        "cloudflare_account_invalid",
    )
    selected_branch = _safe_cli_value(branch, "pages_branch_invalid", maximum=255)
    if commit_hash is not None:
        _require(
            isinstance(commit_hash, str) and _COMMIT_SHA.fullmatch(commit_hash) is not None,
            "pages_commit_invalid",
        )
    _require(
        isinstance(timeout_seconds, int)
        and not isinstance(timeout_seconds, bool)
        and timeout_seconds > 0,
        "deployment_timeout_invalid",
    )

    before = inspect_static_output(root)
    parent = _state_parent(state_parent)
    workspace: Path | None = None
    try:
        workspace = Path(tempfile.mkdtemp(prefix="ciw-pages-", dir=parent))
        workspace.chmod(0o700)
        for name in ("cache", "home", "config"):
            child = workspace / name
            child.mkdir(mode=0o700)
    except OSError as error:
        if workspace is not None:
            try:
                _remove_state(workspace, parent=parent)
            except WebPrimitiveError:
                raise WebPrimitiveError(
                    "web_state_create_failed",
                    cleanup_failed=True,
                ) from error
        raise WebPrimitiveError("web_state_create_failed") from error

    assert workspace is not None
    primary_error: WebPrimitiveError | None = None
    result: PagesDeploymentResult | None = None
    output_file = workspace / "wrangler-output.ndjson"
    try:
        env = _pages_environment(
            environment,
            account_id=account_id.lower(),
            workspace=workspace,
            output_file=output_file,
        )
        argv: list[str] = [
            "wrangler",
            "pages",
            "deploy",
            str(root),
            "--project-name",
            project,
            "--branch",
            selected_branch,
            "--commit-dirty=false",
            "--no-bundle",
        ]
        if commit_hash is not None:
            argv.extend(("--commit-hash", commit_hash))
        try:
            outcome = runner.run(
                tuple(argv),
                cwd=workspace,
                env=env,
                timeout_seconds=timeout_seconds,
            )
        except Exception as error:
            raise WebPrimitiveError("cloudflare_deploy_failed") from error
        _require(outcome.returncode == 0, "cloudflare_deploy_failed")
        record = _pages_record(output_file)
        deployment_id = _record_text(record, ("deployment_id", "deploymentId", "id"))
        url = _deployment_url(record)
        after = inspect_static_output(root)
        _require(after.sha256 == before.sha256, "deployment_mutated_output")
        result = PagesDeploymentResult(
            project,
            selected_branch,
            deployment_id,
            url,
            after,
        )
    except WebPrimitiveError as error:
        primary_error = error
    finally:
        try:
            _remove_state(workspace, parent=parent)
        except WebPrimitiveError as cleanup_error:
            if primary_error is not None:
                raise WebPrimitiveError(primary_error.code, cleanup_failed=True) from cleanup_error
            raise

    if primary_error is not None:
        raise primary_error
    assert result is not None
    return result
