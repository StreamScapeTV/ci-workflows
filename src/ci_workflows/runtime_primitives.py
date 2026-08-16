"""Small product-neutral runtime primitives for shared CI functions."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from .source_checkout import exact_checkout

_INPUT_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]{0,63}$")
_ENVIRONMENT_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
_OUTPUT_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
_PREFIX = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,31}$")
_ERROR_CODE = re.compile(r"^[a-z][a-z0-9_]{2,95}$")


class RuntimePrimitiveError(RuntimeError):
    """Fail closed with one stable non-secret runtime primitive code."""

    def __init__(self, code: str) -> None:
        if _ERROR_CODE.fullmatch(code) is None:
            raise ValueError("runtime primitive error code must be a safe identifier")
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class ProcessResult:
    """Captured result from one bounded subprocess execution."""

    returncode: int | None
    stdout: str
    stderr: str
    timed_out: bool

    @property
    def ok(self) -> bool:
        return not self.timed_out and self.returncode == 0


@dataclass(frozen=True, slots=True)
class CheckoutResult:
    """Stable exact-checkout result without credential material."""

    repository: str
    head_sha: str
    path: str
    fetch_depth: int
    verified: bool


def _text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def run_process(
    arguments: Sequence[str],
    *,
    cwd: Path,
    environment: Mapping[str, str],
    stdin: str = "",
    timeout_seconds: float | None = None,
) -> ProcessResult:
    """Run one process with explicit cwd, environment, stdin and timeout."""

    if not arguments or any(
        not isinstance(value, str) or not value or "\x00" in value
        for value in arguments
    ):
        raise RuntimePrimitiveError("process_arguments_invalid")
    if not isinstance(stdin, str) or "\x00" in stdin:
        raise RuntimePrimitiveError("process_stdin_invalid")
    if timeout_seconds is not None and (
        isinstance(timeout_seconds, bool)
        or not isinstance(timeout_seconds, (int, float))
        or timeout_seconds <= 0
    ):
        raise RuntimePrimitiveError("process_timeout_invalid")
    if not isinstance(environment, Mapping) or any(
        not isinstance(name, str)
        or _ENVIRONMENT_NAME.fullmatch(name) is None
        or not isinstance(value, str)
        or "\x00" in value
        for name, value in environment.items()
    ):
        raise RuntimePrimitiveError("process_environment_invalid")

    working_directory = Path(cwd)
    if (
        not working_directory.is_absolute()
        or working_directory.is_symlink()
        or not working_directory.is_dir()
    ):
        raise RuntimePrimitiveError("process_cwd_invalid")

    try:
        completed = subprocess.run(
            list(arguments),
            cwd=working_directory,
            env=dict(environment),
            input=stdin,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        return ProcessResult(
            returncode=None,
            stdout=_text(error.stdout),
            stderr=_text(error.stderr),
            timed_out=True,
        )
    except OSError as error:
        raise RuntimePrimitiveError("process_start_failed") from error

    return ProcessResult(
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
        timed_out=False,
    )


def normalize_input(
    name: str,
    *,
    argument: str | None = None,
    environment: Mapping[str, str],
    default: str = "",
    required: bool = False,
    choices: Sequence[str] | None = None,
) -> str:
    """Resolve a non-secret value from an argument or INPUT_* environment."""

    if not isinstance(name, str) or _INPUT_NAME.fullmatch(name) is None:
        raise RuntimePrimitiveError("input_name_invalid")
    if not isinstance(default, str):
        raise RuntimePrimitiveError("input_default_invalid")
    if choices is not None and (
        isinstance(choices, (str, bytes))
        or any(not isinstance(choice, str) or not choice for choice in choices)
    ):
        raise RuntimePrimitiveError("input_choices_invalid")

    key = "INPUT_" + name.upper().replace("-", "_")
    raw = argument if argument is not None else environment.get(key, default)
    if not isinstance(raw, str) or any(token in raw for token in ("\x00", "\r", "\n")):
        raise RuntimePrimitiveError("input_value_invalid")
    value = raw.strip()
    if required and not value:
        raise RuntimePrimitiveError("input_required")
    if choices is not None and value not in choices:
        raise RuntimePrimitiveError("input_choice_invalid")
    return value


def secret_environment(
    name: str,
    *,
    environment: Mapping[str, str],
    required: bool = True,
) -> str:
    """Read one fixed named secret environment variable without logging it."""

    if not isinstance(name, str) or _ENVIRONMENT_NAME.fullmatch(name) is None:
        raise RuntimePrimitiveError("secret_name_invalid")
    value = environment.get(name, "")
    if not isinstance(value, str) or "\x00" in value:
        raise RuntimePrimitiveError("secret_value_invalid")
    if required and not value:
        raise RuntimePrimitiveError("secret_required")
    return value


def _real_directory(path: Path, *, code: str) -> Path:
    candidate = Path(path)
    if not candidate.is_absolute() or candidate.is_symlink() or not candidate.is_dir():
        raise RuntimePrimitiveError(code)
    try:
        return candidate.resolve(strict=True)
    except OSError as error:
        raise RuntimePrimitiveError(code) from error


def create_temporary_workspace(parent: Path, *, prefix: str = "ciw") -> Path:
    """Create one mode-0700 temporary workspace beneath a reviewed parent."""

    root = _real_directory(parent, code="workspace_parent_invalid")
    if not isinstance(prefix, str) or _PREFIX.fullmatch(prefix) is None:
        raise RuntimePrimitiveError("workspace_prefix_invalid")
    try:
        created = Path(tempfile.mkdtemp(prefix=f"{prefix}-", dir=root))
        created.chmod(0o700)
    except OSError as error:
        raise RuntimePrimitiveError("workspace_create_failed") from error
    if created.parent != root or created.is_symlink():
        raise RuntimePrimitiveError("workspace_create_failed")
    return created


def _bounded_target(path: Path, *, root: Path) -> Path:
    bounded_root = _real_directory(root, code="cleanup_root_invalid")
    candidate = Path(path)
    if not candidate.is_absolute():
        raise RuntimePrimitiveError("cleanup_target_outside_root")
    candidate = Path(os.path.normpath(os.fspath(candidate)))
    try:
        relative = candidate.relative_to(bounded_root)
    except ValueError as error:
        raise RuntimePrimitiveError("cleanup_target_outside_root") from error
    if not relative.parts:
        raise RuntimePrimitiveError("cleanup_target_is_root")

    cursor = bounded_root
    for part in relative.parts[:-1]:
        cursor = cursor / part
        if cursor.is_symlink():
            raise RuntimePrimitiveError("cleanup_symlink_ancestor")
    return candidate


def finalize_temporary_path(path: Path, *, root: Path) -> bool:
    """Idempotently remove a file, symlink or directory beneath a reviewed root."""

    target = _bounded_target(path, root=root)
    try:
        if target.is_symlink():
            target.unlink()
            return True
        if target.is_dir():
            shutil.rmtree(target)
            return True
        if target.exists():
            target.unlink()
            return True
        return False
    except OSError as error:
        raise RuntimePrimitiveError("cleanup_failed") from error


def finalize_temporary_paths(paths: Sequence[Path], *, root: Path) -> int:
    """Finalize bounded temporary/auth paths and return the removal count."""

    removed = 0
    for path in reversed(tuple(paths)):
        removed += int(finalize_temporary_path(path, root=root))
    return removed


def checkout_exact_repository(
    repository: str,
    source_sha: str,
    *,
    workspace: Path,
    path: str = "source",
    fetch_depth: int = 1,
    token_environment: str | None = None,
    environment: Mapping[str, str],
) -> CheckoutResult:
    """Check out one exact GitHub repository SHA using an optional named token."""

    token = (
        secret_environment(
            token_environment,
            environment=environment,
            required=False,
        )
        if token_environment is not None
        else ""
    )
    values = exact_checkout(
        repository=repository,
        admitted_sha=source_sha,
        path=path,
        fetch_depth=fetch_depth,
        token=token,
        workspace=workspace,
    )
    return CheckoutResult(
        repository=str(values["repository"]),
        head_sha=str(values["head_sha"]),
        path=str(values["path"]),
        fetch_depth=int(values["fetch_depth"]),
        verified=str(values["verified"]).lower() == "true",
    )


def canonical_json(value: Any) -> str:
    """Serialize JSON deterministically for command outputs and evidence."""

    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except (TypeError, ValueError) as error:
        raise RuntimePrimitiveError("json_serialization_failed") from error


def github_output_values(values: Mapping[str, Any]) -> dict[str, str]:
    """Normalize scalar and structured values into single-line GitHub outputs."""

    if not isinstance(values, Mapping):
        raise RuntimePrimitiveError("github_outputs_invalid")
    result: dict[str, str] = {}
    for name, value in values.items():
        if not isinstance(name, str) or _OUTPUT_NAME.fullmatch(name) is None:
            raise RuntimePrimitiveError("github_output_name_invalid")
        serialized = value if isinstance(value, str) else canonical_json(value)
        if "\r" in serialized or "\n" in serialized:
            raise RuntimePrimitiveError("github_output_value_invalid")
        result[name] = serialized
    return result


def write_github_outputs(path: Path, values: Mapping[str, Any]) -> dict[str, str]:
    """Append normalized outputs to a GitHub command file and return them."""

    normalized = github_output_values(values)
    command_file = Path(path)
    if not command_file.is_absolute() or command_file.is_symlink():
        raise RuntimePrimitiveError("github_output_path_invalid")
    try:
        with command_file.open("a", encoding="utf-8", newline="\n") as handle:
            for name, value in normalized.items():
                handle.write(f"{name}={value}\n")
    except OSError as error:
        raise RuntimePrimitiveError("github_output_write_failed") from error
    return normalized
