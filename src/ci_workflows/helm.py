"""Public façade for bounded Helm validation and OCI chart publication."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Mapping

from .helm_archive import finalize_validation_archive
from .helm_contract import HelmPlan, load_helm_contract, request_from_environment, require
from .helm_dependency_policy import resolve_validation_plan
from .helm_execution import validate_and_package
from .helm_manifest import remote_chart_manifest_digest
from .helm_policy import run_policy_hook
from .helm_registry import publish_and_read_back
from .helm_types import HelmPublicationResult, HelmValidationError, HelmValidationResult


def validate(
    source_root: Path,
    state_root: Path,
    plan: HelmPlan,
    admitted_sha: str,
    environment: Mapping[str, str],
) -> HelmValidationResult:
    """Validate one resolved product chart and emit its deterministic digest."""

    preliminary = validate_and_package(
        source_root,
        state_root,
        plan,
        admitted_sha,
        environment,
    )
    run_policy_hook(
        source_root,
        state_root,
        plan,
        admitted_sha,
        environment,
    )
    return finalize_validation_archive(preliminary, plan.product.chart_name)


def package(
    source_root: Path,
    state_root: Path,
    plan: HelmPlan,
    admitted_sha: str,
    environment: Mapping[str, str],
) -> HelmValidationResult:
    """Package via the same validation boundary; standalone packaging is forbidden."""

    return validate(source_root, state_root, plan, admitted_sha, environment)


def _remote_publication_result(
    source_root: Path,
    state_root: Path,
    plan: HelmPlan,
    validation: HelmValidationResult,
    environment: Mapping[str, str],
) -> HelmPublicationResult:
    require(plan.release_version is not None, "release_version_mismatch")
    publication = publish_and_read_back(
        source_root,
        state_root,
        plan,
        validation,
        environment,
    )
    chart_reference = f"{plan.product.registry_repository}/{plan.product.chart_name}"
    chart_digest = remote_chart_manifest_digest(
        source_root,
        state_root,
        chart_reference=chart_reference,
        release_version=plan.release_version,
        expected_package_sha256=validation.package_sha256,
        inherited=environment,
    )
    try:
        immutable = json.loads(publication.immutable_references_json)
    except json.JSONDecodeError as error:
        raise HelmValidationError("remote_manifest_invalid") from error
    require(isinstance(immutable, dict), "remote_manifest_invalid")
    immutable["chart_digest"] = chart_digest
    return HelmPublicationResult(
        chart_digest=chart_digest,
        immutable_references_json=json.dumps(
            immutable,
            sort_keys=True,
            separators=(",", ":"),
        ),
        package_sha256=publication.package_sha256,
        published=publication.published,
    )


def publish(
    source_root: Path,
    state_root: Path,
    plan: HelmPlan,
    validation: HelmValidationResult,
    environment: Mapping[str, str],
) -> HelmPublicationResult:
    """Publish on tag authority, then return exact remote manifest evidence."""

    return _remote_publication_result(
        source_root,
        state_root,
        plan,
        validation,
        environment,
    )


def read_back(
    source_root: Path,
    state_root: Path,
    plan: HelmPlan,
    validation: HelmValidationResult,
    environment: Mapping[str, str],
) -> HelmPublicationResult:
    """Verify existing immutable content without permitting publication."""

    replay_environment = dict(environment)
    replay_environment["INPUT_RELEASE_MODE"] = "existing-tag"
    return _remote_publication_result(
        source_root,
        state_root,
        plan,
        validation,
        replay_environment,
    )


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
