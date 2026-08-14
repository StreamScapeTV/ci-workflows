"""Internal exact simulator-lease argument handoff for Apple script tasks."""
from __future__ import annotations

from pathlib import Path
from typing import Mapping, Sequence

from .apple_execution import CommandOutcome, CommandRunner, SubprocessCommandRunner
from .apple_types import AppleValidationError

SIMULATOR_UDID_TOKEN = "{ciw.apple.simulator_udid}"


class SimulatorLeaseArgumentRunner:
    """Replace one reserved argument only after Central creates its simulator.

    The token is contract-owned data, never a caller input.  The wrapper learns the
    exact UDID from Central's own successful ``simctl create`` command and replaces
    only an argv element that equals the reserved token.  It performs no shell
    interpolation and grants no simulator-selection authority to product source.
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
        outcome = self._delegate.run(
            resolved,
            cwd=cwd,
            env=env,
            timeout_seconds=timeout_seconds,
        )
        if (
            outcome.returncode == 0
            and tuple(argv[:3]) == ("xcrun", "simctl", "create")
        ):
            if self._simulator_udid is not None:
                raise AppleValidationError("simulator_ambiguous")
            self._simulator_udid = outcome.stdout.strip()
        return outcome
