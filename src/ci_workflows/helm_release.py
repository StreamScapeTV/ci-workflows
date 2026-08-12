"""Exact-tag Helm release binding and remote OCI manifest evidence for issue #18."""
from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any, Mapping, Sequence

import yaml

from .helm_contract import (
    IMMUTABLE_IMAGE_REFERENCE,
    bounded_path,
    require,
    validate_chart_layout,
)
from .helm_execution import (
    _chart_version,
    _copy_chart_for_build,
    _image_reference_assertions,
    _run,
    _runtime_environment,
    normalize_chart_archive,
    verify_exact_source,
    verify_helm_toolchain,
)
from .helm_types import HelmPlan, HelmValidationError, HelmValidationResult


RELEASE_BINDINGS_PATH = Path("contracts/helm-release-bindings.json")


def _mapping(value: Any, code: str) -> Mapping[str, Any]:
    require(isinstance(value, Mapping), code)
    return value


def load_release_bindings(root: Path) -> Mapping[str, Any]:
    try:
        payload = json.loads(
            (root / RELEASE_BINDINGS_PATH).read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError) as error:
        raise HelmValidationError("invalid_release_binding_contract") from error
    require(isinstance(payload, Mapping), "invalid_release_binding_contract")
    require(payload.get("schema_version") == 1, "invalid_release_binding_contract")
    products = payload.get("products")
    require(
        isinstance(products, Mapping)
        and set(products)
        == {
            "iptv-backend-chart",
            "agent-state-chart",
            "flux-github-actions-runner-chart",
        },
        "invalid_release_binding_contract",
    )
    for product_id, product in products.items():
        product = _mapping(product, "invalid_release_binding_contract")
        require(product.get("product_id") == product_id, "invalid_release_binding_contract")
        bindings = product.get("bindings")
        require(isinstance(bindings, list), "invalid_release_binding_contract")
        repositories: list[str] = []
        for binding in bindings:
            binding = _mapping(binding, "invalid_release_binding_contract")
            require(
                set(binding)
                == {"repository", "values_path", "repository_path", "digest_path"},
                "invalid_release_binding_contract",
            )
            repository = binding.get("repository")
            require(
                isinstance(repository, str)
                and "/" in repository
                and "@" not in repository
                and ":" not in repository.rsplit("/", 1)[-1],
                "invalid_release_binding_contract",
            )
            repositories.append(repository)
            safe = binding.get("values_path")
            require(isinstance(safe, str), "invalid_release_binding_contract")
            bounded = Path(safe)
            require(
                bool(safe)
                and not bounded.is_absolute()
                and ".." not in bounded.parts,
                "invalid_release_binding_contract",
            )
            for key in ("repository_path", "digest_path"):
                path = binding.get(key)
                require(
                    isinstance(path, list)
                    and path
                    and all(
                        isinstance(item, str)
                        and item
                        and len(item) <= 64
                        and item.replace("_", "").replace("-", "").isalnum()
                        for item in path
                    ),
                    "invalid_release_binding_contract",
                )
        require(
            repositories == sorted(set(repositories)),
            "invalid_release_binding_contract",
        )
    return payload


def parse_required_image_references(
    raw: str,
    product_id: str,
    contract: Mapping[str, Any],
) -> tuple[str, ...]:
    require(bool(raw.strip()), "required_image_references_required")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as error:
        raise HelmValidationError("required_image_references_invalid") from error
    require(
        isinstance(payload, list)
        and all(
            isinstance(item, str)
            and IMMUTABLE_IMAGE_REFERENCE.fullmatch(item) is not None
            for item in payload
        ),
        "required_image_references_invalid",
    )
    require(
        payload == sorted(set(payload)),
        "required_image_references_invalid",
    )
    products = _mapping(contract.get("products"), "invalid_release_binding_contract")
    product = _mapping(products.get(product_id), "unsupported_product")
    bindings = product.get("bindings")
    require(isinstance(bindings, list), "invalid_release_binding_contract")
    expected = tuple(binding["repository"] for binding in bindings)
    actual = tuple(reference.rsplit("@", 1)[0] for reference in payload)
    require(actual == expected, "required_image_references_mismatch")
    return tuple(payload)


def _yaml_scalar_node(text: str, key_path: Sequence[str]):
    try:
        node = yaml.compose(text)
    except yaml.YAMLError as error:
        raise HelmValidationError("image_binding_invalid") from error
    require(isinstance(node, yaml.nodes.MappingNode), "image_binding_invalid")
    current = node
    for key in key_path:
        require(isinstance(current, yaml.nodes.MappingNode), "image_binding_invalid")
        match = None
        for key_node, value_node in current.value:
            if isinstance(key_node, yaml.nodes.ScalarNode) and key_node.value == key:
                match = value_node
                break
        require(match is not None, "image_binding_invalid")
        current = match
    require(isinstance(current, yaml.nodes.ScalarNode), "image_binding_invalid")
    return current


def apply_release_image_bindings(
    chart_root: Path,
    product_contract: Mapping[str, Any],
    references: tuple[str, ...],
) -> None:
    bindings = product_contract["bindings"]
    reference_map = {
        reference.rsplit("@", 1)[0]: reference
        for reference in references
    }
    for binding in bindings:
        repository = binding["repository"]
        reference = reference_map.get(repository)
        require(reference is not None, "required_image_references_mismatch")
        digest = reference.rsplit("@", 1)[1]
        values_file = bounded_path(
            chart_root,
            binding["values_path"],
            "image_binding_invalid",
        )
        require(
            values_file.is_file() and not values_file.is_symlink(),
            "image_binding_invalid",
        )
        try:
            text = values_file.read_text(encoding="utf-8")
        except OSError as error:
            raise HelmValidationError("image_binding_invalid") from error
        repository_node = _yaml_scalar_node(text, binding["repository_path"])
        require(
            repository_node.value == repository,
            "image_binding_repository_mismatch",
        )
        digest_node = _yaml_scalar_node(text, binding["digest_path"])
        require(
            digest_node.value in {"", digest},
            "image_binding_conflict",
        )
        text = (
            text[: digest_node.start_mark.index]
            + json.dumps(digest)
            + text[digest_node.end_mark.index :]
        )
        try:
            values_file.write_text(text, encoding="utf-8")
            parsed = yaml.safe_load(text)
        except (OSError, yaml.YAMLError) as error:
            raise HelmValidationError("image_binding_invalid") from error
        require(isinstance(parsed, Mapping), "image_binding_invalid")


def validate_and_package_release(
    source_root: Path,
    state_root: Path,
    plan: HelmPlan,
    admitted_sha: str,
    references: tuple[str, ...],
    product_contract: Mapping[str, Any],
    inherited: Mapping[str, str],
) -> HelmValidationResult:
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
    apply_release_image_bindings(work_chart, product_contract, references)
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
    _image_reference_assertions(rendered, references)

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


def remote_chart_manifest_digest(
    source_root: Path,
    state_root: Path,
    chart_reference: str,
    release_version: str,
    expected_package_sha256: str,
    inherited: Mapping[str, str],
) -> str:
    environment = _runtime_environment(inherited, state_root)
    authfile = Path(environment["HELM_CONFIG_HOME"]) / "registry" / "config.json"
    require(authfile.is_file() and not authfile.is_symlink(), "registry_auth_failed")
    docker_reference = (
        "docker://" + chart_reference.removeprefix("oci://") + ":" + release_version
    )
    manifest = _run(
        [
            "skopeo",
            "inspect",
            "--raw",
            "--authfile",
            str(authfile),
            docker_reference,
        ],
        cwd=source_root,
        environment=environment,
        timeout=120,
        code="remote_manifest_read_back_failed",
    ).stdout
    require(0 < len(manifest.encode("utf-8")) <= 2_000_000, "remote_manifest_read_back_failed")
    try:
        payload = json.loads(manifest)
    except json.JSONDecodeError as error:
        raise HelmValidationError("remote_manifest_invalid") from error
    require(isinstance(payload, Mapping), "remote_manifest_invalid")
    config = payload.get("config")
    layers = payload.get("layers")
    require(
        isinstance(config, Mapping)
        and config.get("mediaType") == "application/vnd.cncf.helm.config.v1+json",
        "remote_manifest_invalid",
    )
    require(isinstance(layers, list) and len(layers) == 1, "remote_manifest_invalid")
    layer = layers[0]
    require(
        isinstance(layer, Mapping)
        and layer.get("mediaType")
        == "application/vnd.cncf.helm.chart.content.v1.tar+gzip"
        and layer.get("digest") == f"sha256:{expected_package_sha256}",
        "remote_manifest_invalid",
    )
    return "sha256:" + hashlib.sha256(manifest.encode("utf-8")).hexdigest()
