"""Inventory-driven orchestration for Flux-owned infrastructure assets.

The module owns no registry destination, Kubernetes target, cluster credential,
or live selection policy. It validates a small checked-in product contract,
derives contract-owned dependency calls, verifies immutable dependency evidence,
and emits a review-only Flux handoff.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import re
import shutil
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence

_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_RELEASE_RE = re.compile(r"^[0-9A-Za-z][0-9A-Za-z._+-]{0,127}$")
_REQUEST_RE = re.compile(r"^[0-9A-Za-z][0-9A-Za-z._:-]{0,127}$")
_TOOL_VERSION_RE = re.compile(r"^[0-9A-Za-z][0-9A-Za-z.+_-]{0,63}$")
_ALLOWED_OPERATIONS = frozenset({"plan", "release", "verify-only"})
_SUCCESS_RESULTS = frozenset({"success", "verified", "published", "replayed"})


class FluxAssetError(ValueError):
    """Stable fail-closed error with a machine-readable code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


@dataclasses.dataclass(frozen=True)
class DependencyCall:
    api_name: str
    product_id: str
    required_outputs: tuple[str, ...]


@dataclasses.dataclass(frozen=True)
class ReleasePlan:
    admitted_sha: str
    product_id: str
    release_version: str
    operation: str
    policy_path: str
    request_id: str
    asset_kind: str
    runs_on: tuple[str, ...]
    workspace_profile: str
    dependencies: tuple[DependencyCall, ...]
    version_identity: str
    source_identity: str
    canary_id: str
    previous_known_good_policy: str
    rollback_id: str
    bootstrap_policy: Mapping[str, Any]

    def as_dict(self) -> dict[str, Any]:
        payload = dataclasses.asdict(self)
        payload["runs_on"] = list(self.runs_on)
        payload["dependencies"] = [
            {
                "api_name": item.api_name,
                "product_id": item.product_id,
                "required_outputs": list(item.required_outputs),
            }
            for item in self.dependencies
        ]
        payload["bootstrap_policy"] = dict(self.bootstrap_policy)
        return payload


def _contract_error(message: str) -> FluxAssetError:
    return FluxAssetError("invalid_contract", message)


def _require_mapping(value: Any, *, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise _contract_error(f"{name} must be an object")
    return value


def _require_list(value: Any, *, name: str) -> list[Any]:
    if not isinstance(value, list):
        raise _contract_error(f"{name} must be a list")
    return value


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise FluxAssetError("invalid_json", f"{path}: {error}") from error


def canonical_json(value: Any) -> str:
    """Return deterministic JSON used for bounded evidence hashes."""

    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def load_contract(path: Path) -> dict[str, Any]:
    """Load and structurally validate the issue-33 product contract."""

    payload = _require_mapping(_load_json(path), name="contract")
    if payload.get("schema_version") != 1:
        raise _contract_error("schema_version must equal 1")
    if payload.get("workflow_api") != "flux.assets":
        raise _contract_error("workflow_api must equal flux.assets")
    if payload.get("source_repository") != "StreamScapeTV/flux":
        raise _contract_error("source_repository must remain StreamScapeTV/flux")
    if payload.get("artifact_policy") != "zero":
        raise _contract_error("routine artifact policy must be zero")
    if payload.get("cluster_mutation_authorized") is not False:
        raise _contract_error("cluster mutation must be explicitly false")
    if payload.get("kubernetes_credentials") is not False:
        raise _contract_error("Kubernetes credentials must be explicitly false")
    if payload.get("latest_forbidden") is not True:
        raise _contract_error("latest must be forbidden")

    dependency_interfaces = _require_mapping(
        payload.get("dependency_interfaces"), name="dependency_interfaces"
    )
    for api_name, interface_raw in dependency_interfaces.items():
        interface = _require_mapping(interface_raw, name=f"dependency {api_name}")
        outputs = _require_list(
            interface.get("required_outputs"), name=f"{api_name}.required_outputs"
        )
        if not outputs or any(not isinstance(item, str) or not item for item in outputs):
            raise _contract_error(f"{api_name}.required_outputs must contain names")
        product_id = interface.get("product_id")
        if not isinstance(product_id, str) or not product_id:
            raise _contract_error(f"{api_name}.product_id is required")

    products = _require_mapping(payload.get("products"), name="products")
    if set(products) != {"flux-runner-images", "flux-runner-chart-assets"}:
        raise _contract_error("exact Flux infrastructure product set is required")

    images = _require_mapping(products["flux-runner-images"], name="flux-runner-images")
    if images.get("kind") != "runner-image-family":
        raise _contract_error("flux-runner-images kind mismatch")
    members = _require_list(images.get("members"), name="flux-runner-images.members")
    member_ids = {
        str(_require_mapping(item, name="image member").get("id")) for item in members
    }
    expected_members = {
        "github-actions-runner-buildah",
        "github-actions-runner-mobile",
    }
    if member_ids != expected_members:
        raise _contract_error(
            "custom runner image inventory must contain Buildah and Mobile only"
        )
    if "github-actions-runner-portable" in member_ids:
        raise _contract_error("portable is upstream and may not be a custom image product")

    chart = _require_mapping(
        products["flux-runner-chart-assets"], name="flux-runner-chart-assets"
    )
    if chart.get("kind") != "runner-chart-bundle":
        raise _contract_error("flux-runner-chart-assets kind mismatch")
    if chart.get("chart_root") != "apps/github-actions-runner":
        raise _contract_error("confirmed runner chart root mismatch")
    upstream = _require_list(chart.get("upstream_assets"), name="upstream_assets")
    upstream_ids = {
        str(_require_mapping(item, name="upstream asset").get("id"))
        for item in upstream
    }
    if upstream_ids != {"gha-runner-scale-set-controller", "gha-runner-scale-set"}:
        raise _contract_error("exact ARC chart asset set is required")

    return dict(payload)


def validate_live_inventory(contract: Mapping[str, Any], paths: Sequence[str]) -> None:
    """Prove the central inventory still matches a supplied exact Flux tree."""

    normalized = {PurePosixPath(path).as_posix() for path in paths}
    products = _require_mapping(contract["products"], name="products")
    images = _require_mapping(products["flux-runner-images"], name="flux-runner-images")
    members = _require_list(images["members"], name="members")
    expected_dirs = {
        str(_require_mapping(member, name="image member")["source_root"])
        for member in members
    }
    actual_custom = {
        path
        for path in normalized
        if path.startswith("images/github-actions-runner-") and path.count("/") == 1
    }
    if actual_custom != expected_dirs:
        raise FluxAssetError(
            "inventory_drift",
            f"custom image roots differ: expected={sorted(expected_dirs)}, "
            f"actual={sorted(actual_custom)}",
        )
    chart = _require_mapping(products["flux-runner-chart-assets"], name="chart")
    chart_root = str(chart["chart_root"])
    if f"{chart_root}/Chart.yaml" not in normalized:
        raise FluxAssetError("inventory_drift", "confirmed runner chart is missing")
    if "images/github-actions-runner-portable" in normalized:
        raise FluxAssetError(
            "inventory_drift", "unexpected custom portable image appeared"
        )


def _safe_relative_path(raw: str, *, allowed_roots: Sequence[str]) -> str:
    if not raw or "\\" in raw:
        raise FluxAssetError(
            "invalid_policy_path", "policy path must use POSIX separators"
        )
    path = PurePosixPath(raw)
    if path.is_absolute() or ".." in path.parts:
        raise FluxAssetError(
            "invalid_policy_path", "policy path must be normalized and relative"
        )
    value = path.as_posix()
    if not any(
        value == root.rstrip("/") or value.startswith(root.rstrip("/") + "/")
        for root in allowed_roots
    ):
        raise FluxAssetError(
            "invalid_policy_path", "policy path is outside contract-owned roots"
        )
    lowered = value.lower()
    if any(
        token in lowered
        for token in ("secret", ".sops", "kubeconfig", "credential")
    ):
        raise FluxAssetError(
            "invalid_policy_path", "credential-bearing policy paths are forbidden"
        )
    return value


def _validate_release_version(value: str) -> str:
    if not _RELEASE_RE.fullmatch(value) or value.lower() == "latest":
        raise FluxAssetError(
            "invalid_release_version", "release_version is invalid or mutable"
        )
    return value


def _validate_request_id(value: str) -> str:
    if not _REQUEST_RE.fullmatch(value):
        raise FluxAssetError(
            "invalid_request_id", "request_id is outside the bounded format"
        )
    return value


def _validate_tag_authority(
    operation: str, release_version: str, ref_type: str, ref_name: str
) -> None:
    if operation != "release":
        return
    if ref_type != "tag":
        raise FluxAssetError(
            "release_tag_required", "publication requires an exact tag context"
        )
    if ref_name not in {release_version, f"v{release_version}"}:
        raise FluxAssetError(
            "release_tag_mismatch", "release version does not match the exact tag"
        )


def build_release_plan(
    contract: Mapping[str, Any],
    *,
    admitted_sha: str,
    product_id: str,
    release_version: str,
    operation: str,
    policy_path: str,
    request_id: str,
    source_ref_type: str = "",
    source_ref_name: str = "",
) -> ReleasePlan:
    """Derive a complete plan without caller-selected infrastructure."""

    if not _SHA_RE.fullmatch(admitted_sha):
        raise FluxAssetError(
            "invalid_source_sha", "admitted_sha must be lowercase 40-hex"
        )
    if operation not in _ALLOWED_OPERATIONS:
        raise FluxAssetError(
            "invalid_operation",
            f"operation must be one of {sorted(_ALLOWED_OPERATIONS)}",
        )
    release_version = _validate_release_version(release_version)
    request_id = _validate_request_id(request_id)
    _validate_tag_authority(
        operation, release_version, source_ref_type, source_ref_name
    )

    products = _require_mapping(contract["products"], name="products")
    product = products.get(product_id)
    if not isinstance(product, Mapping):
        raise FluxAssetError(
            "unsupported_product", f"unsupported product {product_id!r}"
        )
    policy_path = _safe_relative_path(
        policy_path,
        allowed_roots=[
            str(item)
            for item in _require_list(product["policy_roots"], name="policy_roots")
        ],
    )

    runners = _require_mapping(product["runner_profiles"], name="runner_profiles")
    runner = _require_mapping(
        runners.get(operation), name=f"runner profile {operation}"
    )
    labels = tuple(
        str(item)
        for item in _require_list(runner["labels"], name="runner labels")
    )
    if "self-hosted" in labels or labels == ("buildah",):
        raise _contract_error("unsafe runner selector in product contract")

    interfaces = _require_mapping(
        contract["dependency_interfaces"], name="dependency_interfaces"
    )
    operation_dependencies = _require_mapping(product["operations"], name="operations")
    names = _require_list(
        operation_dependencies.get(operation), name=f"operations.{operation}"
    )
    dependencies: list[DependencyCall] = []
    for api_name in names:
        interface = _require_mapping(
            interfaces.get(str(api_name)), name=f"dependency {api_name}"
        )
        dependencies.append(
            DependencyCall(
                api_name=str(api_name),
                product_id=str(interface["product_id"]),
                required_outputs=tuple(
                    str(item) for item in interface["required_outputs"]
                ),
            )
        )

    handoff = _require_mapping(product["handoff"], name="handoff")
    bootstrap = _require_mapping(product["bootstrap"], name="bootstrap")
    return ReleasePlan(
        admitted_sha=admitted_sha,
        product_id=product_id,
        release_version=release_version,
        operation=operation,
        policy_path=policy_path,
        request_id=request_id,
        asset_kind=str(product["kind"]),
        runs_on=labels,
        workspace_profile=str(runner["workspace_profile"]),
        dependencies=tuple(dependencies),
        version_identity=release_version,
        source_identity=f"sha-{admitted_sha}",
        canary_id=str(handoff["canary_id"]),
        previous_known_good_policy=str(handoff["previous_known_good_policy"]),
        rollback_id=str(handoff["rollback_id"]),
        bootstrap_policy=dict(bootstrap),
    )


def _is_digest_reference(value: str) -> bool:
    if "@sha256:" not in value:
        return False
    digest = "sha256:" + value.rsplit("@sha256:", 1)[1]
    return bool(_DIGEST_RE.fullmatch(digest))


def validate_bootstrap_independence(
    plan: ReleasePlan,
    *,
    known_good_builder_reference: str,
    candidate_reference: str,
) -> None:
    """Reject self-hosting the unverified replacement during bootstrap."""

    if not _is_digest_reference(known_good_builder_reference):
        raise FluxAssetError(
            "mutable_bootstrap_builder", "known-good builder must be digest-pinned"
        )
    if not _is_digest_reference(candidate_reference):
        raise FluxAssetError(
            "mutable_candidate_reference", "candidate reference must be digest-pinned"
        )
    if known_good_builder_reference == candidate_reference:
        raise FluxAssetError(
            "self_bootstrap_forbidden", "candidate cannot build or verify itself"
        )
    if plan.bootstrap_policy.get("known_good_required") is not True:
        raise _contract_error("known_good_required must remain true")


def validate_dockerfile_bases(text: str) -> tuple[str, ...]:
    """Require every effective FROM identity to contain an immutable digest."""

    bases: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or not line.upper().startswith("FROM "):
            continue
        fields = line.split()
        image = next(
            (
                field
                for field in fields[1:]
                if not field.startswith("--") and field.upper() != "AS"
            ),
            "",
        )
        if not image or "$" in image or not _is_digest_reference(image):
            raise FluxAssetError(
                "mutable_base_image", f"FROM is not digest-pinned: {line}"
            )
        bases.append(image)
    if not bases:
        raise FluxAssetError(
            "missing_base_image", "Dockerfile contains no FROM instruction"
        )
    return tuple(bases)


def validate_runtime_probe(
    expected: Mapping[str, Any], probe: Mapping[str, Any]
) -> None:
    """Validate exact runtime capability and forbidden-state evidence."""

    if (
        probe.get("os") != expected.get("os")
        or probe.get("architecture") != expected.get("architecture")
    ):
        raise FluxAssetError(
            "platform_mismatch", "runtime platform does not match product contract"
        )
    required_tools = _require_mapping(
        expected.get("required_tools"), name="required_tools"
    )
    actual_tools = _require_mapping(probe.get("tools"), name="probe.tools")
    for tool, version in required_tools.items():
        if not isinstance(version, str) or not _TOOL_VERSION_RE.fullmatch(version):
            raise _contract_error(f"invalid required tool version for {tool}")
        if actual_tools.get(tool) != version:
            raise FluxAssetError(
                "tool_version_mismatch", f"{tool} version mismatch"
            )
    if probe.get("forbidden_tools_present"):
        raise FluxAssetError(
            "forbidden_tool_present", "forbidden runtime tool is present"
        )
    if probe.get("forbidden_sockets_present"):
        raise FluxAssetError(
            "forbidden_socket_present", "forbidden daemon socket is present"
        )
    if probe.get("credential_paths_present"):
        raise FluxAssetError(
            "credential_residue", "credential-bearing path is present"
        )
    if probe.get("service_account_token_present") is True:
        raise FluxAssetError(
            "service_account_token_present", "Kubernetes token is forbidden"
        )
    if probe.get("kubeconfig_present") is True:
        raise FluxAssetError("kubeconfig_present", "KUBECONFIG is forbidden")


def validate_chart_upstream(
    expected: Mapping[str, Any], evidence: Mapping[str, Any]
) -> None:
    """Require locked upstream identity, digest, license, and attribution."""

    if evidence.get("repository") != expected.get("upstream_repository"):
        raise FluxAssetError(
            "upstream_repository_mismatch", "chart upstream repository mismatch"
        )
    if evidence.get("version") != expected.get("version"):
        raise FluxAssetError(
            "upstream_version_mismatch", "chart upstream version mismatch"
        )
    digest = str(evidence.get("digest", ""))
    if not _DIGEST_RE.fullmatch(digest):
        raise FluxAssetError(
            "mutable_upstream", "upstream chart digest is missing or invalid"
        )
    if evidence.get("license") != expected.get("license"):
        raise FluxAssetError("license_mismatch", "upstream chart license mismatch")
    if evidence.get("attribution_preserved") is not True:
        raise FluxAssetError(
            "attribution_missing", "upstream attribution must be preserved"
        )
    if evidence.get("templates_mutated") is True:
        raise FluxAssetError(
            "unreviewed_template_mutation",
            "unreviewed upstream template mutation is forbidden",
        )


def _parse_json_object(value: Any, *, name: str) -> dict[str, Any]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as error:
            raise FluxAssetError(
                "invalid_dependency_output", f"{name} is not valid JSON"
            ) from error
    if not isinstance(value, Mapping):
        raise FluxAssetError(
            "invalid_dependency_output", f"{name} must be a JSON object"
        )
    return dict(value)


def _contains_latest(value: Any) -> bool:
    if isinstance(value, str):
        return bool(re.search(r"(^|[:/@])latest($|[:/@])", value.lower()))
    if isinstance(value, Mapping):
        return any(_contains_latest(item) for item in value.values())
    if isinstance(value, Sequence) and not isinstance(
        value, (str, bytes, bytearray)
    ):
        return any(_contains_latest(item) for item in value)
    return False


def _validate_digest_container(value: Any, *, name: str) -> None:
    if isinstance(value, str):
        if _DIGEST_RE.fullmatch(value):
            return
        try:
            value = json.loads(value)
        except json.JSONDecodeError as error:
            raise FluxAssetError(
                "invalid_digest", f"{name} is not a digest or JSON digest map"
            ) from error
    if isinstance(value, Mapping):
        if not value:
            raise FluxAssetError("invalid_digest", f"{name} digest map is empty")
        for key, digest in value.items():
            if not isinstance(key, str) or not _DIGEST_RE.fullmatch(str(digest)):
                raise FluxAssetError(
                    "invalid_digest", f"{name} contains an invalid digest"
                )
        return
    raise FluxAssetError("invalid_digest", f"{name} has an unsupported shape")


def verify_dependency_outputs(
    plan: ReleasePlan, evidence: Mapping[str, Any]
) -> dict[str, Any]:
    """Validate only the bounded public outputs declared by dependencies."""

    normalized: dict[str, Any] = {}
    for dependency in plan.dependencies:
        raw = evidence.get(dependency.api_name)
        if not isinstance(raw, Mapping):
            raise FluxAssetError(
                "missing_dependency_evidence",
                f"{dependency.api_name} evidence is required",
            )
        missing = set(dependency.required_outputs) - set(str(key) for key in raw)
        if missing:
            raise FluxAssetError(
                "missing_dependency_output",
                f"{dependency.api_name} is missing {sorted(missing)}",
            )
        result = str(raw.get("result", ""))
        if result not in _SUCCESS_RESULTS:
            raise FluxAssetError(
                "dependency_failed",
                f"{dependency.api_name} result {result!r} is not successful",
            )
        entry = {key: raw[key] for key in dependency.required_outputs}
        for key, value in entry.items():
            if key.endswith("digest") or key.endswith("digests_json"):
                _validate_digest_container(
                    value, name=f"{dependency.api_name}.{key}"
                )
            if key == "immutable_references_json":
                entry[key] = _parse_json_object(
                    value, name=f"{dependency.api_name}.{key}"
                )
        if _contains_latest(entry):
            raise FluxAssetError(
                "mutable_reference", f"{dependency.api_name} returned latest"
            )
        normalized[dependency.api_name] = entry
    return normalized


def verify_replay(
    expected: Mapping[str, str], existing: Mapping[str, str]
) -> None:
    """Accept replay only when every immutable identity resolves identically."""

    if set(expected) != set(existing):
        raise FluxAssetError(
            "replay_identity_mismatch", "immutable replay identity set differs"
        )
    for identity, digest in expected.items():
        if not _DIGEST_RE.fullmatch(digest):
            raise FluxAssetError(
                "invalid_digest", f"expected digest for {identity} is invalid"
            )
        if existing.get(identity) != digest:
            raise FluxAssetError(
                "immutable_conflict", f"immutable identity {identity} conflicts"
            )


def _asset_references(normalized: Mapping[str, Any]) -> dict[str, Any]:
    references: dict[str, Any] = {}
    for api_name, output in normalized.items():
        if isinstance(output, Mapping) and "immutable_references_json" in output:
            references[api_name] = output["immutable_references_json"]
    return references


def build_handoff(
    plan: ReleasePlan,
    *,
    manifest_sha256: str,
    asset_references: Mapping[str, Any],
) -> dict[str, Any]:
    """Return a review-only handoff that can never authorize Flux mutation."""

    if not re.fullmatch(r"[0-9a-f]{64}", manifest_sha256):
        raise FluxAssetError(
            "invalid_manifest_digest",
            "manifest digest must be lowercase SHA-256",
        )
    return {
        "kind": "flux-infrastructure-assets",
        "request_id": plan.request_id,
        "product_id": plan.product_id,
        "source_sha": plan.admitted_sha,
        "release_version": plan.release_version,
        "release_manifest_sha256": manifest_sha256,
        "asset_references": dict(asset_references),
        "canary_id": plan.canary_id,
        "previous_known_good_policy": plan.previous_known_good_policy,
        "rollback_id": plan.rollback_id,
        "review_required": True,
        "canary_required": True,
        "mutation_authorized": False,
        "desired_state_change_requested": False,
        "cluster_credentials_included": False,
        "sops_credentials_included": False,
    }


def release(
    contract: Mapping[str, Any],
    *,
    request: Mapping[str, str],
    dependency_outputs: Mapping[str, Any],
) -> dict[str, str]:
    """Compose verified dependency outputs into registered flux.assets outputs."""

    plan = build_release_plan(
        contract,
        admitted_sha=request["admitted_sha"],
        product_id=request["product_id"],
        release_version=request["release_version"],
        operation=request["operation"],
        policy_path=request["policy_path"],
        request_id=request["request_id"],
        source_ref_type=request.get("source_ref_type", ""),
        source_ref_name=request.get("source_ref_name", ""),
    )
    if plan.operation == "plan":
        references = {
            "version_identity": plan.version_identity,
            "source_identity": plan.source_identity,
            "handoff": {
                "canary_id": plan.canary_id,
                "previous_known_good_policy": plan.previous_known_good_policy,
                "rollback_id": plan.rollback_id,
                "review_required": True,
                "mutation_authorized": False,
            },
        }
        manifest = {
            "schema_version": 1,
            "state": "planned",
            "plan": plan.as_dict(),
            "asset_references": references,
        }
        return {
            "result": "planned",
            "immutable_references_json": canonical_json(references),
            "release_manifest_sha256": canonical_sha256(manifest),
            "request_id": plan.request_id,
        }

    normalized = verify_dependency_outputs(plan, dependency_outputs)
    references = _asset_references(normalized)
    manifest = {
        "schema_version": 1,
        "state": "verified",
        "request": {
            "request_id": plan.request_id,
            "product_id": plan.product_id,
            "source_sha": plan.admitted_sha,
            "release_version": plan.release_version,
            "operation": plan.operation,
        },
        "dependencies": normalized,
        "asset_references": references,
        "bootstrap": dict(plan.bootstrap_policy),
        "selection_policy": {
            "canary_id": plan.canary_id,
            "previous_known_good_policy": plan.previous_known_good_policy,
            "rollback_id": plan.rollback_id,
            "review_required": True,
        },
    }
    digest = canonical_sha256(manifest)
    handoff = build_handoff(
        plan, manifest_sha256=digest, asset_references=references
    )
    immutable = {
        "version_identity": plan.version_identity,
        "source_identity": plan.source_identity,
        "assets": references,
        "flux_handoff": handoff,
    }
    return {
        "result": "verified",
        "immutable_references_json": canonical_json(immutable),
        "release_manifest_sha256": digest,
        "request_id": plan.request_id,
    }


def validate_source_contract(
    contract: Mapping[str, Any], *, product_id: str, source_root: Path
) -> dict[str, Any]:
    """Validate checked-in non-secret source before dependency execution."""

    products = _require_mapping(contract["products"], name="products")
    product = products.get(product_id)
    if not isinstance(product, Mapping):
        raise FluxAssetError(
            "unsupported_product", f"unsupported product {product_id!r}"
        )
    root = source_root.resolve()
    if product["kind"] == "runner-image-family":
        bases: dict[str, list[str]] = {}
        for member_raw in _require_list(product["members"], name="members"):
            member = _require_mapping(member_raw, name="member")
            dockerfile = root / str(member["dockerfile_path"])
            try:
                resolved = dockerfile.resolve(strict=True)
            except OSError as error:
                raise FluxAssetError(
                    "missing_source", f"{member['dockerfile_path']} is missing"
                ) from error
            if root not in resolved.parents:
                raise FluxAssetError(
                    "source_path_escape", "Dockerfile escaped admitted source"
                )
            bases[str(member["id"])] = list(
                validate_dockerfile_bases(resolved.read_text(encoding="utf-8"))
            )
        return {"kind": "runner-image-family", "bases": bases}

    chart_root = root / str(product["chart_root"])
    required = ["Chart.yaml", "values.yaml", "values.schema.json"]
    missing = [name for name in required if not (chart_root / name).is_file()]
    if missing:
        raise FluxAssetError(
            "missing_source", f"chart source is missing {missing}"
        )
    return {
        "kind": "runner-chart-bundle",
        "chart_root": str(product["chart_root"]),
    }


def cleanup_state(root: Path) -> None:
    """Remove issue-owned transient state without following a root symlink."""

    if root.is_symlink():
        root.unlink()
        return
    if root.exists():
        shutil.rmtree(root)


def verify_residue_absent(root: Path) -> None:
    if root.exists() or root.is_symlink():
        raise FluxAssetError(
            "residue_detected", f"transient state remains at {root}"
        )
