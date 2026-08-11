"""Public façade for bounded Helm validation and OCI chart publication."""
from __future__ import annotations

from pathlib import Path
from typing import Mapping

from .helm_contract import HelmPlan, load_helm_contract, request_from_environment, resolve_validation_plan
from .helm_execution import publish_and_read_back, validate_and_package
from .helm_types import HelmPublicationResult, HelmValidationError, HelmValidationResult


def validate(
    source_root: Path,
    state_root: Path,
    plan: HelmPlan,
    admitted_sha: str,
    environment: Mapping[str, str],
) -> HelmValidationResult:
    """Validate one resolved product chart and emit its deterministic digest."""

    return validate_and_package(source_root, state_root, plan, admitted_sha, environment)


def package(
    source_root: Path,
    state_root: Path,
    plan: HelmPlan,
    admitted_sha: str,
    environment: Mapping[str, str],
) -> HelmValidationResult:
    """Package via the same validation boundary; standalone packaging is forbidden."""

    return validate(source_root, state_root, plan, admitted_sha, environment)


def publish(
    source_root: Path,
    state_root: Path,
    plan: HelmPlan,
    validation: HelmValidationResult,
    environment: Mapping[str, str],
) -> HelmPublicationResult:
    """Perform the idempotent push and mandatory OCI pull verification."""

    return publish_and_read_back(source_root, state_root, plan, validation, environment)


def read_back(
    source_root: Path,
    state_root: Path,
    plan: HelmPlan,
    validation: HelmValidationResult,
    environment: Mapping[str, str],
) -> HelmPublicationResult:
    """The publication primitive always includes read-back; no weaker path exists."""

    return publish(source_root, state_root, plan, validation, environment)

__all__ = [
    "HelmPublicationResult",
    "HelmValidationError",
    "HelmValidationResult",
    "load_helm_contract",
    "publish_and_read_back",
    "package",
    "publish",
    "read_back",
    "request_from_environment",
    "resolve_validation_plan",
    "validate_and_package",
    "validate",
]
