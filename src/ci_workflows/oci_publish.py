"""Trusted immutable OCI publication, replay safety, and registry read-back."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

_FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
_STABLE_SEMVER = re.compile(r"^(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)$")
_PRODUCT = re.compile(r"^[a-z0-9]+(?:[._-][a-z0-9]+)*$")
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_PLATFORM = re.compile(r"^linux/(?:amd64|arm64/v8)$")
_SAFE_CODE = re.compile(r"^[a-z][a-z0-9_]{2,95}$")
_REGISTRY_HOST = "ghcr.io"
_INDEX_MEDIA_TYPE = "application/vnd.oci.image.index.v1+json"
_MANIFEST_MEDIA_TYPE = "application/vnd.oci.image.manifest.v1+json"
_CONFIG_MEDIA_TYPE = "application/vnd.oci.image.config.v1+json"
_LAYER_MEDIA_TYPES = {
    "application/vnd.oci.image.layer.v1.tar",
    "application/vnd.oci.image.layer.v1.tar+gzip",
    "application/vnd.oci.image.layer.v1.tar+zstd",
}
_BUILD_RUNNERS = {
    "buildah-tiny": ("linux", "amd64", "buildah", "tiny"),
    "buildah-small": ("linux", "amd64", "buildah", "small"),
    "buildah-medium": ("linux", "amd64", "buildah", "medium"),
    "buildah-high": ("linux", "amd64", "buildah", "high"),
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


class OciPublishError(RuntimeError):
    """Fail-closed publication error carrying one stable safe code."""

    def __init__(self, code: str) -> None:
        if _SAFE_CODE.fullmatch(code) is None:
            raise ValueError("unsafe OCI publication error code")
        self.code = code
        super().__init__(code)


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise OciPublishError(code)


def _mapping(value: Any, code: str = "invalid_contract") -> Mapping[str, Any]:
    _require(isinstance(value, Mapping), code)
    return value


def _strings(value: Any, code: str = "invalid_contract") -> tuple[str, ...]:
    _require(isinstance(value, list), code)
    _require(all(isinstance(item, str) and item for item in value), code)
    _require(len(value) == len(set(value)), code)
    return tuple(value)


@dataclass(frozen=True)
class PublishRequest:
    repository: str
    admitted_sha: str
    release_authority_sha: str
    product_id: str
    release_version: str
    source_trust: str


@dataclass(frozen=True)
class PublishTarget:
    target_id: str
    source_repository: str
    platforms: tuple[str, ...]
    registry_repository: str
    version_reference: str
    source_reference: str
    metadata: Mapping[str, str]
    required_user: str | None
    required_entrypoint: tuple[str, ...]
    required_command: tuple[str, ...]
    required_ports: tuple[str, ...]


@dataclass(frozen=True)
class PublishPlan:
    repository: str
    admitted_sha: str
    product_id: str
    release_version: str
    source_trust: str
    runner_profile: str
    runs_on: tuple[str, ...]
    workspace_profile: str
    timeout_minutes: int
    targets: tuple[PublishTarget, ...]
    flux_asset: bool
    canary_id: str | None
    previous_known_good: str | None
    rollback_id: str | None

    def planning_outputs(self) -> dict[str, str]:
        return {
            "result": "planned",
            "source_sha": self.admitted_sha,
            "product_id": self.product_id,
            "release_version": self.release_version,
            "source_trust": self.source_trust,
            "runner_profile": self.runner_profile,
            "runs_on_json": json.dumps(list(self.runs_on), separators=(",", ":")),
            "workspace_profile": self.workspace_profile,
            "timeout_minutes": str(self.timeout_minutes),
            "repositories_json": json.dumps(
                {target.target_id: target.registry_repository for target in self.targets},
                sort_keys=True,
                separators=(",", ":"),
            ),
            "version_references_json": json.dumps(
                {target.target_id: target.version_reference for target in self.targets},
                sort_keys=True,
                separators=(",", ":"),
            ),
            "source_references_json": json.dumps(
                {target.target_id: target.source_reference for target in self.targets},
                sort_keys=True,
                separators=(",", ":"),
            ),
            "canary_id": self.canary_id or "",
            "previous_known_good": self.previous_known_good or "",
            "rollback_id": self.rollback_id or "",
            "failure_code": "",
        }


def _source_trust(environment: Mapping[str, str]) -> str:
    event = environment.get("GITHUB_EVENT_NAME", "")
    if event == "pull_request":
        return "untrusted-pr"
    if event in {"pull_request_target", "workflow_run"}:
        return "untrusted-dispatch"
    return "trusted-exact"


def request_from_environment(environment: Mapping[str, str]) -> PublishRequest:
    repository = environment.get("GITHUB_REPOSITORY", "")
    admitted_sha = environment.get("INPUT_ADMITTED_SHA", "")
    authority_sha = environment.get("INPUT_RELEASE_AUTHORITY_SHA", "")
    product_id = environment.get("INPUT_PRODUCT_ID", "")
    release_version = environment.get("INPUT_RELEASE_VERSION", "")
    _require(re.fullmatch(r"StreamScapeTV/[A-Za-z0-9_.-]+", repository) is not None, "unsupported_consumer")
    _require(_FULL_SHA.fullmatch(admitted_sha) is not None, "invalid_source")
    _require(_FULL_SHA.fullmatch(authority_sha) is not None, "release_authority_invalid")
    _require(admitted_sha == authority_sha, "release_authority_mismatch")
    _require(_PRODUCT.fullmatch(product_id) is not None, "invalid_product")
    _require(_STABLE_SEMVER.fullmatch(release_version) is not None, "invalid_version")
    source_trust = _source_trust(environment)
    _require(source_trust == "trusted-exact", "publication_untrusted")
    return PublishRequest(
        repository=repository,
        admitted_sha=admitted_sha,
        release_authority_sha=authority_sha,
        product_id=product_id,
        release_version=release_version,
        source_trust=source_trust,
    )


def load_product_contract(repository_root: Path) -> Mapping[str, Any]:
    try:
        value = json.loads((repository_root / "contracts/oci-products.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise OciPublishError("publication_dependency_missing") from error
    contract = _mapping(value)
    _require(contract.get("workflow_api") == "oci.build", "invalid_contract")
    _require(contract.get("publication") is False, "invalid_contract")
    _require(contract.get("registry_credentials") is False, "invalid_contract")
    _require(contract.get("artifact_policy") == "zero-default", "invalid_contract")
    _mapping(contract.get("platform_sets"))
    _mapping(contract.get("runner_profiles"))
    _mapping(contract.get("products"))
    return contract


def canonical_registry_repository(source_repository: str, target_id: str, target_count: int) -> str:
    owner, name = source_repository.split("/", 1)
    _require(owner == "StreamScapeTV", "unsupported_consumer")
    _require(_PRODUCT.fullmatch(target_id) is not None, "invalid_contract")
    base = f"{_REGISTRY_HOST}/{owner.lower()}/{name.lower()}"
    return base if target_count == 1 else f"{base}-{target_id}"


def resolve_plan(repository_root: Path, request: PublishRequest) -> PublishPlan:
    contract = load_product_contract(repository_root)
    products = _mapping(contract["products"])
    _require(request.product_id in products, "unsupported_product")
    product = _mapping(products[request.product_id])
    _require(product.get("repository") == request.repository, "unsupported_consumer")
    _require(product.get("adoption_ready") is True, "publication_not_ready")
    runner_profile = product.get("runner_profile")
    _require(isinstance(runner_profile, str) and runner_profile in _BUILD_RUNNERS, "invalid_contract")
    runner = _mapping(_mapping(contract["runner_profiles"]).get(runner_profile))
    labels = _strings(runner.get("labels"))
    _require(labels == _BUILD_RUNNERS[runner_profile], "invalid_contract")
    workspace_profile = product.get("workspace_profile")
    timeout_minutes = product.get("timeout_minutes")
    _require(isinstance(workspace_profile, str) and _PRODUCT.fullmatch(workspace_profile), "invalid_contract")
    _require(type(timeout_minutes) is int and 1 <= timeout_minutes <= 180, "invalid_contract")
    platform_sets = _mapping(contract["platform_sets"])
    metadata = _mapping(product.get("metadata"))
    _require(set(metadata) == {"title", "description", "licenses"}, "invalid_contract")
    _require(all(isinstance(metadata[key], str) and metadata[key] for key in metadata), "invalid_contract")
    raw_targets = product.get("targets")
    _require(isinstance(raw_targets, list) and raw_targets, "invalid_contract")
    targets: list[PublishTarget] = []
    count = len(raw_targets)
    for raw in raw_targets:
        target = _mapping(raw)
        target_id = target.get("target_id")
        platform_set = target.get("platform_set")
        _require(isinstance(target_id, str) and _PRODUCT.fullmatch(target_id), "invalid_contract")
        _require(isinstance(platform_set, str) and platform_set in platform_sets, "invalid_contract")
        platforms = _strings(platform_sets[platform_set])
        _require(platforms and all(_PLATFORM.fullmatch(item) for item in platforms), "invalid_contract")
        assertions = _mapping(target.get("assertions"))
        required_user = assertions.get("user")
        _require(required_user is None or isinstance(required_user, str), "invalid_contract")
        repository = canonical_registry_repository(request.repository, target_id, count)
        version_ref = f"{repository}:{request.release_version}"
        source_ref = f"{repository}:sha-{request.admitted_sha}"
        _require(":latest" not in version_ref and ":latest" not in source_ref, "mutable_tag_forbidden")
        targets.append(
            PublishTarget(
                target_id=target_id,
                source_repository=request.repository,
                platforms=platforms,
                registry_repository=repository,
                version_reference=version_ref,
                source_reference=source_ref,
                metadata={key: str(metadata[key]) for key in ("title", "description", "licenses")},
                required_user=required_user,
                required_entrypoint=_strings(assertions.get("entrypoint")),
                required_command=_strings(assertions.get("command")),
                required_ports=_strings(assertions.get("ports")),
            )
        )
    _require(len({target.target_id for target in targets}) == len(targets), "invalid_contract")
    flux_asset = product.get("flux_asset")
    _require(isinstance(flux_asset, bool), "invalid_contract")
    canary = product.get("canary_id")
    known_good = product.get("previous_known_good")
    rollback = product.get("rollback_id")
    if flux_asset:
        _require(all(isinstance(item, str) and item for item in (canary, known_good, rollback)), "invalid_contract")
    else:
        _require(all(item is None for item in (canary, known_good, rollback)), "invalid_contract")
    return PublishPlan(
        repository=request.repository,
        admitted_sha=request.admitted_sha,
        product_id=request.product_id,
        release_version=request.release_version,
        source_trust=request.source_trust,
        runner_profile=runner_profile,
        runs_on=labels,
        workspace_profile=workspace_profile,
        timeout_minutes=timeout_minutes,
        targets=tuple(targets),
        flux_asset=flux_asset,
        canary_id=canary,
        previous_known_good=known_good,
        rollback_id=rollback,
    )


def _tokenized_root(environment: Mapping[str, str], domain: str, prefix: str) -> Path:
    runner_temp = Path(environment.get("RUNNER_TEMP", ".ci-state"))
    run_id = environment.get("GITHUB_RUN_ID", "local")
    attempt = environment.get("GITHUB_RUN_ATTEMPT", "1")
    token = hashlib.sha256(f"{domain}:{run_id}:{attempt}".encode()).hexdigest()[:16]
    return runner_temp / f"{prefix}-{token}"


def publication_state_root(environment: Mapping[str, str]) -> Path:
    return _tokenized_root(environment, "oci-publish", "ciw-oci-publish")


def build_state_root(environment: Mapping[str, str]) -> Path:
    # Must match issue #16's deterministic per-run build state until the shared
    # runtime exposes this as a named internal helper after integration.
    return _tokenized_root(environment, "oci-build", "ciw-oci")


def _run(
    argv: Sequence[str],
    *,
    input_bytes: bytes | None = None,
    capture: bool = True,
    check: bool = True,
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        list(argv),
        input=input_bytes,
        capture_output=capture,
        check=check,
    )


def _authfile(root: Path) -> Path:
    return root / "registry-auth.json"


def _secure_existing_authfile(root: Path) -> Path:
    path = _authfile(root)
    try:
        mode = path.lstat().st_mode
    except OSError as error:
        raise OciPublishError("registry_auth_missing") from error
    _require(stat.S_ISREG(mode) and not stat.S_ISLNK(mode), "registry_auth_invalid")
    _require(mode & 0o077 == 0, "registry_auth_invalid")
    return path


def authenticate(
    plan: PublishPlan,
    environment: Mapping[str, str],
    username: str,
    token: str,
) -> dict[str, str]:
    _require(username == username.strip() and bool(username) and "\n" not in username and "\r" not in username, "registry_auth_invalid")
    _require(bool(token) and "\n" not in token and "\r" not in token, "registry_auth_invalid")
    _require(shutil.which("skopeo") is not None, "registry_tool_unavailable")
    root = publication_state_root(environment)
    _require(not root.exists() and not root.is_symlink(), "residue_detected")
    root.mkdir(parents=True, mode=0o700)
    authfile = _authfile(root)
    try:
        _run(
            [
                "skopeo",
                "login",
                "--authfile",
                str(authfile),
                "--username",
                username,
                "--password-stdin",
                _REGISTRY_HOST,
            ],
            input_bytes=token.encode("utf-8"),
        )
    except (OSError, subprocess.CalledProcessError) as error:
        raise OciPublishError("registry_auth_failed") from error
    try:
        authfile.chmod(0o600)
    except OSError as error:
        raise OciPublishError("registry_auth_invalid") from error
    _secure_existing_authfile(root)
    state = {
        "api": "oci.publish",
        "version": "1.0.0",
        "source_sha": plan.admitted_sha,
        "product_id": plan.product_id,
        "release_version": plan.release_version,
        "registry_host": _REGISTRY_HOST,
    }
    (root / "plan.json").write_text(json.dumps(state, sort_keys=True) + "\n", encoding="utf-8")
    (root / "plan.json").chmod(0o600)
    return {"result": "authenticated", "failure_code": ""}


def _blob_path(layout: Path, digest: str) -> Path:
    _require(_DIGEST.fullmatch(digest) is not None, "oci_layout_malformed")
    path = layout / "blobs" / "sha256" / digest.removeprefix("sha256:")
    _require(path.is_file() and not path.is_symlink(), "oci_layout_malformed")
    actual = "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
    _require(actual == digest, "oci_digest_mismatch")
    return path


def _json_blob(layout: Path, descriptor: Mapping[str, Any], allowed_media: set[str]) -> Mapping[str, Any]:
    media = descriptor.get("mediaType")
    digest = descriptor.get("digest")
    size = descriptor.get("size")
    _require(media in allowed_media and isinstance(digest, str) and type(size) is int and size >= 0, "oci_layout_malformed")
    path = _blob_path(layout, digest)
    _require(path.stat().st_size == size, "oci_layout_malformed")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise OciPublishError("oci_layout_malformed") from error
    return _mapping(value, "oci_layout_malformed")


def _root_descriptor(layout: Path, ref_name: str) -> Mapping[str, Any]:
    try:
        root = json.loads((layout / "index.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise OciPublishError("oci_layout_malformed") from error
    root = _mapping(root, "oci_layout_malformed")
    manifests = root.get("manifests")
    _require(root.get("schemaVersion") == 2 and isinstance(manifests, list) and manifests, "oci_layout_malformed")
    matches: list[Mapping[str, Any]] = []
    for item in manifests:
        descriptor = _mapping(item, "oci_layout_malformed")
        annotations = descriptor.get("annotations") or {}
        if isinstance(annotations, Mapping) and annotations.get("org.opencontainers.image.ref.name") == ref_name:
            matches.append(descriptor)
    if not matches and len(manifests) == 1:
        matches = [_mapping(manifests[0], "oci_layout_malformed")]
    _require(len(matches) == 1, "oci_layout_malformed")
    descriptor = matches[0]
    _require(isinstance(descriptor.get("digest"), str) and _DIGEST.fullmatch(descriptor["digest"]), "oci_layout_malformed")
    _blob_path(layout, descriptor["digest"])
    return descriptor


def _platform_name(config: Mapping[str, Any]) -> str:
    os_name = config.get("os")
    arch = config.get("architecture")
    variant = config.get("variant")
    _require(isinstance(os_name, str) and isinstance(arch, str), "oci_layout_malformed")
    _require(variant is None or isinstance(variant, str), "oci_layout_malformed")
    name = f"{os_name}/{arch}" + (f"/{variant}" if variant else "")
    _require(_PLATFORM.fullmatch(name) is not None, "oci_layout_malformed")
    return name


def _validate_runtime(target: PublishTarget, config: Mapping[str, Any], labels: Mapping[str, Any]) -> None:
    _require(set(labels) >= _REQUIRED_LABELS, "metadata_mismatch")
    expected = {
        "dev.streamscape.product": target.target_id,
        "org.opencontainers.image.description": target.metadata["description"],
        "org.opencontainers.image.licenses": target.metadata["licenses"],
        "org.opencontainers.image.revision": target.source_reference.rsplit("sha-", 1)[1],
        "org.opencontainers.image.source": f"https://github.com/{target.source_repository}",
        "org.opencontainers.image.title": target.metadata["title"],
        "org.opencontainers.image.version": target.version_reference.rsplit(":", 1)[1],
    }
    for key, value in expected.items():
        _require(labels.get(key) == value, "metadata_mismatch")
    created = labels.get("org.opencontainers.image.created")
    _require(isinstance(created, str) and created.endswith("Z") and "T" in created, "metadata_mismatch")
    runtime = _mapping(config.get("config") or {}, "oci_layout_malformed")
    if target.required_user is not None:
        _require(runtime.get("User", "") == target.required_user, "assertion_failed")
    if target.required_entrypoint:
        _require(tuple(runtime.get("Entrypoint") or ()) == target.required_entrypoint, "assertion_failed")
    if target.required_command:
        _require(tuple(runtime.get("Cmd") or ()) == target.required_command, "assertion_failed")
    if target.required_ports:
        ports = runtime.get("ExposedPorts") or {}
        _require(isinstance(ports, Mapping), "assertion_failed")
        _require(tuple(sorted(ports)) == tuple(sorted(target.required_ports)), "assertion_failed")


def inspect_layout(layout: Path, target: PublishTarget, ref_name: str) -> Mapping[str, Any]:
    _require(layout.is_dir() and not layout.is_symlink(), "oci_layout_malformed")
    descriptor = _root_descriptor(layout, ref_name)
    top_digest = descriptor["digest"]
    media = descriptor.get("mediaType")
    if media == _INDEX_MEDIA_TYPE:
        index = _json_blob(layout, descriptor, {_INDEX_MEDIA_TYPE})
        manifests = index.get("manifests")
        _require(index.get("schemaVersion") == 2 and isinstance(manifests, list), "oci_layout_malformed")
        manifest_descriptors = tuple(_mapping(item, "oci_layout_malformed") for item in manifests)
    elif media == _MANIFEST_MEDIA_TYPE:
        manifest_descriptors = (descriptor,)
    else:
        raise OciPublishError("oci_layout_malformed")
    rows: dict[str, Any] = {}
    for manifest_descriptor in manifest_descriptors:
        manifest = _json_blob(layout, manifest_descriptor, {_MANIFEST_MEDIA_TYPE})
        _require(manifest.get("schemaVersion") == 2, "oci_layout_malformed")
        config_descriptor = _mapping(manifest.get("config"), "oci_layout_malformed")
        config = _json_blob(layout, config_descriptor, {_CONFIG_MEDIA_TYPE})
        platform = _platform_name(config)
        _require(platform not in rows, "oci_layout_malformed")
        runtime = _mapping(config.get("config") or {}, "oci_layout_malformed")
        labels = _mapping(runtime.get("Labels") or {}, "metadata_mismatch")
        _validate_runtime(target, config, labels)
        layers = manifest.get("layers")
        _require(isinstance(layers, list), "oci_layout_malformed")
        layer_digests: list[str] = []
        for raw_layer in layers:
            layer = _mapping(raw_layer, "oci_layout_malformed")
            _require(layer.get("mediaType") in _LAYER_MEDIA_TYPES, "oci_layout_malformed")
            digest = layer.get("digest")
            _require(isinstance(digest, str), "oci_layout_malformed")
            _blob_path(layout, digest)
            layer_digests.append(digest)
        rows[platform] = {
            "manifest_digest": manifest_descriptor["digest"],
            "config_digest": config_descriptor["digest"],
            "layer_digests": layer_digests,
            "labels": dict(sorted((str(k), str(v)) for k, v in labels.items())),
        }
    _require(tuple(sorted(rows)) == tuple(sorted(target.platforms)), "platform_mismatch")
    return {
        "manifest_digest": top_digest,
        "platforms": {key: rows[key] for key in sorted(rows)},
    }


def raw_manifest_digest(payload: bytes) -> str:
    _require(bool(payload), "registry_inspection_failed")
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def replay_decision(local_digest: str, version_digest: str | None, source_digest: str | None) -> tuple[bool, bool, bool]:
    _require(_DIGEST.fullmatch(local_digest) is not None, "oci_digest_mismatch")
    for remote in (version_digest, source_digest):
        _require(remote is None or _DIGEST.fullmatch(remote) is not None, "registry_inspection_failed")
        if remote is not None and remote != local_digest:
            raise OciPublishError("immutable_reference_conflict")
    return version_digest is None, source_digest is None, version_digest is not None or source_digest is not None


def _inspect_remote_digest(reference: str, authfile: Path) -> str | None:
    try:
        result = _run(
            ["skopeo", "inspect", "--authfile", str(authfile), "--raw", f"docker://{reference}"],
            check=False,
        )
    except OSError as error:
        raise OciPublishError("registry_inspection_failed") from error
    if result.returncode == 0:
        return raw_manifest_digest(result.stdout)
    stderr = result.stderr.lower()
    if any(marker in stderr for marker in (b"manifest unknown", b"name unknown", b"not found")):
        return None
    raise OciPublishError("registry_inspection_failed")


def _copy(source: str, destination: str, authfile: Path) -> None:
    try:
        _run(["skopeo", "copy", "--all", "--authfile", str(authfile), source, destination])
    except (OSError, subprocess.CalledProcessError) as error:
        raise OciPublishError("registry_copy_failed") from error


def _write_state(path: Path, value: Mapping[str, Any]) -> None:
    path.write_text(json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    path.chmod(0o600)


def publish(plan: PublishPlan, environment: Mapping[str, str]) -> dict[str, str]:
    root = publication_state_root(environment)
    authfile = _secure_existing_authfile(root)
    build_root = build_state_root(environment)
    results: dict[str, Any] = {}
    any_replayed = False
    for target in plan.targets:
        layout = build_root / "layouts" / target.target_id
        local = inspect_layout(layout, target, "validation")
        local_digest = str(local["manifest_digest"])
        version_digest = _inspect_remote_digest(target.version_reference, authfile)
        source_digest = _inspect_remote_digest(target.source_reference, authfile)
        publish_version, publish_source, replayed = replay_decision(local_digest, version_digest, source_digest)
        any_replayed = any_replayed or replayed
        if publish_version:
            _copy(f"oci:{layout}:validation", f"docker://{target.version_reference}", authfile)
        if publish_source:
            _copy(f"oci:{layout}:validation", f"docker://{target.source_reference}", authfile)
        verified_version = _inspect_remote_digest(target.version_reference, authfile)
        verified_source = _inspect_remote_digest(target.source_reference, authfile)
        _require(verified_version == local_digest and verified_source == local_digest, "registry_digest_mismatch")
        results[target.target_id] = {
            "repository": target.registry_repository,
            "version_reference": target.version_reference,
            "source_reference": target.source_reference,
            "manifest_digest": local_digest,
            "local": local,
            "replayed": replayed,
        }
    _write_state(root / "publication.json", {"targets": results})
    return {
        "result": "published",
        "manifest_digests_json": json.dumps({key: row["manifest_digest"] for key, row in sorted(results.items())}, separators=(",", ":")),
        "replayed": str(any_replayed).lower(),
        "failure_code": "",
    }


def read_back(plan: PublishPlan, environment: Mapping[str, str]) -> dict[str, str]:
    root = publication_state_root(environment)
    authfile = _secure_existing_authfile(root)
    try:
        published = _mapping(json.loads((root / "publication.json").read_text(encoding="utf-8")), "publication_state_missing")
    except (OSError, json.JSONDecodeError) as error:
        raise OciPublishError("publication_state_missing") from error
    rows = _mapping(published.get("targets"), "publication_state_missing")
    readback_root = root / "readback"
    _require(not readback_root.exists() and not readback_root.is_symlink(), "residue_detected")
    readback_root.mkdir(mode=0o700)
    verified: dict[str, Any] = {}
    for target in plan.targets:
        row = _mapping(rows.get(target.target_id), "publication_state_missing")
        destination = readback_root / target.target_id
        _copy(f"docker://{target.version_reference}", f"oci:{destination}:readback", authfile)
        remote = inspect_layout(destination, target, "readback")
        local = _mapping(row.get("local"), "publication_state_missing")
        _require(remote == local, "registry_readback_mismatch")
        source_digest = _inspect_remote_digest(target.source_reference, authfile)
        _require(source_digest == remote["manifest_digest"], "registry_readback_mismatch")
        verified[target.target_id] = {
            "repository": target.registry_repository,
            "version_reference": target.version_reference,
            "source_reference": target.source_reference,
            "manifest_digest": remote["manifest_digest"],
            "platforms": remote["platforms"],
            "replayed": bool(row.get("replayed")),
        }
    _write_state(root / "readback.json", {"targets": verified})
    return {
        "result": "read-back",
        "manifest_digests_json": json.dumps({key: row["manifest_digest"] for key, row in sorted(verified.items())}, separators=(",", ":")),
        "platform_digests_json": json.dumps(
            {key: row["platforms"] for key, row in sorted(verified.items())},
            sort_keys=True,
            separators=(",", ":"),
        ),
        "failure_code": "",
    }


def verify(plan: PublishPlan, environment: Mapping[str, str]) -> dict[str, str]:
    root = publication_state_root(environment)
    try:
        readback = _mapping(json.loads((root / "readback.json").read_text(encoding="utf-8")), "publication_state_missing")
    except (OSError, json.JSONDecodeError) as error:
        raise OciPublishError("publication_state_missing") from error
    rows = _mapping(readback.get("targets"), "publication_state_missing")
    _require(set(rows) == {target.target_id for target in plan.targets}, "publication_state_missing")
    repositories: dict[str, str] = {}
    versions: dict[str, str] = {}
    sources: dict[str, str] = {}
    manifests: dict[str, str] = {}
    platforms: dict[str, Any] = {}
    replayed = False
    for target in plan.targets:
        row = _mapping(rows[target.target_id], "publication_state_missing")
        _require(row.get("repository") == target.registry_repository, "registry_readback_mismatch")
        _require(row.get("version_reference") == target.version_reference, "registry_readback_mismatch")
        _require(row.get("source_reference") == target.source_reference, "registry_readback_mismatch")
        digest = row.get("manifest_digest")
        _require(isinstance(digest, str) and _DIGEST.fullmatch(digest), "registry_readback_mismatch")
        repositories[target.target_id] = target.registry_repository
        versions[target.target_id] = target.version_reference
        sources[target.target_id] = target.source_reference
        manifests[target.target_id] = digest
        platforms[target.target_id] = row.get("platforms")
        replayed = replayed or bool(row.get("replayed"))
    evidence = {
        "api": "oci.publish",
        "version": "1.0.0",
        "source": plan.admitted_sha,
        "product": plan.product_id,
        "release_version": plan.release_version,
        "manifests": manifests,
        "platforms": platforms,
    }
    evidence_id = hashlib.sha256(json.dumps(evidence, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {
        "result": "success",
        "source_sha": plan.admitted_sha,
        "product_id": plan.product_id,
        "release_version": plan.release_version,
        "repositories_json": json.dumps(repositories, sort_keys=True, separators=(",", ":")),
        "version_references_json": json.dumps(versions, sort_keys=True, separators=(",", ":")),
        "source_references_json": json.dumps(sources, sort_keys=True, separators=(",", ":")),
        "manifest_digests_json": json.dumps(manifests, sort_keys=True, separators=(",", ":")),
        "platform_digests_json": json.dumps(platforms, sort_keys=True, separators=(",", ":")),
        "replayed": str(replayed).lower(),
        "evidence_id": evidence_id,
        "canary_id": plan.canary_id or "",
        "previous_known_good": plan.previous_known_good or "",
        "rollback_id": plan.rollback_id or "",
        "failure_code": "",
    }


def cleanup(environment: Mapping[str, str]) -> None:
    root = publication_state_root(environment)
    try:
        mode = root.lstat().st_mode
    except FileNotFoundError:
        return
    except OSError as error:
        raise OciPublishError("cleanup_failed") from error
    if stat.S_ISLNK(mode):
        root.unlink()
        raise OciPublishError("cleanup_failed")
    _require(stat.S_ISDIR(mode), "cleanup_failed")
    try:
        shutil.rmtree(root)
    except OSError as error:
        raise OciPublishError("cleanup_failed") from error


def residue(environment: Mapping[str, str]) -> None:
    root = publication_state_root(environment)
    _require(not root.exists() and not root.is_symlink(), "residue_detected")
