"""Simple Helm validation/package/publish path for the core reusable workflow.

The product repository owns chart layout, values profiles, dependency locks,
registry destination, and release policy. Central owns only common Helm mechanics:
exact-source validation, lint/render/package, normal OCI authentication/push, and
terminal cleanup. The older immutable/read-back helpers remain available to legacy
callers but are not required by this core path.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlsplit

from .helm_contract import (
    NAME,
    PRODUCT_MANIFEST_PATH,
    SEMVER,
    _json,
    _locked_dependencies,
    bounded_path,
    require,
    safe_relative,
    validate_chart_layout,
)
from .helm_execution import (
    _chart_version,
    _copy_chart_for_build,
    _run,
    _runtime_environment,
    _verify_no_kubernetes_authority,
    normalize_chart_archive,
    verify_exact_source,
    verify_helm_toolchain,
)
from .helm_types import (
    HelmPlan,
    HelmProduct,
    HelmPublicationResult,
    HelmRequest,
    HelmValidationResult,
)

_IMAGE_LINE = re.compile(r"^\s*(?:-\s*)?image:\s*(.+?)\s*$")
_OCI_REPOSITORY = re.compile(r"^oci://[a-z0-9.-]+(?::[0-9]{1,5})?/[A-Za-z0-9._/-]+$")


def _string_list(value: Any, code: str) -> list[str]:
    require(
        isinstance(value, list) and all(isinstance(item, str) for item in value),
        code,
    )
    return list(value)


def resolve_plan(
    source_root: Path,
    contract: Mapping[str, Any],
    request: HelmRequest,
) -> HelmPlan:
    """Resolve the product-owned chart manifest with only repository admission central."""

    products = contract.get("products")
    require(isinstance(products, Mapping), "invalid_contract")
    registration = products.get(request.product_id)
    require(isinstance(registration, Mapping), "unsupported_product")
    require(
        registration.get("repository") == request.repository,
        "repository_rejected",
    )

    manifest_path = bounded_path(
        source_root,
        PRODUCT_MANIFEST_PATH.as_posix(),
        "invalid_product_manifest",
    )
    require(
        manifest_path.is_file() and not manifest_path.is_symlink(),
        "invalid_product_manifest",
    )
    data = _json(manifest_path, "invalid_product_manifest")
    required = {
        "schema_version",
        "product_id",
        "repository",
        "chart_name",
        "chart_root",
        "values_profiles",
        "policy_path",
        "registry_repository",
        "locked_dependencies",
        "required_image_references",
        "upstream_assets",
    }
    require(
        set(data) == required and data.get("schema_version") == 1,
        "invalid_product_manifest",
    )
    require(data.get("product_id") == request.product_id, "invalid_product_manifest")
    require(data.get("repository") == request.repository, "invalid_product_manifest")

    chart_name = data.get("chart_name")
    require(
        isinstance(chart_name, str) and NAME.fullmatch(chart_name) is not None,
        "invalid_product_manifest",
    )
    chart_root = safe_relative(data.get("chart_root"), "invalid_product_manifest")

    profiles = data.get("values_profiles")
    require(isinstance(profiles, Mapping) and profiles, "invalid_product_manifest")
    parsed_profiles: dict[str, str] = {}
    for name, relative in profiles.items():
        require(
            isinstance(name, str) and NAME.fullmatch(name) is not None,
            "invalid_product_manifest",
        )
        parsed_profiles[name] = safe_relative(relative, "invalid_product_manifest")
    require(list(parsed_profiles) == sorted(parsed_profiles), "invalid_product_manifest")

    policy_path = data.get("policy_path")
    require(
        policy_path is None or isinstance(policy_path, str),
        "invalid_product_manifest",
    )
    if policy_path is not None:
        policy_path = safe_relative(policy_path, "invalid_product_manifest")
    require(request.policy_path == policy_path, "policy_path_rejected")

    registry_repository = data.get("registry_repository")
    require(
        isinstance(registry_repository, str)
        and _OCI_REPOSITORY.fullmatch(registry_repository) is not None,
        "invalid_product_manifest",
    )
    _string_list(data.get("required_image_references"), "invalid_product_manifest")
    require(isinstance(data.get("upstream_assets"), list), "invalid_product_manifest")

    values_profile = request.values_profile or "default"
    require(values_profile in parsed_profiles, "values_profile_rejected")
    product = HelmProduct(
        product_id=request.product_id,
        repository=request.repository,
        chart_name=chart_name,
        chart_root=chart_root,
        values_profiles=parsed_profiles,
        policy_path=policy_path,
        registry_repository=registry_repository,
        locked_dependencies=_locked_dependencies(data.get("locked_dependencies")),
        required_image_references=(),
    )
    return HelmPlan(
        product=product,
        release_version=request.release_version,
        values_profile=values_profile,
        values_path=parsed_profiles[values_profile],
        policy_path=policy_path,
    )


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


def _registry_host(repository: str) -> str:
    require(
        isinstance(repository, str)
        and _OCI_REPOSITORY.fullmatch(repository) is not None,
        "registry_rejected",
    )
    parsed = urlsplit(repository.removeprefix("oci://"))
    host = parsed.path.split("/", 1)[0]
    require(bool(host) and "@" not in host, "registry_rejected")
    return host


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
