"""Strict product-manifest and Helm chart admission for issue #18."""
from __future__ import annotations

import json
import re
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

import yaml

from .helm_types import HelmPlan, HelmProduct, HelmRequest, HelmValidationError


CONTRACT_PATH = Path("contracts/helm-validation.json")
PUBLICATION_CONTRACT_PATH = Path("contracts/helm-publication.json")
PRODUCT_MANIFEST_PATH = Path(".streamscape/helm-product.json")
FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
SEMVER = re.compile(
    r"^(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)
NAME = re.compile(r"^[a-z][a-z0-9-]{1,63}$")
DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
IMMUTABLE_IMAGE_REFERENCE = re.compile(r"^[a-z0-9._/-]+@sha256:[0-9a-f]{64}$")
OCI_REPOSITORY = re.compile(r"^oci://[a-z0-9.-]+/[a-z0-9._/-]+$")


def require(condition: bool, code: str) -> None:
    if not condition:
        raise HelmValidationError(code)


def safe_relative(value: Any, code: str = "invalid_input", *, allow_dot: bool = False) -> str:
    require(isinstance(value, str), code)
    candidate = value.strip()
    if allow_dot and candidate == ".":
        return "."
    path = PurePosixPath(candidate)
    require(
        bool(candidate)
        and not path.is_absolute()
        and ".." not in path.parts
        and "\\" not in candidate
        and all(part not in {"", "."} for part in path.parts),
        code,
    )
    return path.as_posix()


def bounded_path(root: Path, relative: str, code: str = "invalid_input") -> Path:
    """Return an in-root ordinary path without following a symlink component."""

    resolved_root = root.resolve()
    require(root.is_dir() and not root.is_symlink(), code)
    candidate = safe_relative(relative, code, allow_dot=True)
    lexical = resolved_root if candidate == "." else resolved_root.joinpath(*PurePosixPath(candidate).parts)
    current = resolved_root
    for part in PurePosixPath(candidate).parts if candidate != "." else ():
        current /= part
        require(not current.is_symlink(), code)
    resolved = lexical.resolve(strict=False)
    require(resolved == resolved_root or resolved_root in resolved.parents, code)
    return lexical


def _json(path: Path, code: str) -> Mapping[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise HelmValidationError(code) from error
    require(isinstance(value, Mapping), code)
    return value


def _yaml(path: Path, code: str) -> Mapping[str, Any]:
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as error:
        raise HelmValidationError(code) from error
    require(isinstance(value, Mapping), code)
    return value


def load_helm_contract(root: Path) -> Mapping[str, Any]:
    payload = _json(root / CONTRACT_PATH, "invalid_contract")
    require(payload.get("schema_version") == 1, "invalid_contract")
    require(payload.get("runner_profile") == "portable", "invalid_contract")
    require(payload.get("workspace_profile") == "minimal", "invalid_contract")
    require(payload.get("artifact_policy") == "zero-default", "invalid_contract")
    require(payload.get("product_manifest_path") == PRODUCT_MANIFEST_PATH.as_posix(), "invalid_contract")
    products = payload.get("products")
    require(isinstance(products, Mapping) and len(products) == 3, "invalid_contract")
    expected = {"iptv-backend-chart", "agent-state-chart", "flux-runner-chart-assets"}
    require(set(products) == expected, "invalid_contract")
    for product_id, value in products.items():
        require(isinstance(value, Mapping), "invalid_contract")
        require(value.get("repository") in {
            "StreamScapeTV/iptv-backend",
            "StreamScapeTV/agent-state",
            "StreamScapeTV/flux",
        }, "invalid_contract")
        require(isinstance(value.get("chart_name"), str) and NAME.fullmatch(value["chart_name"]) is not None, "invalid_contract")
        repository = value.get("registry_repository")
        require(isinstance(repository, str) and OCI_REPOSITORY.fullmatch(repository) is not None, "invalid_contract")
        require(product_id in expected, "invalid_contract")
    return payload


def load_helm_publication_contract(root: Path) -> Mapping[str, Any]:
    payload = _json(root / PUBLICATION_CONTRACT_PATH, "invalid_publication_contract")
    require(payload.get("schema_version") == 1, "invalid_publication_contract")
    require(payload.get("registry_host") == "git.faruqi.dev", "invalid_publication_contract")
    require(payload.get("registry_namespace") == "mimranfaruqi/helm-charts", "invalid_publication_contract")
    require(payload.get("version_policy") == "immutable-semver-no-latest", "invalid_publication_contract")
    require(payload.get("idempotency") == "pull-compare-before-push", "invalid_publication_contract")
    require(payload.get("read_back") == "required-oci-pull-and-normalized-sha256", "invalid_publication_contract")
    require(payload.get("named_secrets") == ["registry_username", "registry_token"], "invalid_publication_contract")
    require(payload.get("forbidden_capabilities") == ["kubernetes", "sops-decryption", "latest"], "invalid_publication_contract")
    return payload


def request_from_environment(environment: Mapping[str, str]) -> HelmRequest:
    value = lambda name: environment.get(f"INPUT_{name}", "").strip()
    release_version = value("RELEASE_VERSION") or None
    values_profile = value("VALUES_PROFILE") or None
    policy_path = value("POLICY_PATH") or None
    artifact_exception_id = value("ARTIFACT_EXCEPTION_ID") or None
    request = HelmRequest(
        repository=environment.get("GITHUB_REPOSITORY", "").strip(),
        admitted_sha=value("ADMITTED_SHA"),
        product_id=value("PRODUCT_ID"),
        release_version=release_version,
        values_profile=values_profile,
        policy_path=policy_path,
        artifact_exception_id=artifact_exception_id,
        source_trust=environment.get("INPUT_SOURCE_TRUST", "trusted-exact").strip(),
    )
    require(FULL_SHA.fullmatch(request.admitted_sha) is not None, "invalid_input")
    require(NAME.fullmatch(request.product_id) is not None, "invalid_input")
    require(request.source_trust in {"trusted-pr", "trusted-exact"}, "invalid_input")
    if request.release_version is not None:
        require(SEMVER.fullmatch(request.release_version) is not None, "invalid_input")
    if request.values_profile is not None:
        require(NAME.fullmatch(request.values_profile) is not None, "invalid_input")
    if request.policy_path is not None:
        safe_relative(request.policy_path, "invalid_input")
    # Routine package diagnostics are never retained by this workflow family.
    require(request.artifact_exception_id is None, "artifact_policy_failed")
    return request


def _locked_dependencies(value: Any) -> tuple[tuple[str, str, str], ...]:
    require(isinstance(value, list), "invalid_product_manifest")
    rows: list[tuple[str, str, str]] = []
    for item in value:
        require(isinstance(item, Mapping) and set(item) == {"name", "repository", "version"}, "invalid_product_manifest")
        name, version, repository = item["name"], item["version"], item["repository"]
        require(isinstance(name, str) and NAME.fullmatch(name) is not None, "invalid_product_manifest")
        require(isinstance(version, str) and SEMVER.fullmatch(version) is not None, "invalid_product_manifest")
        require(isinstance(repository, str) and OCI_REPOSITORY.fullmatch(repository) is not None, "invalid_product_manifest")
        rows.append((name, version, repository))
    require(rows == sorted(rows), "invalid_product_manifest")
    require(len(rows) == len(set(rows)), "invalid_product_manifest")
    return tuple(rows)


def load_product_manifest(
    source_root: Path,
    contract: Mapping[str, Any],
    request: HelmRequest,
) -> HelmProduct:
    products = contract["products"]
    template = products.get(request.product_id)
    require(isinstance(template, Mapping), "unsupported_product")
    require(request.repository == template["repository"], "repository_rejected")
    manifest_path = bounded_path(source_root, PRODUCT_MANIFEST_PATH.as_posix(), "invalid_product_manifest")
    require(manifest_path.is_file() and not manifest_path.is_symlink(), "invalid_product_manifest")
    data = _json(manifest_path, "invalid_product_manifest")
    required = {
        "schema_version", "product_id", "repository", "chart_name", "chart_root",
        "values_profiles", "policy_path", "registry_repository", "locked_dependencies",
        "required_image_references",
    }
    require(set(data) == required and data.get("schema_version") == 1, "invalid_product_manifest")
    require(data.get("product_id") == request.product_id, "invalid_product_manifest")
    for key in ("repository", "chart_name", "registry_repository"):
        require(data.get(key) == template.get(key), "invalid_product_manifest")
    chart_root = safe_relative(data.get("chart_root"), "invalid_product_manifest")
    policy = data.get("policy_path")
    require(policy is None or isinstance(policy, str), "invalid_product_manifest")
    if policy is not None:
        policy = safe_relative(policy, "invalid_product_manifest")
    profiles = data.get("values_profiles")
    require(isinstance(profiles, Mapping) and profiles, "invalid_product_manifest")
    parsed_profiles: dict[str, str] = {}
    for name, path in profiles.items():
        require(isinstance(name, str) and NAME.fullmatch(name) is not None, "invalid_product_manifest")
        parsed_profiles[name] = safe_relative(path, "invalid_product_manifest")
    require(list(parsed_profiles) == sorted(parsed_profiles), "invalid_product_manifest")
    images = data.get("required_image_references")
    require(
        isinstance(images, list)
        and all(
            isinstance(item, str)
            and IMMUTABLE_IMAGE_REFERENCE.fullmatch(item) is not None
            for item in images
        ),
        "invalid_product_manifest",
    )
    require(images == sorted(set(images)), "invalid_product_manifest")
    return HelmProduct(
        product_id=request.product_id,
        repository=request.repository,
        chart_name=str(data["chart_name"]),
        chart_root=chart_root,
        values_profiles=parsed_profiles,
        policy_path=policy,
        registry_repository=str(data["registry_repository"]),
        locked_dependencies=_locked_dependencies(data["locked_dependencies"]),
        required_image_references=tuple(images),
    )


def resolve_validation_plan(
    source_root: Path,
    contract: Mapping[str, Any],
    request: HelmRequest,
) -> HelmPlan:
    product = load_product_manifest(source_root, contract, request)
    values_profile = request.values_profile or "default"
    require(values_profile in product.values_profiles, "values_profile_rejected")
    require(request.policy_path == product.policy_path, "policy_path_rejected")
    return HelmPlan(
        product=product,
        release_version=request.release_version,
        values_profile=values_profile,
        values_path=product.values_profiles[values_profile],
        policy_path=product.policy_path,
    )


def validate_chart_metadata(chart_root: Path, product: HelmProduct) -> Mapping[str, Any]:
    chart_file = bounded_path(chart_root, "Chart.yaml", "chart_metadata_invalid")
    require(chart_file.is_file() and not chart_file.is_symlink(), "chart_metadata_invalid")
    metadata = _yaml(chart_file, "chart_metadata_invalid")
    require(metadata.get("apiVersion") == "v2", "chart_metadata_invalid")
    require(metadata.get("name") == product.chart_name, "chart_metadata_invalid")
    require(isinstance(metadata.get("version"), str) and SEMVER.fullmatch(metadata["version"]) is not None, "chart_metadata_invalid")
    dependencies = metadata.get("dependencies", [])
    require(isinstance(dependencies, list), "dependency_lock_invalid")
    actual = []
    for item in dependencies:
        require(isinstance(item, Mapping), "dependency_lock_invalid")
        actual.append((item.get("name"), item.get("version"), item.get("repository")))
    require(tuple(actual) == product.locked_dependencies, "dependency_lock_invalid")
    return metadata


def validate_chart_lock(chart_root: Path, product: HelmProduct) -> None:
    if not product.locked_dependencies:
        require(not (chart_root / "Chart.lock").exists(), "dependency_lock_invalid")
        return
    lock = bounded_path(chart_root, "Chart.lock", "dependency_lock_invalid")
    require(lock.is_file() and not lock.is_symlink(), "dependency_lock_invalid")
    data = _yaml(lock, "dependency_lock_invalid")
    require(isinstance(data.get("digest"), str) and DIGEST.fullmatch(data["digest"]) is not None, "dependency_lock_invalid")
    dependencies = data.get("dependencies")
    require(isinstance(dependencies, list), "dependency_lock_invalid")
    actual = tuple((item.get("name"), item.get("version"), item.get("repository")) for item in dependencies if isinstance(item, Mapping))
    require(actual == product.locked_dependencies and len(actual) == len(dependencies), "dependency_lock_invalid")


def validate_chart_layout(source_root: Path, plan: HelmPlan) -> tuple[Path, Path]:
    chart_root = bounded_path(source_root, plan.product.chart_root, "chart_root_rejected")
    require(chart_root.is_dir() and not chart_root.is_symlink(), "chart_root_rejected")
    values_path = bounded_path(chart_root, plan.values_path, "values_profile_rejected")
    require(values_path.is_file() and not values_path.is_symlink(), "values_profile_rejected")
    _yaml(values_path, "values_profile_rejected")
    schema = chart_root / "values.schema.json"
    if schema.exists() or schema.is_symlink():
        require(schema.is_file() and not schema.is_symlink(), "schema_invalid")
        _json(schema, "schema_invalid")
    templates = chart_root / "templates"
    require(templates.is_dir() and not templates.is_symlink(), "template_invalid")
    for candidate in templates.rglob("*"):
        require(not candidate.is_symlink(), "template_invalid")
    crds = chart_root / "crds"
    if crds.exists() or crds.is_symlink():
        require(crds.is_dir() and not crds.is_symlink(), "template_invalid")
        for candidate in crds.rglob("*"):
            require(not candidate.is_symlink(), "template_invalid")
    validate_chart_metadata(chart_root, plan.product)
    validate_chart_lock(chart_root, plan.product)
    if plan.policy_path is not None:
        policy = bounded_path(source_root, plan.policy_path, "policy_path_rejected")
        require(policy.is_file() and not policy.is_symlink(), "policy_path_rejected")
    return chart_root, values_path
