"""Compatibility facade for the original physical-device component API.

The public reusable workflow now routes through typed ``ciw device`` commands,
but issue #14 retains these three named component helpers for callers and tests
that imported the original bounded facade during development.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .device_cleanup import assert_zero_device_residue, cleanup_device_state
from .device_lifecycle import execute_device_plan


def lock(*, adapter: Any, plan: Any, selected: Any, now: int) -> Any:
    """Acquire one lock through the supplied reviewed adapter."""

    return adapter.acquire(plan=plan, selected=selected, now=now)


def validate(**kwargs: Any) -> Any:
    """Delegate bounded lifecycle execution to the typed implementation."""

    return execute_device_plan(**kwargs)


def cleanup(*, state_root: Path) -> None:
    """Remove registered device state and fail closed on residue."""

    cleanup_device_state(state_root)
    assert_zero_device_residue(state_root)
