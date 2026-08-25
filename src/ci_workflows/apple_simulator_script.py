"""Internal exact simulator-lease argument handoff for Apple script tasks."""
from __future__ import annotations

import os
from pathlib import Path
import stat
import subprocess
from typing import Mapping, Sequence

from .apple_execution import CommandOutcome, CommandRunner, SubprocessCommandRunner
from .apple_types import AppleValidationError

SIMULATOR_UDID_TOKEN = "{ciw.apple.simulator_udid}"


def _stream_text(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _private_log_path(environment: Mapping[str, str]) -> Path | None:
    """Return one validated runner-local private log target when explicitly enabled."""

    raw = environment.get("CIW_PRIVATE_LOG_PATH", "")
    if not raw:
        return None
    runner_temp_raw = environment.get("RUNNER_TEMP", "")
    if not runner_temp_raw:
        raise AppleValidationError("private_log_path_invalid")
    candidate = Path(raw)
    if not candidate.is_absolute():
        raise AppleValidationError("private_log_path_invalid")
    try:
        runner_temp = Path(runner_temp_raw).resolve(strict=True)
        metadata = os.lstat(candidate)
        resolved = candidate.resolve(strict=True)
    except OSError:
        raise AppleValidationError("private_log_path_invalid") from None
    if (
        not runner_temp.is_dir()
        or runner_temp.is_symlink()
        or stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_mode & 0o077
        or runner_temp not in resolved.parents
    ):
        raise AppleValidationError("private_log_path_invalid")
    return resolved


def _append_private_output(path: Path | None, stdout: str | bytes | None, stderr: str | bytes | None) -> None:
    if path is None:
        return
    try:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(_stream_text(stdout))
            handle.write(_stream_text(stderr))
            handle.flush()
    except OSError:
        raise AppleValidationError("private_log_unavailable") from None


class SimulatorLeaseArgumentRunner:
    """Replace one reserved argument only after Central creates its simulator.

    The token is contract-owned data, never a caller input. The wrapper learns the
    exact UDID from Central's own successful ``simctl create`` command and replaces
    only an argv element that equals the reserved token. It performs no shell
    interpolation and grants no simulator-selection authority to product source.

    When the trusted private-CI boundary supplies ``CIW_PRIVATE_LOG_PATH``, every
    captured command stdout/stderr stream is appended in full to that validated
    runner-local file. No private command output is emitted to GitHub by this layer.
    """

    def __init__(self, delegate: CommandRunner | None = None) -> None:
        self._delegate = delegate or SubprocessCommandRunner()
        self._simulator_udid: str | None = None

    def _arguments(self, argv: Sequence[str]) -> tuple[str, ...]:
        if SIMULATOR_UDID_TOKEN not in argv:
            return tuple(argv)
        if self._simulator_udid is None:
            raise AppleValidationError("unsafe_destination")
        return tuple(
            self._simulator_udid if value == SIMULATOR_UDID_TOKEN else value
            for value in argv
        )

    def run(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        env: Mapping[str, str],
        timeout_seconds: int,
    ) -> CommandOutcome:
        resolved = self._arguments(argv)
        private_log = _private_log_path(env)
        try:
            outcome = self._delegate.run(
                resolved,
                cwd=cwd,
                env=env,
                timeout_seconds=timeout_seconds,
            )
        except subprocess.TimeoutExpired as error:
            _append_private_output(private_log, error.stdout, error.stderr)
            raise
        _append_private_output(private_log, outcome.stdout, outcome.stderr)
        if (
            outcome.returncode == 0
            and tuple(argv[:3]) == ("xcrun", "simctl", "create")
        ):
            if self._simulator_udid is not None:
                raise AppleValidationError("simulator_ambiguous")
            self._simulator_udid = outcome.stdout.strip()
        return outcome
