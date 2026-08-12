"""Engine-neutral public OCI build and inspection facade."""
from __future__ import annotations

from pathlib import Path
from typing import Mapping

from .oci_execution import inspect_layout as _inspect_layout
from .oci_execution_safe import execute_plan as _execute_plan
from .oci_types import (
    OciBuildPlan,
    OciBuildResult,
    OciTarget,
    OciTargetResult,
)


def build(
    repository_root: Path,
    source_root: Path,
    plan: OciBuildPlan,
    environment: Mapping[str, str],
    secret_files: Mapping[str, Path] | None = None,
) -> OciBuildResult:
    """Build and validate one contract-owned OCI plan without publication."""

    return _execute_plan(
        repository_root,
        source_root,
        plan,
        environment,
        secret_files,
    )


def inspect(
    layout: Path,
    target: OciTarget,
    labels: Mapping[str, str],
) -> OciTargetResult:
    """Strictly inspect one local OCI layout against its target contract."""

    return _inspect_layout(layout, target, labels)
