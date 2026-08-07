"""Typed result, error, context, and GitHub command-file primitives for ``ciw``."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, TextIO

_IDENTIFIER = re.compile(r"^[a-z][a-z0-9-]{1,63}$")
_ERROR_CODE = re.compile(r"^[a-z][a-z0-9_]{2,95}$")
_COMMAND_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class CIWError(RuntimeError):
    """Fail-closed projected error containing only a stable non-secret code."""

    def __init__(self, domain: str, code: str, *, exit_code: int = 2) -> None:
        if _IDENTIFIER.fullmatch(domain) is None:
            raise ValueError("ciw error domain must be a safe identifier")
        if _ERROR_CODE.fullmatch(code) is None:
            raise ValueError("ciw error code must be a safe identifier")
        if not isinstance(exit_code, int) or not 1 <= exit_code <= 125:
            raise ValueError("ciw exit code must be between 1 and 125")
        self.domain = domain
        self.code = code
        self.exit_code = exit_code
        super().__init__(code)


@dataclass(frozen=True)
class CIWContext:
    """Immutable command execution context."""

    root: Path
    environment: Mapping[str, str]
    stdout: TextIO
    stderr: TextIO


def _validated_values(values: Mapping[str, str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for raw_name, raw_value in values.items():
        if not isinstance(raw_name, str) or _COMMAND_NAME.fullmatch(raw_name) is None:
            raise CIWError("ciw", "invalid_github_command_name")
        if not isinstance(raw_value, str) or "\n" in raw_value or "\r" in raw_value:
            raise CIWError("ciw", "invalid_github_command_value")
        result[raw_name] = raw_value
    return result


def write_command_file(path: Path, values: Mapping[str, str]) -> None:
    """Append validated single-line values to one GitHub command file."""

    validated = _validated_values(values)
    try:
        with path.open("a", encoding="utf-8", newline="\n") as handle:
            for name, value in validated.items():
                handle.write(f"{name}={value}\n")
    except OSError as error:
        raise CIWError("ciw", "github_command_file_unavailable") from error


@dataclass(frozen=True)
class CIWResult:
    """Stable typed result emitted by one registered command."""

    domain: str
    operation: str
    outputs: Mapping[str, str] = field(default_factory=dict)
    environment: Mapping[str, str] = field(default_factory=dict)
    summary: str | None = None
    stdout_text: str | None = None

    def __post_init__(self) -> None:
        if _IDENTIFIER.fullmatch(self.domain) is None:
            raise ValueError("ciw result domain must be a safe identifier")
        if _IDENTIFIER.fullmatch(self.operation) is None:
            raise ValueError("ciw result operation must be a safe identifier")
        object.__setattr__(self, "outputs", _validated_values(self.outputs))
        object.__setattr__(self, "environment", _validated_values(self.environment))
        if self.summary is not None and not isinstance(self.summary, str):
            raise TypeError("ciw summary must be text")
        if self.stdout_text is not None and not isinstance(self.stdout_text, str):
            raise TypeError("ciw stdout must be text")

    def emit(self, context: CIWContext) -> None:
        output_path = context.environment.get("GITHUB_OUTPUT", "")
        if self.outputs and output_path:
            write_command_file(Path(output_path), self.outputs)
        environment_path = context.environment.get("GITHUB_ENV", "")
        if self.environment and environment_path:
            write_command_file(Path(environment_path), self.environment)
        summary_path = context.environment.get("GITHUB_STEP_SUMMARY", "")
        if self.summary is not None and summary_path:
            try:
                with Path(summary_path).open("a", encoding="utf-8", newline="\n") as handle:
                    handle.write(self.summary)
                    if self.summary and not self.summary.endswith("\n"):
                        handle.write("\n")
            except OSError as error:
                raise CIWError("ciw", "github_summary_unavailable") from error
        if self.stdout_text is not None:
            context.stdout.write(self.stdout_text)
            if self.stdout_text and not self.stdout_text.endswith("\n"):
                context.stdout.write("\n")


def required_environment(
    environment: Mapping[str, str],
    name: str,
    *,
    domain: str,
    code: str | None = None,
) -> str:
    value = environment.get(name, "")
    if not value:
        raise CIWError(domain, code or f"{name.lower()}_required")
    return value


def input_value(
    environment: Mapping[str, str],
    name: str,
    default: str = "",
) -> str:
    key = "INPUT_" + name.upper().replace("-", "_")
    return environment.get(key, default).strip()


def project_error(error: BaseException, *, domain: str) -> CIWError:
    """Project domain failures without leaking arbitrary exception text."""

    if isinstance(error, CIWError):
        return error
    instruction = getattr(error, "instruction", None)
    if isinstance(instruction, str) and _ERROR_CODE.fullmatch(instruction):
        return CIWError(domain, instruction)
    code = getattr(error, "code", None)
    if isinstance(code, str) and _ERROR_CODE.fullmatch(code):
        return CIWError(domain, code)
    return CIWError(domain, "ciw_unexpected_failure")


def default_context(root: Path, *, stdout: TextIO, stderr: TextIO) -> CIWContext:
    return CIWContext(
        root=root.resolve(),
        environment=dict(os.environ),
        stdout=stdout,
        stderr=stderr,
    )
