"""Engine-neutral public OCI build, inspection, publication, and read-back facade."""
from __future__ import annotations

from pathlib import Path
from typing import Mapping

from .oci_execution import inspect_layout as _inspect_layout
from .oci_execution_safe import execute_plan as _execute_plan
from .oci_publish_contract import (
    PublishPlan as OciPublishPlan,
    publish as _publish,
    read_back as _read_back,
)
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


def publish(
    plan: OciPublishPlan,
    environment: Mapping[str, str],
) -> dict[str, str]:
    """Publish or verify exact immutable OCI identities under the trusted event boundary."""

    return _publish(plan, environment)


def read_back(
    plan: OciPublishPlan,
    environment: Mapping[str, str],
) -> dict[str, str]:
    """Independently pull and verify exact registry bytes for one publication plan."""

    return _read_back(plan, environment)
