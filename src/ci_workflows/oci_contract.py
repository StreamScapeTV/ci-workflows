"""Checked-in OCI product contract and deterministic build planning."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

from .oci_types import OciBuildError, OciBuildPlan, OciBuildRequest, OciTarget

CONTRACT_PATH = Path("contracts/oci-products.json")
MAPPING_PATH = Path("generated/oci-engine-mapping.json")
_FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
_REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_PRODUCT = re.compile(r"^[a-z0-9]+(?:[._-][a-z0-9]+)*$")
_SEMVER = re.compile(
    r"^(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"
    r"(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)
_PLATFORM = re.compile(r"^linux/(?:amd64|arm64/v8)$")
_FORBIDDEN = {
    "arguments", "artifact_upload", "buildah", "builder", "buildkit",
    "callback", "command", "deployment", "docker", "engine", "flux_target",
    "podman", "publish", "registry", "registry_command", "runner",
    "runner_labels", "runs_on", "secret_name", "shell", "socket",
    "storage_driver",
}
_REQUIRED_LABELS = {
    "dev.streamscape.product",
    "org.opencontainers.image.created",
    "org.opencontainers.image.description",
    "org.opencontainers.image.licenses",
    "org.opencontainers.image.revision",
    "org.opencontainers.image.source",
    "org.opencontainers.image.title",
    "org.opencontainers.image.version",
}
_BUILD_RUNNER_SELECTORS = {
    "buildah-tiny": ("linux", "amd64", "buildah", "tiny"),
    "buildah-small": ("linux", "amd64", "buildah", "small"),
    "buildah-medium": ("linux", "amd64", "buildah", "medium"),
    "buildah-high": ("linux", "amd64", "buildah", "high"),
}


def require(condition: bool, code: str) -> None:
    if not condition:
        raise OciBuildError(code)


def mapping(value: Any) -> Mapping[str, Any]:
    require(isinstance(value, Mapping), "invalid_contract")
    return value


def strings(value: Any, *, nonempty: bool = False) -> tuple[str, ...]:
    require(isinstance(value, list), "invalid_contract")
    require(all(isinstance(item, str) and item for item in value), "invalid_contract")
    require(len(value) == len(set(value)), "invalid_contract")
    require(not nonempty or bool(value), "invalid_contract")
    return tuple(value)


def safe_relative(value: Any, *, allow_dot: bool = False) -> str:
    require(isinstance(value, str), "invalid_path")
    if allow_dot and value == ".":
        return value
    path = PurePosixPath(value)
    require(
        bool(value)
        and value == value.strip()
        and not path.is_absolute()
        and ".." not in path.parts
        and "\\" not in value
        and all(part not in {"", "."} for part in path.parts),
        "invalid_path",
    )
    return path.as_posix()


def bounded_path(root: Path, relative: str, *, allow_root: bool = False) -> Path:
    # Reject a caller-controlled checkout alias before resolving it.  Resolving
    # first would erase the fact that the root itself was a symlink.
    require(root.is_dir() and not root.is_symlink(), "invalid_path")
    root = root.resolve()
    if relative == ".":
        require(allow_root, "invalid_path")
        return root
    current = root
    for part in PurePosixPath(safe_relative(relative)).parts:
        current /= part
        require(not current.is_symlink(), "symlink_path_forbidden")
    resolved = current.resolve(strict=False)
    require(root in resolved.parents, "invalid_path")
    return resolved


def _validate_target(value: Mapping[str, Any], platform_sets: Mapping[str, Any]) -> OciTarget:
    require(
        set(value)
        == {
            "target_id", "context_path", "dockerfile_path", "target_stage",
            "platform_set", "smoke_script", "assertions", "fixed_build_args",
            "secret_mount_ids",
        },
        "invalid_contract",
    )
    target_id = value["target_id"]
    require(isinstance(target_id, str) and _PRODUCT.fullmatch(target_id) is not None, "invalid_contract")
    platform_set = value["platform_set"]
    require(isinstance(platform_set, str) and platform_set in platform_sets, "invalid_platform_set")
    platforms = strings(platform_sets[platform_set], nonempty=True)
    require(all(_PLATFORM.fullmatch(item) is not None for item in platforms), "invalid_contract")
    assertions = mapping(value["assertions"])
    require(
        set(assertions)
        == {
            "user", "entrypoint", "command", "ports", "required_files",
            "required_tools", "forbidden_tools",
        },
        "invalid_contract",
    )
    user = assertions["user"]
    require(user is None or isinstance(user, str), "invalid_contract")
    stage = value["target_stage"]
    require(stage is None or (isinstance(stage, str) and _PRODUCT.fullmatch(stage) is not None), "invalid_contract")
    smoke = value["smoke_script"]
    smoke_script = None if smoke is None else safe_relative(smoke)
    build_args = mapping(value["fixed_build_args"])
    normalized_args: dict[str, str] = {}
    for key, item in build_args.items():
        require(isinstance(key, str) and re.fullmatch(r"[A-Z][A-Z0-9_]{0,63}", key) is not None, "invalid_contract")
        require(isinstance(item, str) and "\n" not in item and "\r" not in item and len(item) <= 512, "invalid_contract")
        normalized_args[key] = item
    secret_ids = strings(value["secret_mount_ids"])
    require(all(re.fullmatch(r"[a-z][a-z0-9_-]{1,63}", item) for item in secret_ids), "invalid_contract")
    return OciTarget(
        target_id=target_id,
        context_path=safe_relative(value["context_path"], allow_dot=True),
        dockerfile_path=safe_relative(value["dockerfile_path"]),
        target_stage=stage,
        platforms=platforms,
        smoke_script=smoke_script,
        required_user=user,
        required_entrypoint=strings(assertions["entrypoint"]),
        required_command=strings(assertions["command"]),
        required_ports=strings(assertions["ports"]),
        required_files=strings(assertions["required_files"]),
        required_tools=strings(assertions["required_tools"]),
        forbidden_tools=strings(assertions["forbidden_tools"]),
        fixed_build_args=normalized_args,
        secret_mount_ids=secret_ids,
    )


def load_contract(root: Path) -> Mapping[str, Any]:
    try:
        payload = json.loads((root / CONTRACT_PATH).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise OciBuildError("invalid_contract") from error
    payload = mapping(payload)
    require(payload.get("schema_version") == 1, "invalid_contract")
    require(payload.get("contract_version") == "1.0.0", "invalid_contract")
    require(payload.get("organization") == "StreamScapeTV", "invalid_contract")
    require(payload.get("workflow_api") == "oci.build", "invalid_contract")
    require(payload.get("stable_check_name") == "CI / OCI build validation", "invalid_contract")
    require(payload.get("publication") is False, "publication_forbidden")
    require(payload.get("registry_credentials") is False, "registry_credentials_forbidden")
    require(payload.get("artifact_policy") == "zero-default", "artifact_policy_failed")
    require(strings(payload.get("allowed_source_trust"), nonempty=True) == ("trusted-exact", "trusted-pr"), "invalid_contract")
    require(payload.get("storage_driver") == "vfs", "invalid_contract")
    require(set(strings(payload.get("required_metadata_labels"), nonempty=True)) == _REQUIRED_LABELS, "invalid_contract")
    require(_FORBIDDEN <= set(strings(payload.get("forbidden_public_inputs"), nonempty=True)), "invalid_contract")
    platforms = mapping(payload.get("platform_sets"))
    runners = mapping(payload.get("runner_profiles"))
    products = mapping(payload.get("products"))
    require(set(products) >= {"iptv-backend-image", "agent-state-image", "flux-runner-images", "ciw-oci-smoke"}, "invalid_contract")
    normalized: dict[str, Any] = {}
    for product_id, raw in products.items():
        require(isinstance(product_id, str) and _PRODUCT.fullmatch(product_id) is not None, "invalid_contract")
        product = mapping(raw)
        require(
            set(product)
            == {
                "repository", "builder_id", "runner_profile", "workspace_profile",
                "timeout_minutes", "measurement", "metadata", "targets",
                "flux_asset", "canary_id", "previous_known_good", "rollback_id",
                "independent_bootstrap", "adoption_ready",
            },
            "invalid_contract",
        )
        repository = product["repository"]
        require(isinstance(repository, str) and _REPOSITORY.fullmatch(repository), "invalid_contract")
        require(product["builder_id"] == "buildah-v1", "invalid_contract")
        runner_profile = product["runner_profile"]
        require(
            isinstance(runner_profile, str)
            and runner_profile in runners
            and runner_profile in _BUILD_RUNNER_SELECTORS,
            "invalid_contract",
        )
        runner = mapping(runners[runner_profile])
        labels = strings(runner.get("labels"), nonempty=True)
        require(labels == _BUILD_RUNNER_SELECTORS[runner_profile], "invalid_contract")
        for key in ("memory_limit_bytes", "storage_limit_bytes"):
            require(type(runner.get(key)) is int and runner[key] > 0, "invalid_contract")
        workspace_profile = product["workspace_profile"]
        timeout_minutes = product["timeout_minutes"]
        require(
            isinstance(workspace_profile, str)
            and _PRODUCT.fullmatch(workspace_profile) is not None,
            "invalid_contract",
        )
        require(
            type(timeout_minutes) is int and 1 <= timeout_minutes <= 180,
            "invalid_contract",
        )
        require(isinstance(product["adoption_ready"], bool), "invalid_contract")
        measurement = mapping(product["measurement"])
        require(set(measurement) == {"peak_memory_bytes", "peak_storage_bytes", "headroom_percent"}, "invalid_contract")
        for key in ("peak_memory_bytes", "peak_storage_bytes"):
            require(isinstance(measurement[key], int) and measurement[key] > 0, "invalid_contract")
        require(isinstance(measurement["headroom_percent"], int) and 10 <= measurement["headroom_percent"] <= 100, "invalid_contract")
        require(measurement["peak_memory_bytes"] * (100 + measurement["headroom_percent"]) <= runner["memory_limit_bytes"] * 100, "invalid_contract")
        require(measurement["peak_storage_bytes"] * (100 + measurement["headroom_percent"]) <= runner["storage_limit_bytes"] * 100, "invalid_contract")
        metadata = mapping(product["metadata"])
        require(set(metadata) == {"title", "description", "licenses"}, "invalid_contract")
        require(all(isinstance(metadata[key], str) and metadata[key] for key in metadata), "invalid_contract")
        raw_targets = product["targets"]
        require(isinstance(raw_targets, list) and raw_targets, "invalid_contract")
        targets = tuple(_validate_target(mapping(item), platforms) for item in raw_targets)
        require(len({target.target_id for target in targets}) == len(targets), "invalid_contract")
        flux_asset = product["flux_asset"]
        require(isinstance(flux_asset, bool), "invalid_contract")
        if flux_asset:
            require(product["independent_bootstrap"] is True, "invalid_contract")
            require(all(isinstance(product[key], str) and product[key] for key in ("canary_id", "previous_known_good", "rollback_id")), "invalid_contract")
        else:
            require(product["independent_bootstrap"] is False, "invalid_contract")
            require(all(product[key] is None for key in ("canary_id", "previous_known_good", "rollback_id")), "invalid_contract")
        normalized[product_id] = {**product, "targets": targets, "runs_on": labels}
    return {**payload, "_products": normalized}


def source_trust_from_environment(environment: Mapping[str, str]) -> str:
    if environment.get("GITHUB_EVENT_NAME") != "pull_request":
        return "trusted-exact"
    path = environment.get("GITHUB_EVENT_PATH", "")
    try:
        event = json.loads(Path(path).read_text(encoding="utf-8"))
        head = event["pull_request"]["head"]["repo"]["full_name"]
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as error:
        raise OciBuildError("invalid_request") from error
    return "trusted-pr" if head == environment.get("GITHUB_REPOSITORY") else "untrusted-fork"


def request_from_mapping(value: Mapping[str, Any], environment: Mapping[str, str]) -> OciBuildRequest:
    allowed = {"repository", "admitted_sha", "product_id", "release_version", "platform_set", "artifact_exception_id"}
    require(set(value) <= allowed, "forbidden_input")
    require(not (_FORBIDDEN & set(value)), "forbidden_input")
    repository = value.get("repository")
    admitted_sha = value.get("admitted_sha")
    product_id = value.get("product_id")
    version = value.get("release_version")
    platform_set = value.get("platform_set")
    artifact = value.get("artifact_exception_id")
    require(isinstance(repository, str) and _REPOSITORY.fullmatch(repository), "invalid_request")
    require(isinstance(admitted_sha, str) and _FULL_SHA.fullmatch(admitted_sha), "invalid_request")
    require(isinstance(product_id, str) and _PRODUCT.fullmatch(product_id), "invalid_request")
    require(version in {None, ""} or (isinstance(version, str) and _SEMVER.fullmatch(version)), "invalid_version")
    require(platform_set in {None, ""} or (isinstance(platform_set, str) and _PRODUCT.fullmatch(platform_set)), "invalid_platform_set")
    require(artifact in {None, ""}, "artifact_policy_failed")
    return OciBuildRequest(
        repository=repository,
        admitted_sha=admitted_sha,
        product_id=product_id,
        release_version=None if version in {None, ""} else str(version),
        platform_set=None if platform_set in {None, ""} else str(platform_set),
        artifact_exception_id=None,
        source_trust=source_trust_from_environment(environment),
    )


def resolve_plan(root: Path, request: OciBuildRequest) -> OciBuildPlan:
    contract = load_contract(root)
    products = contract["_products"]
    require(request.product_id in products, "unsupported_product")
    product = products[request.product_id]
    require(product["repository"] == request.repository, "unsupported_consumer")
    require(request.source_trust in contract["allowed_source_trust"], "unsupported_consumer")
    if request.platform_set:
        expected = tuple(contract["platform_sets"].get(request.platform_set, ()))
        require(expected, "invalid_platform_set")
        require(all(target.platforms == expected for target in product["targets"]), "platform_override_forbidden")
    version = request.release_version or f"0.0.0-ci-{request.admitted_sha[:12]}"
    return OciBuildPlan(
        repository=request.repository,
        admitted_sha=request.admitted_sha,
        product_id=request.product_id,
        release_version=version,
        source_trust=request.source_trust,
        runner_profile=product["runner_profile"],
        runs_on=product["runs_on"],
        workspace_profile=product["workspace_profile"],
        timeout_minutes=product["timeout_minutes"],
        builder_id=product["builder_id"],
        storage_driver=contract["storage_driver"],
        targets=product["targets"],
        flux_asset=product["flux_asset"],
        canary_id=product["canary_id"],
        previous_known_good=product["previous_known_good"],
        rollback_id=product["rollback_id"],
        adoption_ready=product["adoption_ready"],
    )


def metadata_labels(contract: Mapping[str, Any], plan: OciBuildPlan, target: OciTarget, epoch: int) -> Mapping[str, str]:
    product = contract["_products"][plan.product_id]
    meta = product["metadata"]
    return {
        "dev.streamscape.product": target.target_id,
        "org.opencontainers.image.created": datetime.fromtimestamp(epoch, timezone.utc).isoformat().replace("+00:00", "Z"),
        "org.opencontainers.image.description": meta["description"],
        "org.opencontainers.image.licenses": meta["licenses"],
        "org.opencontainers.image.revision": plan.admitted_sha,
        "org.opencontainers.image.source": f"https://github.com/{plan.repository}",
        "org.opencontainers.image.title": meta["title"],
        "org.opencontainers.image.version": plan.release_version,
    }


def render_engine_mapping(contract: Mapping[str, Any]) -> Mapping[str, Any]:
    rows = {
        product_id: {
            "builder_id": product["builder_id"],
            "runner_profile": product["runner_profile"],
            "runs_on": list(product["runs_on"]),
            "storage_driver": contract["storage_driver"],
            "targets": [
                {"target_id": target.target_id, "platforms": list(target.platforms)}
                for target in product["targets"]
            ],
        }
        for product_id, product in sorted(contract["_products"].items())
    }
    digest = hashlib.sha256(json.dumps(rows, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {
        "schema_version": 1,
        "contract_version": contract["contract_version"],
        "workflow_api": contract["workflow_api"],
        "mapping_sha256": digest,
        "products": rows,
    }


def validate_generated_mapping(root: Path, contract: Mapping[str, Any] | None = None) -> None:
    contract = contract or load_contract(root)
    try:
        actual = json.loads((root / MAPPING_PATH).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise OciBuildError("generated_mapping_stale") from error
    require(actual == render_engine_mapping(contract), "generated_mapping_stale")
