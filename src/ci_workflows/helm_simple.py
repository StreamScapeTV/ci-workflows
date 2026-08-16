"""Simple Helm validation/package/publish path for the core reusable workflow.

This module intentionally keeps product release policy in the caller.  Central owns
only common Helm mechanics: exact-source validation, lint/render/package, normal OCI
registry authentication/push, and cleanup through the existing Helm state boundary.
The older immutable/read-back helpers remain available to legacy callers but are not
required by this core path.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Mapping

from .helm_contract import SEMVER, require, validate_chart_layout
from .helm_execution import (
    _chart_version,
    _copy_chart_for_build,
    _registry_host,
    _run,
    _runtime_environment,
    _verify_no_kubernetes_authority,
    normalize_chart_archive,
    verify_exact_source,
    verify_helm_toolchain,
)
from .helm_types import HelmPlan, HelmPublicationResult, HelmValidationResult

_IMAGE_LINE = re.compile(r"^\s*(?:-\s*)?image:\s*(.+?)\s*$")


def _reject_latest_images(rendered: str) -> None:
    """Keep one generic image hygiene rule without owning product digest policy."""

    for line in rendered.splitlines():
        match = _IMAGE_LINE.match(line)
        if match is None:
            continue
        value = match.group(1).strip().strip("\"'")
        lowered = value.casefold()
        require(
            lowered != "latest"
            and not lowered.endswith(":latest")
            and ":latest@" not in lowered,
            "image_reference_mismatch",
        )


def validate_and_package(
    source_root: Path,
    state_root: Path,
    plan: HelmPlan,
    admitted_sha: str,
    inherited: Mapping[str, str],
) -> HelmValidationResult:
    """Lint, render, and package one caller-owned product chart."""

    environment = _runtime_environment(inherited, state_root)
    verify_exact_source(source_root, admitted_sha, environment)
    chart_root, values_path = validate_chart_layout(source_root, plan)
    verify_helm_toolchain(source_root, environment)
    source_version = _chart_version(chart_root)
    work_chart, work_values = _copy_chart_for_build(
        chart_root,
        values_path,
        state_root,
        plan.product.chart_name,
    )

    if plan.product.locked_dependencies:
        _run(
            ["helm", "dependency", "build", str(work_chart)],
            cwd=source_root,
            environment=environment,
            timeout=120,
            code="dependency_build_failed",
        )

    _run(
        ["helm", "lint", "--strict", str(work_chart), "--values", str(work_values)],
        cwd=source_root,
        environment=environment,
        timeout=120,
        code="lint_failed",
    )
    rendered = _run(
        [
            "helm",
            "template",
            plan.product.chart_name,
            str(work_chart),
            "--include-crds",
            "--values",
            str(work_values),
        ],
        cwd=source_root,
        environment=environment,
        timeout=120,
        code="template_failed",
    ).stdout
    _reject_latest_images(rendered)

    package_version = plan.release_version or source_version
    output_root = state_root / "helm-validation" / "package"
    output_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    package_args = [
        "helm",
        "package",
        str(work_chart),
        "--destination",
        str(output_root),
    ]
    if plan.release_version is not None:
        package_args.extend(
            ["--version", plan.release_version, "--app-version", plan.release_version]
        )
    _run(
        package_args,
        cwd=source_root,
        environment=environment,
        timeout=120,
        code="package_failed",
    )
    candidate = output_root / f"{plan.product.chart_name}-{package_version}.tgz"
    require(candidate.is_file() and not candidate.is_symlink(), "package_failed")
    normalized = output_root / "normalized.tgz"
    package_sha256 = normalize_chart_archive(
        candidate,
        normalized,
        plan.product.chart_name,
    )
    candidate.unlink()
    verify_exact_source(source_root, admitted_sha, environment)
    return HelmValidationResult(
        chart_digest=f"sha256:{package_sha256}",
        package_sha256=package_sha256,
        summary=json.dumps(
            {
                "chart_name": plan.product.chart_name,
                "package_sha256": package_sha256,
                "release_version": package_version,
                "status": "success",
                "values_profile": plan.values_profile,
            },
            sort_keys=True,
            separators=(",", ":"),
        ),
        archive_path=normalized,
    )


def publish(
    source_root: Path,
    state_root: Path,
    plan: HelmPlan,
    validation: HelmValidationResult,
    inherited: Mapping[str, str],
) -> HelmPublicationResult:
    """Authenticate and push one already-validated chart without mandatory read-back."""

    require(
        plan.release_version is not None
        and SEMVER.fullmatch(plan.release_version) is not None,
        "release_version_mismatch",
    )
    _verify_no_kubernetes_authority(inherited)
    username = inherited.get("INPUT_REGISTRY_USERNAME", "")
    token = inherited.get("INPUT_REGISTRY_TOKEN", "")
    require(bool(username) and bool(token), "registry_auth_missing")

    environment = _runtime_environment(inherited, state_root)
    host = _registry_host(plan.product.registry_repository)
    _run(
        [
            "helm",
            "registry",
            "login",
            host,
            "--username",
            username,
            "--password-stdin",
        ],
        cwd=source_root,
        environment=environment,
        timeout=60,
        code="registry_auth_failed",
        stdin=f"{token}\n",
    )
    _run(
        [
            "helm",
            "push",
            str(validation.archive_path),
            plan.product.registry_repository,
        ],
        cwd=source_root,
        environment=environment,
        timeout=120,
        code="publication_failed",
    )

    chart_reference = (
        f"{plan.product.registry_repository}/{plan.product.chart_name}:"
        f"{plan.release_version}"
    )
    references = json.dumps(
        {
            "chart": chart_reference,
            "chart_digest": validation.chart_digest,
            "package_sha256": validation.package_sha256,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return HelmPublicationResult(
        chart_digest=validation.chart_digest,
        immutable_references_json=references,
        package_sha256=validation.package_sha256,
        published=True,
    )
