"""Shared named CI workflow function library.

The package exposes bounded domain functions plus the checked-in ``ciw`` command
registry. Domain modules retain their own decision authority; ``ciw`` only
normalizes typed dispatch, stable result/error projection, and documentation.
"""
from __future__ import annotations

from .ciw_types import CIWError, CIWResult

__all__ = (
    "CIWError",
    "CIWResult",
)
