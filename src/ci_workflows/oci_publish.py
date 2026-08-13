"""Trusted immutable OCI publication, replay safety, and registry read-back."""
from __future__ import annotations

import hashlib
import json
import os
import re
import selectors
import shutil
import signal
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence

from . import oci_execution as _execution
from .oci_input_contract import OciTargetInputLock
from .oci_types import (
    OciRegistryWritePolicy,
    has_valid_oci_tag_length,
    is_canonical_publication_repository,
)

_FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
_STABLE_SEMVER = re.compile(r"^(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)$")
_PRODUCT = re.compile(r"^[a-z0-9]+(?:[._-][a-z0-9]+)*$")
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_RAW_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_SAFE_INPUT_ID = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
_EXACT_INPUT_REFERENCE = re.compile(
    r"^[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?/"
    r"[a-z0-9]+(?:[._-][a-z0-9]+)*"
    r"(?:/[a-z0-9]+(?:[._-][a-z0-9]+)*)*"
    r"(?::[A-Za-z0-9_][A-Za-z0-9._-]{0,127})?"
    r"@sha256:[0-9a-f]{64}$"
)
_PLATFORM = re.compile(r"^linux/(?:amd64|arm64/v8)$")
_TOOL_NAME = re.compile(r"^[A-Za-z0-9._+-]+$")
_PORT = re.compile(r"^[0-9]{1,5}/(?:tcp|udp)$")
_CREATED_LABEL = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(?:\.[0-9]{1,9})?Z$"
)
_SAFE_CODE = re.compile(r"^[a-z][a-z0-9_]{2,95}$")
_INDEX_MEDIA_TYPE = "application/vnd.oci.image.index.v1+json"
_MANIFEST_MEDIA_TYPE = "application/vnd.oci.image.manifest.v1+json"
_CONFIG_MEDIA_TYPE = "application/vnd.oci.image.config.v1+json"
_LAYER_MEDIA_TYPES = {
    "application/vnd.oci.image.layer.v1.tar",
    "application/vnd.oci.image.layer.v1.tar+gzip",
}
_BUILD_RUNNERS = {
    "buildah-tiny": ("linux", "amd64", "buildah", "tiny"),
    "buildah-small": ("linux", "amd64", "buildah", "small"),
    "buildah-medium": ("linux", "amd64", "buildah", "medium"),
    "buildah-high": ("linux", "amd64", "buildah", "high"),
}
_SUBPROCESS_ENVIRONMENT = (
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "PATH",
    "SSL_CERT_DIR",
    "SSL_CERT_FILE",
)
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
_MAX_AUTHFILE_BYTES = 64 * 1024
_MAX_PLAN_STATE_BYTES = 64 * 1024
_MAX_PHASE_STATE_BYTES = 1024 * 1024
_REGISTRIES_CONF_BYTES = (
    b'unqualified-search-registries = []\nshort-name-mode = "disabled"\n'
)
_REGISTRY_WRITE_ENFORCEMENT = "server-side-create-only-tags-v1"
_REGISTRY_WRITE_AUTHORITY = "StreamScapeTV/flux"
_MAX_PUBLIC_TARGETS = 2
_MAX_PUBLIC_PLATFORMS = 2
_MAX_PUBLIC_LAYERS = 256
_MAX_PUBLIC_JSON_OUTPUT_BYTES = {
    "manifest_digests_json": 4 * 1024,
    "platform_digests_json": 192 * 1024,
    "immutable_references_json": 192 * 1024,
}
_MAX_PUBLIC_JSON_OUTPUT_TOTAL_BYTES = 384 * 1024
_MAX_LAYOUT_JSON_BYTES = 32 * 1024 * 1024
_MAX_LAYOUT_ROOT_BYTES = 64 * 1024
_MAX_REGISTRY_RAW_MANIFEST_BYTES = 32 * 1024 * 1024
_MAX_REGISTRY_INSPECTION_STDERR_BYTES = 64 * 1024
_MAX_REGISTRY_LOGIN_STDOUT_BYTES = 4 * 1024
_MAX_REGISTRY_LOGIN_STDERR_BYTES = 16 * 1024
_MAX_REGISTRY_COPY_STDOUT_BYTES = 4 * 1024
_MAX_REGISTRY_COPY_STDERR_BYTES = 64 * 1024
_MAX_REGISTRY_CAPTURE_BYTES = 32 * 1024 * 1024
_REGISTRY_CAPTURE_CHUNK_BYTES = 64 * 1024
_METADATA_LABEL_MAX_LENGTHS = {
    "dev.streamscape.product": 64,
    "org.opencontainers.image.created": 64,
    "org.opencontainers.image.description": 512,
    "org.opencontainers.image.licenses": 256,
    "org.opencontainers.image.revision": 40,
    "org.opencontainers.image.source": 256,
    "org.opencontainers.image.title": 256,
    "org.opencontainers.image.version": 128,
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


def _validate_resolved_input_evidence(
    value: Any,
    target: PublishTarget,
    code: str,
    *,
    expected_lock: OciTargetInputLock | None = None,
) -> dict[str, Any]:
    """Validate and normalize one closed, redacted #150 input-evidence row."""

    evidence = _mapping(value, code)
    _require(
        set(evidence)
        == {
            "lock_digest",
            "input_policy_id",
            "bases",
            "external_inputs",
            "evidence_id",
        },
        code,
    )
    lock_digest = evidence.get("lock_digest")
    policy_id = evidence.get("input_policy_id")
    bases = evidence.get("bases")
    external_inputs = evidence.get("external_inputs")
    evidence_id = evidence.get("evidence_id")
    _require(
        (
            lock_digest == "none"
            or isinstance(lock_digest, str)
            and _DIGEST.fullmatch(lock_digest) is not None
        )
        and policy_id == target.input_policy_id
        and isinstance(bases, list)
        and len(bases) <= 64
        and isinstance(external_inputs, list)
        and len(external_inputs) <= 64
        and isinstance(evidence_id, str)
        and _RAW_SHA256.fullmatch(evidence_id) is not None,
        code,
    )
    normalized_bases: list[dict[str, Any]] = []
    stage_ids: set[str] = set()
    for raw in bases:
        base = _mapping(raw, code)
        _require(
            set(base)
            == {"stage_id", "declared_reference", "root_digest", "platforms"},
            code,
        )
        stage_id = base.get("stage_id")
        declared = base.get("declared_reference")
        root_digest = base.get("root_digest")
        platforms = base.get("platforms")
        _require(
            isinstance(stage_id, str)
            and _SAFE_INPUT_ID.fullmatch(stage_id) is not None
            and stage_id not in stage_ids
            and isinstance(declared, str)
            and len(declared) <= 512
            and _EXACT_INPUT_REFERENCE.fullmatch(declared) is not None
            and isinstance(root_digest, str)
            and _DIGEST.fullmatch(root_digest) is not None
            and declared.rsplit("@", 1)[1] == root_digest
            and isinstance(platforms, list)
            and 1 <= len(platforms) <= 2,
            code,
        )
        stage_ids.add(stage_id)
        normalized_platforms: list[dict[str, str]] = []
        platform_names: set[str] = set()
        for raw_platform in platforms:
            platform = _mapping(raw_platform, code)
            _require(
                set(platform) == {"platform", "manifest_digest", "config_digest"},
                code,
            )
            platform_name = platform.get("platform")
            manifest_digest = platform.get("manifest_digest")
            config_digest = platform.get("config_digest")
            _require(
                isinstance(platform_name, str)
                and platform_name in target.platforms
                and platform_name not in platform_names
                and isinstance(manifest_digest, str)
                and _DIGEST.fullmatch(manifest_digest) is not None
                and isinstance(config_digest, str)
                and _DIGEST.fullmatch(config_digest) is not None,
                code,
            )
            platform_names.add(platform_name)
            normalized_platforms.append(
                {
                    "platform": platform_name,
                    "manifest_digest": manifest_digest,
                    "config_digest": config_digest,
                }
            )
        _require(
            [item["platform"] for item in normalized_platforms]
            == sorted(platform_names),
            code,
        )
        _require(platform_names <= set(target.platforms), code)
        normalized_bases.append(
            {
                "stage_id": stage_id,
                "declared_reference": declared,
                "root_digest": root_digest,
                "platforms": normalized_platforms,
            }
        )
    normalized_external_inputs: list[dict[str, Any]] = []
    input_ids: set[str] = set()
    for raw in external_inputs:
        external = _mapping(raw, code)
        _require(set(external) == {"input_id", "digest", "size_bytes"}, code)
        input_id = external.get("input_id")
        digest = external.get("digest")
        size_bytes = external.get("size_bytes")
        _require(
            isinstance(input_id, str)
            and _SAFE_INPUT_ID.fullmatch(input_id) is not None
            and input_id not in input_ids
            and isinstance(digest, str)
            and _DIGEST.fullmatch(digest) is not None
            and type(size_bytes) is int
            and 0 <= size_bytes <= 1_073_741_824,
            code,
        )
        input_ids.add(input_id)
        normalized_external_inputs.append(
            {"input_id": input_id, "digest": digest, "size_bytes": size_bytes}
        )
    _require(
        lock_digest != "none"
        or (
            policy_id == "scratch-only-v1"
            and not normalized_bases
            and not normalized_external_inputs
        ),
        code,
    )
    payload = {
        "lock_digest": lock_digest,
        "input_policy_id": policy_id,
        "bases": normalized_bases,
        "external_inputs": normalized_external_inputs,
    }
    expected_id = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    _require(evidence_id == expected_id, code)
    if expected_lock is not None:
        locked_bases = tuple(
            base for base in expected_lock.bases if base.kind == "external"
        )
        _require(
            expected_lock.target_id == target.target_id
            and expected_lock.input_policy_id == target.input_policy_id
            and expected_lock.platforms == target.platforms
            and lock_digest == expected_lock.lock_digest
            and tuple(item["stage_id"] for item in normalized_bases)
            == tuple(base.stage_id for base in locked_bases),
            code,
        )
        for resolved, locked in zip(
            normalized_bases, locked_bases, strict=True
        ):
            _require(
                resolved["declared_reference"] == locked.declared_reference
                and resolved["root_digest"]
                == locked.declared_reference.rsplit("@", 1)[1]
                and tuple(
                    platform["platform"] for platform in resolved["platforms"]
                )
                == locked.platforms,
                code,
            )
            _require(
                tuple(
                    (
                        platform["platform"],
                        platform["manifest_digest"],
                        platform["config_digest"],
                    )
                    for platform in resolved["platforms"]
                )
                == tuple(
                    (
                        identity.platform,
                        identity.manifest_digest,
                        identity.config_digest,
                    )
                    for identity in locked.platform_identities
                ),
                code,
            )
        _require(
            tuple(item["input_id"] for item in normalized_external_inputs)
            == tuple(item.input_id for item in expected_lock.external_inputs),
            code,
        )
        for resolved, locked in zip(
            normalized_external_inputs,
            expected_lock.external_inputs,
            strict=True,
        ):
            _require(
                resolved["digest"] == f"sha256:{locked.sha256}"
                and resolved["size_bytes"] <= locked.maximum_bytes,
                code,
            )
    return {**payload, "evidence_id": evidence_id}


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
    input_policy_id: str = "scratch-only-v1"


def _project_metadata_labels(
    target: PublishTarget,
    labels: Mapping[str, Any],
    code: str,
) -> dict[str, str]:
    """Return only the eight bounded labels in the public evidence contract."""

    _require(set(labels) == _REQUIRED_LABELS, code)
    projected: dict[str, str] = {}
    for key in sorted(_REQUIRED_LABELS):
        value = labels.get(key)
        _require(
            isinstance(value, str)
            and bool(value)
            and len(value) <= _METADATA_LABEL_MAX_LENGTHS[key]
            and all(character not in value for character in ("\x00", "\r", "\n")),
            code,
        )
        projected[key] = value
    _require(
        _PRODUCT.fullmatch(projected["dev.streamscape.product"]) is not None
        and projected["dev.streamscape.product"] == target.target_id
        and _CREATED_LABEL.fullmatch(
            projected["org.opencontainers.image.created"]
        )
        is not None
        and _FULL_SHA.fullmatch(
            projected["org.opencontainers.image.revision"]
        )
        is not None
        and projected["org.opencontainers.image.source"]
        == f"https://github.com/{target.source_repository}"
        and _STABLE_SEMVER.fullmatch(
            projected["org.opencontainers.image.version"]
        )
        is not None
        and has_valid_oci_tag_length(
            projected["org.opencontainers.image.version"]
        ),
        code,
    )
    return projected


def _validate_public_platform_evidence(
    target: PublishTarget,
    value: Any,
) -> dict[str, Any]:
    """Validate one target's platform proof at the public-output boundary."""

    platforms = _mapping(value, "publication_output_invalid")
    expected = set(target.platforms)
    _require(
        1 <= len(expected) <= _MAX_PUBLIC_PLATFORMS
        and len(expected) == len(target.platforms)
        and set(platforms) == expected,
        "publication_output_invalid",
    )
    normalized: dict[str, Any] = {}
    for platform_name in sorted(expected):
        row = _mapping(platforms[platform_name], "publication_output_invalid")
        _require(
            set(row)
            == {"manifest_digest", "config_digest", "layer_digests", "labels"},
            "publication_output_invalid",
        )
        manifest_digest = row.get("manifest_digest")
        config_digest = row.get("config_digest")
        layers = row.get("layer_digests")
        labels = _mapping(row.get("labels"), "publication_output_invalid")
        _require(
            isinstance(manifest_digest, str)
            and _DIGEST.fullmatch(manifest_digest) is not None
            and isinstance(config_digest, str)
            and _DIGEST.fullmatch(config_digest) is not None
            and isinstance(layers, list)
            and 1 <= len(layers) <= _MAX_PUBLIC_LAYERS
            and all(
                isinstance(digest, str) and _DIGEST.fullmatch(digest) is not None
                for digest in layers
            ),
            "publication_output_invalid",
        )
        normalized[platform_name] = {
            "manifest_digest": manifest_digest,
            "config_digest": config_digest,
            "layer_digests": list(layers),
            "labels": _project_metadata_labels(
                target,
                labels,
                "publication_output_invalid",
            ),
        }
    return normalized


def _validate_assertion_vector(value: Any) -> dict[str, Any]:
    vector = _mapping(value, "publication_output_invalid")
    count = vector.get("count")
    digest = vector.get("digest")
    _require(
        set(vector) == {"count", "digest"}
        and type(count) is int
        and 0 <= count <= 64
        and isinstance(digest, str)
        and _DIGEST.fullmatch(digest) is not None,
        "publication_output_invalid",
    )
    return {"count": count, "digest": digest}


def _validate_assertion_strings(
    value: Any,
    *,
    paths: bool = False,
) -> list[str]:
    _require(
        isinstance(value, list)
        and len(value) <= 64
        and len(value) == len(set(value))
        and all(isinstance(item, str) and bool(item) for item in value),
        "publication_output_invalid",
    )
    result: list[str] = []
    for item in value:
        if paths:
            parsed = PurePosixPath(item)
            _require(
                len(item) <= 4096
                and parsed.is_absolute()
                and item.startswith("/")
                and not item.startswith("//")
                and item == parsed.as_posix()
                and ".." not in parsed.parts
                and item != "/"
                and all(character not in item for character in ("\x00", "\r", "\n")),
                "publication_output_invalid",
            )
        else:
            _require(
                len(item) <= 64 and _TOOL_NAME.fullmatch(item) is not None,
                "publication_output_invalid",
            )
        result.append(item)
    return result


def _validate_public_assertions(
    target: PublishTarget,
    value: Any,
) -> dict[str, Any]:
    assertions = _mapping(value, "publication_output_invalid")
    _require(
        set(assertions)
        == {
            "result",
            "verified_platforms",
            "contract_digest",
            "runtime",
            "filesystem",
            "healthcheck",
        }
        and assertions.get("result") == "passed"
        and assertions.get("verified_platforms") == list(target.platforms),
        "publication_output_invalid",
    )
    contract_digest = assertions.get("contract_digest")
    _require(
        isinstance(contract_digest, str)
        and _DIGEST.fullmatch(contract_digest) is not None,
        "publication_output_invalid",
    )
    runtime = _mapping(assertions.get("runtime"), "publication_output_invalid")
    _require(
        set(runtime) == {"user", "entrypoint", "command", "ports"}
        and (
            runtime.get("user") is None
            or isinstance(runtime.get("user"), str)
            and len(runtime["user"]) <= 256
        )
        and isinstance(runtime.get("ports"), list)
        and len(runtime["ports"]) <= 64
        and len(runtime["ports"]) == len(set(runtime["ports"]))
        and all(
            isinstance(port, str) and _PORT.fullmatch(port) is not None
            for port in runtime["ports"]
        ),
        "publication_output_invalid",
    )
    normalized_runtime = {
        "user": runtime.get("user"),
        "entrypoint": _validate_assertion_vector(runtime.get("entrypoint")),
        "command": _validate_assertion_vector(runtime.get("command")),
        "ports": list(runtime["ports"]),
    }
    filesystem = _mapping(
        assertions.get("filesystem"), "publication_output_invalid"
    )
    _require(
        set(filesystem)
        == {
            "required_files",
            "required_tools",
            "required_executables",
            "forbidden_tools",
            "forbidden_paths",
        },
        "publication_output_invalid",
    )
    normalized_filesystem = {
        "required_files": _validate_assertion_strings(
            filesystem.get("required_files"), paths=True
        ),
        "required_tools": _validate_assertion_strings(
            filesystem.get("required_tools")
        ),
        "required_executables": _validate_assertion_strings(
            filesystem.get("required_executables"), paths=True
        ),
        "forbidden_tools": _validate_assertion_strings(
            filesystem.get("forbidden_tools")
        ),
        "forbidden_paths": _validate_assertion_strings(
            filesystem.get("forbidden_paths"), paths=True
        ),
    }
    health = _mapping(assertions.get("healthcheck"), "publication_output_invalid")
    if health.get("mode") == "absent":
        _require(set(health) == {"mode"}, "publication_output_invalid")
        normalized_health: dict[str, Any] = {"mode": "absent"}
    else:
        _require(
            set(health)
            == {
                "mode",
                "test_mode",
                "test",
                "interval_nanoseconds",
                "timeout_nanoseconds",
                "start_period_nanoseconds",
                "start_interval_nanoseconds",
                "retries",
            }
            and health.get("mode") == "exact"
            and health.get("test_mode") in {"CMD", "CMD-SHELL", "NONE"}
            and all(
                type(health.get(field)) is int
                and 0 <= health[field] <= 9_223_372_036_854_775_807
                for field in (
                    "interval_nanoseconds",
                    "timeout_nanoseconds",
                    "start_period_nanoseconds",
                    "start_interval_nanoseconds",
                )
            )
            and type(health.get("retries")) is int
            and 0 <= health["retries"] <= 2_147_483_647,
            "publication_output_invalid",
        )
        normalized_health = {
            **health,
            "test": _validate_assertion_vector(health.get("test")),
        }
    return {
        "result": "passed",
        "verified_platforms": list(target.platforms),
        "contract_digest": contract_digest,
        "runtime": normalized_runtime,
        "filesystem": normalized_filesystem,
        "healthcheck": normalized_health,
    }


def _bounded_public_json(name: str, value: Any) -> str:
    """Serialize one public JSON output without truncation under its byte cap."""

    _require(name in _MAX_PUBLIC_JSON_OUTPUT_BYTES, "publication_output_invalid")
    try:
        encoded = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError) as error:
        raise OciPublishError("publication_output_invalid") from error
    _require(
        len(encoded) <= _MAX_PUBLIC_JSON_OUTPUT_BYTES[name],
        "publication_output_invalid",
    )
    return encoded.decode("utf-8")


def _validate_public_registry_write_policy(
    value: Any,
    targets: Sequence[PublishTarget],
) -> dict[str, str]:
    policy = _mapping(value, "publication_output_invalid")
    _require(
        set(policy)
        == {
            "policy_id",
            "registry_host",
            "required_enforcement",
            "status",
            "authority_repository",
            "authority_source_sha",
            "evidence_id",
        }
        and isinstance(policy.get("policy_id"), str)
        and _PRODUCT.fullmatch(policy["policy_id"]) is not None
        and policy.get("registry_host") == publication_registry_host(targets)
        and policy.get("required_enforcement")
        == _REGISTRY_WRITE_ENFORCEMENT
        and policy.get("status") == "verified"
        and policy.get("authority_repository") == _REGISTRY_WRITE_AUTHORITY
        and isinstance(policy.get("authority_source_sha"), str)
        and _FULL_SHA.fullmatch(policy["authority_source_sha"]) is not None
        and isinstance(policy.get("evidence_id"), str)
        and _DIGEST.fullmatch(policy["evidence_id"]) is not None,
        "publication_output_invalid",
    )
    return {key: str(policy[key]) for key in sorted(policy)}


def public_json_outputs(
    targets: Sequence[PublishTarget],
    manifest_digests: Any,
    platform_digests: Any,
    immutable_references: Any,
) -> dict[str, str]:
    """Validate and bound the three registered public JSON outputs."""

    target_ids = tuple(target.target_id for target in targets)
    _require(
        1 <= len(target_ids) <= _MAX_PUBLIC_TARGETS
        and len(target_ids) == len(set(target_ids))
        and all(
            len(target_id) <= 64 and _PRODUCT.fullmatch(target_id) is not None
            for target_id in target_ids
        ),
        "publication_output_invalid",
    )
    expected = set(target_ids)
    manifests = _mapping(manifest_digests, "publication_output_invalid")
    platforms = _mapping(platform_digests, "publication_output_invalid")
    immutable = _mapping(immutable_references, "publication_output_invalid")
    immutable_targets = _mapping(
        immutable.get("targets"), "publication_output_invalid"
    )
    _require(
        set(manifests) == expected
        and set(platforms) == expected
        and set(immutable_targets) == expected
        and set(immutable)
        in (
            {"registry_write_policy", "targets", "release"},
            {"registry_write_policy", "targets", "release", "flux"},
        ),
        "publication_output_invalid",
    )
    normalized_manifests: dict[str, str] = {}
    normalized_platforms: dict[str, Any] = {}
    normalized_immutable_targets: dict[str, Any] = {}
    targets_by_id = {target.target_id: target for target in targets}
    for target_id in sorted(expected):
        target = targets_by_id[target_id]
        manifest = manifests[target_id]
        _require(
            isinstance(manifest, str) and _DIGEST.fullmatch(manifest) is not None,
            "publication_output_invalid",
        )
        normalized_manifests[target_id] = manifest
        normalized_platforms[target_id] = _validate_public_platform_evidence(
            target, platforms[target_id]
        )
        immutable_target = _mapping(
            immutable_targets[target_id], "publication_output_invalid"
        )
        _require(
            set(immutable_target)
            == {
                "repository",
                "version",
                "source_reference",
                "manifest_digest",
                "resolved_inputs",
                "assertions",
            }
            and immutable_target.get("repository") == target.registry_repository
            and immutable_target.get("version") == target.version_reference
            and immutable_target.get("source_reference")
            == target.source_reference
            and immutable_target.get("manifest_digest") == manifest,
            "publication_output_invalid",
        )
        normalized_immutable_targets[target_id] = {
            "repository": target.registry_repository,
            "version": target.version_reference,
            "source_reference": target.source_reference,
            "manifest_digest": manifest,
            "resolved_inputs": _validate_resolved_input_evidence(
                immutable_target.get("resolved_inputs"),
                target,
                "publication_output_invalid",
            ),
            "assertions": _validate_public_assertions(
                target, immutable_target.get("assertions")
            ),
        }
    release = _mapping(immutable.get("release"), "publication_output_invalid")
    first_target = targets[0]
    expected_source_sha = first_target.source_reference.rsplit("sha-", 1)[1]
    expected_version = first_target.version_reference.rsplit(":", 1)[1]
    _require(
        set(release) == {"source_sha", "version"}
        and release.get("source_sha") == expected_source_sha
        and release.get("version") == expected_version,
        "publication_output_invalid",
    )
    normalized_immutable: dict[str, Any] = {
        "registry_write_policy": _validate_public_registry_write_policy(
            immutable.get("registry_write_policy"), targets
        ),
        "targets": normalized_immutable_targets,
        "release": {
            "source_sha": expected_source_sha,
            "version": expected_version,
        },
    }
    if "flux" in immutable:
        flux = _mapping(immutable.get("flux"), "publication_output_invalid")
        _require(
            set(flux) == {"canary_id", "previous_known_good", "rollback_id"}
            and all(
                isinstance(flux.get(key), str)
                and 1 <= len(flux[key]) <= 256
                for key in ("canary_id", "previous_known_good", "rollback_id")
            ),
            "publication_output_invalid",
        )
        normalized_immutable["flux"] = dict(flux)
    outputs = {
        "manifest_digests_json": _bounded_public_json(
            "manifest_digests_json", normalized_manifests
        ),
        "platform_digests_json": _bounded_public_json(
            "platform_digests_json", normalized_platforms
        ),
        "immutable_references_json": _bounded_public_json(
            "immutable_references_json", normalized_immutable
        ),
    }
    _require(
        sum(len(value.encode("utf-8")) for value in outputs.values())
        <= _MAX_PUBLIC_JSON_OUTPUT_TOTAL_BYTES,
        "publication_output_invalid",
    )
    return outputs


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
    registry_write_policy: OciRegistryWritePolicy
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
    _require(
        _STABLE_SEMVER.fullmatch(release_version) is not None
        and has_valid_oci_tag_length(release_version),
        "invalid_version",
    )
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
    _mapping(contract.get("registry_write_policies"))
    _mapping(contract.get("products"))
    return contract


def _registry_write_policies(
    contract: Mapping[str, Any],
) -> Mapping[str, OciRegistryWritePolicy]:
    """Validate every registry-write policy as a closed authority record."""

    raw_policies = _mapping(contract.get("registry_write_policies"))
    _require(bool(raw_policies), "invalid_contract")
    policies: dict[str, OciRegistryWritePolicy] = {}
    for policy_id, raw in raw_policies.items():
        _require(
            isinstance(policy_id, str)
            and _PRODUCT.fullmatch(policy_id) is not None,
            "invalid_contract",
        )
        policy = _mapping(raw)
        _require(
            set(policy)
            == {
                "registry_host",
                "required_enforcement",
                "status",
                "authority_repository",
                "authority_source_sha",
                "evidence_id",
            },
            "invalid_contract",
        )
        registry_host = policy.get("registry_host")
        status = policy.get("status")
        authority_source_sha = policy.get("authority_source_sha")
        evidence_id = policy.get("evidence_id")
        _require(
            isinstance(registry_host, str)
            and registry_host == registry_host.lower()
            and "/" not in registry_host
            and ":" not in registry_host
            and is_canonical_publication_repository(
                f"{registry_host}/authority/placeholder"
            )
            and policy.get("required_enforcement")
            == _REGISTRY_WRITE_ENFORCEMENT
            and status in {"blocked", "verified"}
            and policy.get("authority_repository")
            == _REGISTRY_WRITE_AUTHORITY,
            "invalid_contract",
        )
        if status == "blocked":
            _require(
                authority_source_sha is None and evidence_id is None,
                "invalid_contract",
            )
        else:
            _require(
                isinstance(authority_source_sha, str)
                and _FULL_SHA.fullmatch(authority_source_sha) is not None
                and isinstance(evidence_id, str)
                and _DIGEST.fullmatch(evidence_id) is not None,
                "invalid_contract",
            )
        policies[policy_id] = OciRegistryWritePolicy(
            policy_id=policy_id,
            registry_host=registry_host,
            required_enforcement=_REGISTRY_WRITE_ENFORCEMENT,
            status=status,
            authority_repository=_REGISTRY_WRITE_AUTHORITY,
            authority_source_sha=authority_source_sha,
            evidence_id=evidence_id,
        )
    return policies


def publication_registry_host(targets: Sequence[PublishTarget]) -> str:
    """Return the one exact contract-owned registry host for all targets."""

    _require(bool(targets), "invalid_contract")
    hosts: set[str] = set()
    for target in targets:
        _require(
            is_canonical_publication_repository(target.registry_repository),
            "invalid_contract",
        )
        hosts.add(target.registry_repository.split("/", 1)[0])
    _require(len(hosts) == 1, "invalid_contract")
    return next(iter(hosts))


def registry_write_policy_evidence(plan: PublishPlan) -> dict[str, str]:
    """Return the verified redacted write authority bound to this plan."""

    try:
        evidence = plan.registry_write_policy.evidence()
    except ValueError as error:
        raise OciPublishError("registry_write_policy_not_ready") from error
    _require(
        set(evidence)
        == {
            "policy_id",
            "registry_host",
            "required_enforcement",
            "status",
            "authority_repository",
            "authority_source_sha",
            "evidence_id",
        }
        and _PRODUCT.fullmatch(evidence["policy_id"]) is not None
        and evidence["registry_host"] == publication_registry_host(plan.targets)
        and evidence["required_enforcement"] == _REGISTRY_WRITE_ENFORCEMENT
        and evidence["status"] == "verified"
        and evidence["authority_repository"] == _REGISTRY_WRITE_AUTHORITY
        and _FULL_SHA.fullmatch(evidence["authority_source_sha"]) is not None
        and _DIGEST.fullmatch(evidence["evidence_id"]) is not None,
        "invalid_contract",
    )
    return evidence


def resolve_plan(repository_root: Path, request: PublishRequest) -> PublishPlan:
    _require(
        _STABLE_SEMVER.fullmatch(request.release_version) is not None
        and has_valid_oci_tag_length(request.release_version),
        "invalid_version",
    )
    contract = load_product_contract(repository_root)
    products = _mapping(contract["products"])
    registry_write_policies = _registry_write_policies(contract)
    _require(request.product_id in products, "unsupported_product")
    product = _mapping(products[request.product_id])
    _require(product.get("repository") == request.repository, "unsupported_consumer")
    registry_write_policy_id = product.get("registry_write_policy_id")
    _require(
        isinstance(registry_write_policy_id, str)
        and registry_write_policy_id in registry_write_policies,
        "invalid_contract",
    )
    registry_write_policy = registry_write_policies[registry_write_policy_id]
    _require(
        registry_write_policy.status == "verified",
        "registry_write_policy_not_ready",
    )
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
    _require(
        all(
            isinstance(metadata[key], str)
            and bool(metadata[key])
            and len(metadata[key]) <= _METADATA_LABEL_MAX_LENGTHS[
                f"org.opencontainers.image.{key}"
            ]
            and all(character not in metadata[key] for character in ("\x00", "\r", "\n"))
            for key in metadata
        ),
        "invalid_contract",
    )
    raw_targets = product.get("targets")
    _require(
        isinstance(raw_targets, list)
        and 1 <= len(raw_targets) <= _MAX_PUBLIC_TARGETS,
        "invalid_contract",
    )
    targets: list[PublishTarget] = []
    for raw in raw_targets:
        target = _mapping(raw)
        target_id = target.get("target_id")
        platform_set = target.get("platform_set")
        _require(
            isinstance(target_id, str)
            and len(target_id) <= 64
            and _PRODUCT.fullmatch(target_id),
            "invalid_contract",
        )
        _require(isinstance(platform_set, str) and platform_set in platform_sets, "invalid_contract")
        platforms = _strings(platform_sets[platform_set])
        _require(
            1 <= len(platforms) <= _MAX_PUBLIC_PLATFORMS
            and all(_PLATFORM.fullmatch(item) for item in platforms),
            "invalid_contract",
        )
        assertions = _mapping(target.get("assertions"))
        input_policy_id = target.get("input_policy_id", "scratch-only-v1")
        _require(
            isinstance(input_policy_id, str)
            and _SAFE_INPUT_ID.fullmatch(input_policy_id) is not None,
            "invalid_contract",
        )
        required_user = assertions.get("user")
        _require(required_user is None or isinstance(required_user, str), "invalid_contract")
        repository = target.get("publication_repository")
        _require(is_canonical_publication_repository(repository), "invalid_contract")
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
                input_policy_id=input_policy_id,
            )
        )
    _require(len({target.target_id for target in targets}) == len(targets), "invalid_contract")
    _require(
        publication_registry_host(targets) == registry_write_policy.registry_host,
        "invalid_contract",
    )
    flux_asset = product.get("flux_asset")
    _require(isinstance(flux_asset, bool), "invalid_contract")
    independent_bootstrap = product.get("independent_bootstrap")
    _require(isinstance(independent_bootstrap, bool), "invalid_contract")
    canary = product.get("canary_id")
    known_good = product.get("previous_known_good")
    rollback = product.get("rollback_id")
    if flux_asset:
        _require(independent_bootstrap is True, "invalid_contract")
        _require(all(isinstance(item, str) and item for item in (canary, known_good, rollback)), "invalid_contract")
    else:
        _require(independent_bootstrap is False, "invalid_contract")
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
        registry_write_policy=registry_write_policy,
        flux_asset=flux_asset,
        canary_id=canary,
        previous_known_good=known_good,
        rollback_id=rollback,
    )


def publication_capacity_roots(
    environment: Mapping[str, str],
    *,
    _capacity_roots: _execution.CapacityRoots | None = None,
) -> _execution.CapacityRoots:
    """Resolve publication's three fixed internal capacity leaves.

    Production paths are selected only by the typed central runtime.  The
    private keyword is an immutable unit-test seam and is not exposed through
    an action, workflow, CLI argument, or environment variable.
    """

    try:
        roots = (
            _execution.build_capacity_roots(
                environment,
                domain="oci-publish",
                prefix="ciw-oci-publish",
            )
            if _capacity_roots is None
            else _capacity_roots
        )
    except _execution.OciBuildError as error:
        raise OciPublishError(error.code) from error
    _require(
        roots.domain == "oci-publish" and roots.prefix == "ciw-oci-publish",
        "capacity_identity_invalid",
    )
    return roots


def publication_state_root(
    environment: Mapping[str, str],
    *,
    _capacity_roots: _execution.CapacityRoots | None = None,
) -> Path:
    return publication_capacity_roots(
        environment, _capacity_roots=_capacity_roots
    ).scratch_root


def _validate_active_publication_capacity(
    roots: _execution.CapacityRoots,
) -> None:
    """Revalidate fixed parents and all ownership markers before state access."""

    try:
        _execution._validate_capacity_parents(roots)  # noqa: SLF001
        _execution._verify_capacity_markers(roots)  # noqa: SLF001
    except _execution.OciBuildError as error:
        raise OciPublishError(error.code) from error


def publication_build_capacity_roots(
    environment: Mapping[str, str],
    *,
    _capacity_roots: _execution.CapacityRoots | None = None,
) -> _execution.CapacityRoots:
    """Resolve only the fixed build allocation consumed by publication."""

    try:
        roots = (
            _execution.build_capacity_roots(environment)
            if _capacity_roots is None
            else _capacity_roots
        )
    except _execution.OciBuildError as error:
        raise OciPublishError(error.code) from error
    _require(
        roots.domain == "oci-build" and roots.prefix == "ciw-oci",
        "capacity_identity_invalid",
    )
    return roots


def _validate_active_build_capacity(
    roots: _execution.CapacityRoots,
) -> None:
    """Authenticate all build leaves before publication reads their evidence."""

    try:
        _execution._validate_capacity_parents(roots)  # noqa: SLF001
        _execution._verify_capacity_markers(roots)  # noqa: SLF001
    except _execution.OciBuildError as error:
        raise OciPublishError(error.code) from error
    except OSError as error:
        raise OciPublishError("capacity_marker_invalid") from error


def build_state_root(
    environment: Mapping[str, str],
    *,
    _capacity_roots: _execution.CapacityRoots | None = None,
) -> Path:
    """Locate the exact build scratch leaf after the capacity relocation."""

    return publication_build_capacity_roots(
        environment, _capacity_roots=_capacity_roots
    ).scratch_root


@dataclass(frozen=True)
class _AuthfileBinding:
    path: Path
    state: Mapping[str, object]


def _authfile_path(value: Path | _AuthfileBinding) -> Path:
    return value.path if isinstance(value, _AuthfileBinding) else value


def _authfile_expected_state(
    value: Path | _AuthfileBinding,
) -> Mapping[str, object] | None:
    return value.state if isinstance(value, _AuthfileBinding) else None


def _file_bytes_at(descriptor: int, maximum_bytes: int, code: str) -> bytes:
    info = os.fstat(descriptor)
    _require(
        stat.S_ISREG(info.st_mode)
        and stat.S_IMODE(info.st_mode) == 0o600
        and 0 < info.st_size <= maximum_bytes,
        code,
    )
    contents = os.pread(descriptor, info.st_size + 1, 0)
    _require(len(contents) == info.st_size, code)
    return contents


def _authfile_identity(contents: bytes) -> dict[str, object]:
    try:
        payload = json.loads(contents)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise OciPublishError("registry_auth_invalid") from error
    _require(isinstance(payload, Mapping), "registry_auth_invalid")
    return {
        "format": "containers-auth-json",
        "mode": "0600",
        "size_bytes": len(contents),
        "sha256": "sha256:" + hashlib.sha256(contents).hexdigest(),
    }


@dataclass
class _RegistryChildRuntime:
    roots: _execution.CapacityRoots
    parent_fds: Mapping[str, int]
    parent_ids: Mapping[str, _execution.DirectoryIdentity]
    leaf_fds: Mapping[str, int]
    leaf_ids: Mapping[str, _execution.DirectoryIdentity]
    resource_fds: Mapping[str, int]
    resource_stats: Mapping[str, os.stat_result]
    directory_ids: Mapping[str, _execution.DirectoryIdentity]
    expected_auth_state: Mapping[str, object]
    closed: bool = False

    @property
    def descriptor_paths(self) -> Mapping[str, str]:
        return {
            name: f"/proc/self/fd/{descriptor}"
            for name, descriptor in self.resource_fds.items()
        }

    @property
    def pass_fds(self) -> tuple[int, ...]:
        return tuple(
            sorted(
                {
                    *self.parent_fds.values(),
                    *self.leaf_fds.values(),
                    *self.resource_fds.values(),
                }
            )
        )

    def revalidate(self, *, allow_auth_change: bool) -> None:
        _require(not self.closed, "engine_isolation_failed")
        for key in ("scratch", "graph", "run"):
            _require(
                _execution._directory_identity(  # noqa: SLF001
                    self.parent_fds[key]
                )
                == self.parent_ids[key]
                and _execution._directory_identity(  # noqa: SLF001
                    self.leaf_fds[key]
                )
                == self.leaf_ids[key]
                and _execution._path_matches_directory(  # noqa: SLF001
                    self.parent_fds[key],
                    self.roots.leaf_name,
                    self.leaf_ids[key],
                ),
                "capacity_root_invalid",
            )
        _require(
            _execution._capacity_tree_mounts_confined(  # noqa: SLF001
                self.leaf_fds["scratch"],
                self.leaf_ids["scratch"].mount_id,
            )
            and _execution._capacity_tree_mounts_confined(  # noqa: SLF001
                self.leaf_fds["run"], self.leaf_ids["run"].mount_id
            ),
            "capacity_mount_invalid",
        )
        for name, descriptor in self.resource_fds.items():
            expected = self.resource_stats[name]
            actual = os.fstat(descriptor)
            named = os.stat(
                name,
                dir_fd=self.leaf_fds["scratch"],
                follow_symlinks=False,
            )
            _require(
                (actual.st_dev, actual.st_ino, stat.S_IFMT(actual.st_mode))
                == (expected.st_dev, expected.st_ino, stat.S_IFMT(expected.st_mode))
                and (named.st_dev, named.st_ino, stat.S_IFMT(named.st_mode))
                == (expected.st_dev, expected.st_ino, stat.S_IFMT(expected.st_mode)),
                "engine_isolation_failed",
            )
            if name in self.directory_ids:
                identity = self.directory_ids[name]
                _require(
                    _execution._directory_identity(descriptor) == identity  # noqa: SLF001
                    and _execution._capacity_tree_mounts_confined(  # noqa: SLF001
                        descriptor, identity.mount_id
                    ),
                    "capacity_mount_invalid",
                )
        config = _file_bytes_at(
            self.resource_fds["registries.conf"],
            len(_REGISTRIES_CONF_BYTES),
            "registry_auth_invalid",
        )
        _require(config == _REGISTRIES_CONF_BYTES, "registry_auth_invalid")
        auth = _file_bytes_at(
            self.resource_fds["registry-auth.json"],
            _MAX_AUTHFILE_BYTES,
            "registry_auth_invalid",
        )
        observed_auth = _authfile_identity(auth)
        if not allow_auth_change:
            _require(
                observed_auth == self.expected_auth_state,
                "publication_state_missing",
            )

    def preexec(self, engine_preexec: Any) -> Any:
        def prepare() -> None:
            engine_preexec()
            # The shared pre-exec has now pinned the canonical scratch/run
            # names as private bind mounts. Retained descriptors must still
            # identify those same owned leaves and runtime children.
            for key in ("scratch", "run"):
                if not _execution._path_matches_directory(  # noqa: SLF001
                    self.parent_fds[key],
                    self.roots.leaf_name,
                    self.leaf_ids[key],
                ):
                    raise OSError("capacity pathname changed")
            for name, descriptor in self.resource_fds.items():
                if name in self.directory_ids:
                    identity = self.directory_ids[name]
                    if (
                        _execution._directory_identity(descriptor)  # noqa: SLF001
                        != identity
                        or not _execution._capacity_tree_mounts_confined(  # noqa: SLF001
                            descriptor, identity.mount_id
                        )
                    ):
                        raise OSError("publication runtime mount changed")

        return prepare

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        for descriptor in reversed(tuple(self.resource_fds.values())):
            try:
                os.close(descriptor)
            except OSError:
                pass
        for descriptor in reversed(tuple(self.leaf_fds.values())):
            try:
                os.close(descriptor)
            except OSError:
                pass
        for descriptor in reversed(tuple(self.parent_fds.values())):
            try:
                os.close(descriptor)
            except OSError:
                pass


def _open_registry_child_runtime(
    roots: _execution.CapacityRoots,
    expected_auth_state: Mapping[str, object] | None = None,
) -> _RegistryChildRuntime:
    parent_fds: Mapping[str, int] = {}
    leaf_fds: Mapping[str, int] = {}
    resource_fds: dict[str, int] = {}
    try:
        parent_fds, parent_ids, leaf_fds, leaf_ids = (
            _execution._open_verified_capacity(roots)  # noqa: SLF001
        )
        scratch_fd = leaf_fds["scratch"]
        for name in (
            "home",
            "tmp",
            "xdg-cache",
            "xdg-config",
            "xdg-data",
            "xdg-runtime",
        ):
            resource_fds[name] = os.open(
                name,
                os.O_RDONLY
                | getattr(os, "O_DIRECTORY", 0)
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=scratch_fd,
            )
        resource_fds["registries.conf"] = os.open(
            "registries.conf",
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0),
            dir_fd=scratch_fd,
        )
        resource_fds["registry-auth.json"] = os.open(
            "registry-auth.json",
            os.O_RDWR
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0),
            dir_fd=scratch_fd,
        )
        resource_stats = {
            name: os.fstat(descriptor)
            for name, descriptor in resource_fds.items()
        }
        directory_ids = {
            name: _execution._directory_identity(descriptor)  # noqa: SLF001
            for name, descriptor in resource_fds.items()
            if stat.S_ISDIR(resource_stats[name].st_mode)
        }
        auth_contents = _file_bytes_at(
            resource_fds["registry-auth.json"],
            _MAX_AUTHFILE_BYTES,
            "registry_auth_invalid",
        )
        auth_state = _authfile_identity(auth_contents)
        if expected_auth_state is not None:
            _require(
                auth_state == expected_auth_state,
                "publication_state_missing",
            )
        runtime = _RegistryChildRuntime(
            roots,
            parent_fds,
            parent_ids,
            leaf_fds,
            leaf_ids,
            resource_fds,
            resource_stats,
            directory_ids,
            auth_state,
        )
        runtime.revalidate(allow_auth_change=False)
        return runtime
    except OciPublishError:
        raise
    except _execution.OciBuildError as error:
        raise OciPublishError(error.code) from error
    except OSError as error:
        raise OciPublishError("engine_isolation_failed") from error
    finally:
        if "runtime" not in locals():
            for descriptor in reversed(tuple(resource_fds.values())):
                try:
                    os.close(descriptor)
                except OSError:
                    pass
            for descriptor in reversed(tuple(leaf_fds.values())):
                try:
                    os.close(descriptor)
                except OSError:
                    pass
            for descriptor in reversed(tuple(parent_fds.values())):
                try:
                    os.close(descriptor)
                except OSError:
                    pass


def _run(
    argv: Sequence[str],
    *,
    capacity_roots: _execution.CapacityRoots,
    input_bytes: bytes | None = None,
    stdout_limit: int,
    stderr_limit: int,
    overflow_code: str,
    retain_output: bool = True,
    check: bool = True,
    expected_auth_state: Mapping[str, object] | None = None,
) -> subprocess.CompletedProcess[bytes]:
    _require(bool(argv) and Path(argv[0]).name == "skopeo", "registry_tool_unavailable")
    child_runtime = _open_registry_child_runtime(
        capacity_roots, expected_auth_state
    )
    child_environment = {
        key: value
        for key in _SUBPROCESS_ENVIRONMENT
        if (value := os.environ.get(key))
    }
    descriptor_paths = child_runtime.descriptor_paths
    child_environment.update(
        {
            "CONTAINERS_REGISTRIES_CONF": descriptor_paths["registries.conf"],
            "HOME": descriptor_paths["home"],
            "REGISTRY_AUTH_FILE": descriptor_paths["registry-auth.json"],
            "TEMP": descriptor_paths["tmp"],
            "TMP": descriptor_paths["tmp"],
            "TMPDIR": descriptor_paths["tmp"],
            "XDG_CACHE_HOME": descriptor_paths["xdg-cache"],
            "XDG_CONFIG_HOME": descriptor_paths["xdg-config"],
            "XDG_DATA_HOME": descriptor_paths["xdg-data"],
            "XDG_RUNTIME_DIR": descriptor_paths["xdg-runtime"],
        }
    )
    child_argv = list(argv)
    if "--authfile" in child_argv:
        index = child_argv.index("--authfile")
        _require(
            index + 1 < len(child_argv)
            and child_argv[index + 1]
            == str(capacity_roots.scratch_root / "registry-auth.json"),
            "registry_auth_invalid",
        )
        child_argv[index + 1] = descriptor_paths["registry-auth.json"]
    try:
        engine_preexec = _execution._private_engine_preexec(capacity_roots)
    except _execution.OciBuildError as error:
        child_runtime.close()
        raise OciPublishError(error.code) from error
    private_preexec = child_runtime.preexec(engine_preexec)
    try:
        child_runtime.revalidate(allow_auth_change=False)
        result = _run_bounded_subprocess(
            child_argv,
            input_bytes=input_bytes,
            stdout_limit=stdout_limit,
            stderr_limit=stderr_limit,
            overflow_code=overflow_code,
            retain_output=retain_output,
            check=check,
            env=child_environment,
            preexec_fn=private_preexec,
            pass_fds=child_runtime.pass_fds,
        )
        child_runtime.revalidate(
            allow_auth_change=len(child_argv) > 1 and child_argv[1] == "login"
        )
        return result
    except subprocess.CalledProcessError:
        raise
    except (OSError, subprocess.SubprocessError) as error:
        raise OciPublishError("engine_isolation_failed") from error
    finally:
        child_runtime.close()


def _kill_registry_process_group(process: subprocess.Popen[bytes]) -> None:
    """Stop the isolated registry producer and synchronously reap its leader."""

    # Do not poll first: poll() can reap an exited group leader while one of its
    # descendants still owns a capture pipe.  Until wait() records returncode,
    # the dedicated group remains the exact producer boundary to terminate.
    if process.returncode is None:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except OSError:
            try:
                process.kill()
            except OSError:
                pass
    try:
        process.wait()
    except OSError:
        pass


def _run_bounded_subprocess(
    argv: Sequence[str],
    *,
    input_bytes: bytes | None,
    stdout_limit: int,
    stderr_limit: int,
    overflow_code: str,
    retain_output: bool,
    check: bool,
    env: Mapping[str, str] | None,
    preexec_fn: Any = None,
    pass_fds: tuple[int, ...] = (),
) -> subprocess.CompletedProcess[bytes]:
    """Run one registry child while retaining at most the declared byte bounds."""

    _require(
        isinstance(stdout_limit, int)
        and 0 <= stdout_limit <= _MAX_REGISTRY_CAPTURE_BYTES
        and isinstance(stderr_limit, int)
        and 0 <= stderr_limit <= _MAX_REGISTRY_CAPTURE_BYTES,
        "engine_isolation_failed",
    )
    _require(_SAFE_CODE.fullmatch(overflow_code) is not None, "engine_isolation_failed")
    process: subprocess.Popen[bytes] | None = None
    selector = selectors.DefaultSelector()
    stdout_parts: list[bytes] = []
    stderr_parts: list[bytes] = []
    counts = {"stdout": 0, "stderr": 0}
    limits = {"stdout": stdout_limit, "stderr": stderr_limit}
    parts = {"stdout": stdout_parts, "stderr": stderr_parts}
    try:
        process = subprocess.Popen(
            list(argv),
            stdin=subprocess.PIPE if input_bytes is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            preexec_fn=preexec_fn,
            pass_fds=pass_fds,
            start_new_session=True,
            bufsize=0,
        )
        _require(
            process.stdout is not None and process.stderr is not None,
            "engine_isolation_failed",
        )
        for name, stream in (("stdout", process.stdout), ("stderr", process.stderr)):
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, selectors.EVENT_READ, name)

        input_view = memoryview(input_bytes or b"")
        input_offset = 0
        if process.stdin is not None:
            os.set_blocking(process.stdin.fileno(), False)
            if input_view:
                selector.register(process.stdin, selectors.EVENT_WRITE, "stdin")
            else:
                process.stdin.close()

        while selector.get_map():
            for key, _events in selector.select():
                stream = key.fileobj
                name = key.data
                if name == "stdin":
                    try:
                        written = os.write(
                            stream.fileno(),
                            input_view[
                                input_offset : input_offset
                                + _REGISTRY_CAPTURE_CHUNK_BYTES
                            ],
                        )
                    except BrokenPipeError:
                        written = len(input_view) - input_offset
                    input_offset += written
                    if input_offset >= len(input_view):
                        selector.unregister(stream)
                        stream.close()
                    continue

                remaining = limits[name] - counts[name]
                try:
                    chunk = os.read(
                        stream.fileno(),
                        min(_REGISTRY_CAPTURE_CHUNK_BYTES, remaining + 1),
                    )
                except BlockingIOError:
                    continue
                if not chunk:
                    selector.unregister(stream)
                    stream.close()
                    continue
                counts[name] += len(chunk)
                if counts[name] > limits[name]:
                    _kill_registry_process_group(process)
                    raise OciPublishError(overflow_code)
                if retain_output:
                    parts[name].append(chunk)

        returncode = process.wait()
        stdout = b"".join(stdout_parts)
        stderr = b"".join(stderr_parts)
        result = subprocess.CompletedProcess(list(argv), returncode, stdout, stderr)
        if check and returncode != 0:
            raise subprocess.CalledProcessError(
                returncode,
                list(argv),
                output=stdout,
                stderr=stderr,
            )
        return result
    except BaseException:
        if process is not None:
            _kill_registry_process_group(process)
        raise
    finally:
        for key in tuple(selector.get_map().values()):
            stream = key.fileobj
            try:
                selector.unregister(stream)
            except (KeyError, ValueError):
                pass
            try:
                stream.close()
            except OSError:
                pass
        selector.close()


def _prepare_publication_runtime(roots: _execution.CapacityRoots) -> None:
    """Create only publication-owned Skopeo runtime state in scratch capacity."""

    for directory in (
        roots.scratch_root / "home",
        roots.scratch_root / "implicit-containers",
        roots.scratch_root / "implicit-containers" / "storage",
        roots.scratch_root / "tmp",
        roots.scratch_root / "xdg-cache",
        roots.scratch_root / "xdg-config",
        roots.scratch_root / "xdg-data",
        roots.scratch_root / "xdg-runtime",
    ):
        try:
            directory.mkdir(mode=0o700, parents=False, exist_ok=False)
        except OSError as error:
            raise OciPublishError("capacity_root_invalid") from error
    registries = roots.scratch_root / "registries.conf"
    try:
        registries.write_text(
            'unqualified-search-registries = []\nshort-name-mode = "disabled"\n',
            encoding="utf-8",
        )
        registries.chmod(0o600)
    except OSError as error:
        raise OciPublishError("capacity_root_invalid") from error


def _authfile(root: Path) -> Path:
    return root / "registry-auth.json"


def _open_verified_state_root(
    roots: _execution.CapacityRoots,
) -> tuple[
    int,
    _execution.DirectoryIdentity,
    int,
    _execution.DirectoryIdentity,
]:
    """Pin the marker-authenticated scratch root used for publication state."""

    try:
        parent_fds, parent_ids, leaf_fds, leaf_ids = (
            _execution._open_verified_capacity(roots)  # noqa: SLF001
        )
    except _execution.OciBuildError as error:
        raise OciPublishError(error.code) from error
    for key in ("run", "graph"):
        os.close(leaf_fds[key])
        os.close(parent_fds[key])
    return (
        parent_fds["scratch"],
        parent_ids["scratch"],
        leaf_fds["scratch"],
        leaf_ids["scratch"],
    )


def _revalidate_state_root(
    roots: _execution.CapacityRoots,
    parent_fd: int,
    parent_identity: _execution.DirectoryIdentity,
    root_fd: int,
    root_identity: _execution.DirectoryIdentity,
    code: str,
) -> None:
    """Require held descriptors and public names to retain exact ownership."""

    rebound_parent = -1
    rebound_root = -1
    try:
        rebound_parent, observed_parent = _execution._open_bound_parent(  # noqa: SLF001
            roots.scratch_parent
        )
        _execution._require_reviewed_parent_mount(  # noqa: SLF001
            roots.scratch_parent, observed_parent, roots
        )
        _require(observed_parent == parent_identity, code)
        rebound_root, observed_root = _execution._open_capacity_leaf(  # noqa: SLF001
            parent_fd,
            roots.leaf_name,
            error_code="capacity_root_invalid",
        )
        _require(
            observed_root == root_identity
            and _execution._directory_identity(root_fd) == root_identity  # noqa: SLF001
            and _execution._capacity_leaf_is_owned(  # noqa: SLF001
                parent_fd,
                parent_identity,
                "scratch",
                root_fd,
                root_identity,
                roots,
            ),
            code,
        )
    except OciPublishError:
        raise
    except _execution.OciBuildError as error:
        raise OciPublishError(error.code) from error
    except OSError as error:
        raise OciPublishError(code) from error
    finally:
        if rebound_root >= 0:
            os.close(rebound_root)
        if rebound_parent >= 0:
            os.close(rebound_parent)


def _close_state_root(parent_fd: int, root_fd: int) -> None:
    for descriptor in (root_fd, parent_fd):
        try:
            os.close(descriptor)
        except OSError:
            pass


def _read_secure_state_file(
    path: Path,
    maximum_bytes: int,
    code: str,
    *,
    missing_code: str | None = None,
    directory_fd: int | None = None,
) -> bytes:
    """Read one bounded regular 0600 file through its no-follow descriptor."""

    no_follow = getattr(os, "O_NOFOLLOW", None)
    _require(no_follow is not None, code)
    descriptor = -1
    try:
        descriptor = os.open(
            path.name if directory_fd is not None else path,
            os.O_RDONLY
            | no_follow
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NONBLOCK", 0),
            dir_fd=directory_fd,
        )
        info = os.fstat(descriptor)
        _require(
            stat.S_ISREG(info.st_mode)
            and stat.S_IMODE(info.st_mode) == 0o600
            and 0 < info.st_size <= maximum_bytes,
            code,
        )
        contents = b""
        remaining = info.st_size
        while remaining:
            chunk = os.read(descriptor, min(remaining, 64 * 1024))
            _require(bool(chunk), code)
            contents += chunk
            remaining -= len(chunk)
        _require(os.read(descriptor, 1) == b"", code)
        final_info = os.fstat(descriptor)
        path_info = os.stat(
            path.name if directory_fd is not None else path,
            dir_fd=directory_fd,
            follow_symlinks=False,
        )
        _require(
            stat.S_ISREG(final_info.st_mode)
            and stat.S_IMODE(final_info.st_mode) == 0o600
            and final_info.st_size == info.st_size
            and (final_info.st_dev, final_info.st_ino)
            == (info.st_dev, info.st_ino)
            and stat.S_ISREG(path_info.st_mode)
            and stat.S_IMODE(path_info.st_mode) == 0o600
            and path_info.st_size == info.st_size
            and (path_info.st_dev, path_info.st_ino)
            == (info.st_dev, info.st_ino),
            code,
        )
        return contents
    except OciPublishError:
        raise
    except FileNotFoundError as error:
        if missing_code == "registry_auth_missing":
            raise OciPublishError("registry_auth_missing") from error
        raise OciPublishError(code) from error
    except OSError as error:
        raise OciPublishError(code) from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _secure_authfile_state_at(
    roots: _execution.CapacityRoots,
    root_fd: int,
) -> tuple[Path, dict[str, object]]:
    """Return a redacted identity for auth read from one bound state root."""

    path = _authfile(roots.scratch_root)
    try:
        contents = _read_secure_state_file(
            path,
            _MAX_AUTHFILE_BYTES,
            "registry_auth_invalid",
            missing_code="registry_auth_missing",
            directory_fd=root_fd,
        )
        payload = json.loads(contents)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise OciPublishError("registry_auth_invalid") from error
    _require(isinstance(payload, Mapping), "registry_auth_invalid")
    return path, {
        "format": "containers-auth-json",
        "mode": "0600",
        "size_bytes": len(contents),
        "sha256": "sha256:" + hashlib.sha256(contents).hexdigest(),
    }


def _secure_authfile_state(
    roots: _execution.CapacityRoots,
) -> tuple[Path, dict[str, object]]:
    """Read auth through a verified root and reject any parent substitution."""

    parent_fd, parent_id, root_fd, root_id = _open_verified_state_root(roots)
    try:
        _revalidate_state_root(
            roots,
            parent_fd,
            parent_id,
            root_fd,
            root_id,
            "registry_auth_invalid",
        )
        result = _secure_authfile_state_at(roots, root_fd)
        _revalidate_state_root(
            roots,
            parent_fd,
            parent_id,
            root_fd,
            root_id,
            "registry_auth_invalid",
        )
        return result
    finally:
        _close_state_root(parent_fd, root_fd)


def _secure_existing_authfile(roots: _execution.CapacityRoots) -> Path:
    return _secure_authfile_state(roots)[0]


def _publication_plan_state(
    plan: PublishPlan,
    roots: _execution.CapacityRoots,
    authfile_state: Mapping[str, object],
) -> dict[str, object]:
    targets = {
        target.target_id: {
            "platforms": list(target.platforms),
            "repository": target.registry_repository,
            "source_repository": target.source_repository,
            "source_reference": target.source_reference,
            "version_reference": target.version_reference,
            "metadata": dict(sorted(target.metadata.items())),
            "required_user": target.required_user,
            "required_entrypoint": list(target.required_entrypoint),
            "required_command": list(target.required_command),
            "required_ports": list(target.required_ports),
            "input_policy_id": target.input_policy_id,
        }
        for target in sorted(plan.targets, key=lambda item: item.target_id)
    }
    return {
        "api": "oci.publish",
        "version": "1.0.0",
        "source": {
            "repository": plan.repository,
            "sha": plan.admitted_sha,
            "trust": plan.source_trust,
        },
        "product_id": plan.product_id,
        "release_version": plan.release_version,
        "execution": {
            "runner_profile": plan.runner_profile,
            "runs_on": list(plan.runs_on),
            "workspace_profile": plan.workspace_profile,
            "timeout_minutes": plan.timeout_minutes,
        },
        "release_policy": {
            "flux_asset": plan.flux_asset,
            "canary_id": plan.canary_id,
            "previous_known_good": plan.previous_known_good,
            "rollback_id": plan.rollback_id,
        },
        "registry_write_policy": registry_write_policy_evidence(plan),
        "registry_host": publication_registry_host(plan.targets),
        "repositories": {
            target_id: target["repository"] for target_id, target in targets.items()
        },
        "targets": targets,
        "capacity": {
            "domain": roots.domain,
            "prefix": roots.prefix,
            "production": roots.production,
            "token": roots.token,
        },
        "authfile": dict(authfile_state),
    }


def _canonical_state_bytes(value: Mapping[str, object]) -> bytes:
    return (
        json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode()


def _write_bound_state_file(
    roots: _execution.CapacityRoots,
    name: str,
    payload: bytes,
    maximum_bytes: int,
    code: str,
) -> None:
    """Create one exact state entry through a marker-authenticated root fd."""

    _require(
        re.fullmatch(r"[a-z][a-z0-9-]*\.json", name) is not None
        and 0 < len(payload) <= maximum_bytes,
        code,
    )
    no_follow = getattr(os, "O_NOFOLLOW", None)
    _require(no_follow is not None, code)
    parent_fd, parent_id, root_fd, root_id = _open_verified_state_root(roots)
    descriptor = -1
    try:
        _revalidate_state_root(
            roots, parent_fd, parent_id, root_fd, root_id, code
        )
        descriptor = os.open(
            name,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | no_follow
            | getattr(os, "O_CLOEXEC", 0),
            0o600,
            dir_fd=root_fd,
        )
        os.fchmod(descriptor, 0o600)
        info = os.fstat(descriptor)
        _require(
            stat.S_ISREG(info.st_mode)
            and stat.S_IMODE(info.st_mode) == 0o600,
            code,
        )
        written = 0
        while written < len(payload):
            count = os.write(descriptor, payload[written:])
            _require(count > 0, code)
            written += count
        os.fsync(descriptor)
        final_info = os.fstat(descriptor)
        path_info = os.stat(name, dir_fd=root_fd, follow_symlinks=False)
        _require(
            stat.S_ISREG(final_info.st_mode)
            and stat.S_IMODE(final_info.st_mode) == 0o600
            and final_info.st_size == len(payload)
            and stat.S_ISREG(path_info.st_mode)
            and stat.S_IMODE(path_info.st_mode) == 0o600
            and (path_info.st_dev, path_info.st_ino)
            == (final_info.st_dev, final_info.st_ino),
            code,
        )
        _revalidate_state_root(
            roots, parent_fd, parent_id, root_fd, root_id, code
        )
        os.fsync(root_fd)
    except OciPublishError:
        raise
    except OSError as error:
        raise OciPublishError(code) from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        _close_state_root(parent_fd, root_fd)


def _write_publication_plan_state(
    plan: PublishPlan,
    roots: _execution.CapacityRoots,
) -> None:
    """Exclusively bind authenticated state to the exact publication plan."""

    _, authfile_state = _secure_authfile_state(roots)
    payload = _canonical_state_bytes(
        _publication_plan_state(plan, roots, authfile_state)
    )
    _write_bound_state_file(
        roots,
        "plan.json",
        payload,
        _MAX_PLAN_STATE_BYTES,
        "publication_state_missing",
    )


def _load_publication_plan_state(
    plan: PublishPlan,
    roots: _execution.CapacityRoots,
) -> _AuthfileBinding:
    """Strictly authenticate plan and auth state before privileged phases."""

    parent_fd, parent_id, root_fd, root_id = _open_verified_state_root(roots)
    try:
        _revalidate_state_root(
            roots,
            parent_fd,
            parent_id,
            root_fd,
            root_id,
            "publication_state_missing",
        )
        authfile, authfile_state = _secure_authfile_state_at(roots, root_fd)
        expected = _publication_plan_state(plan, roots, authfile_state)
        expected_bytes = _canonical_state_bytes(expected)
        contents = _read_secure_state_file(
            roots.scratch_root / "plan.json",
            _MAX_PLAN_STATE_BYTES,
            "publication_state_missing",
            directory_fd=root_fd,
        )
        _require(contents == expected_bytes, "publication_state_missing")
        decoded = json.loads(contents)
        _require(decoded == expected, "publication_state_missing")
        _revalidate_state_root(
            roots,
            parent_fd,
            parent_id,
            root_fd,
            root_id,
            "publication_state_missing",
        )
    except OciPublishError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise OciPublishError("publication_state_missing") from error
    finally:
        _close_state_root(parent_fd, root_fd)
    return _AuthfileBinding(authfile, authfile_state)


def _create_empty_authfile(roots: _execution.CapacityRoots) -> Path:
    _write_bound_state_file(
        roots,
        "registry-auth.json",
        b"{}\n",
        _MAX_AUTHFILE_BYTES,
        "registry_auth_invalid",
    )
    authfile = _secure_existing_authfile(roots)
    return authfile


def authenticate(
    plan: PublishPlan,
    environment: Mapping[str, str],
    username: str,
    token: str,
    *,
    _capacity_roots: _execution.CapacityRoots | None = None,
) -> dict[str, str]:
    registry_write_policy_evidence(plan)
    _require(username == username.strip() and bool(username) and "\n" not in username and "\r" not in username, "registry_auth_invalid")
    _require(bool(token) and "\n" not in token and "\r" not in token, "registry_auth_invalid")
    _require(shutil.which("skopeo") is not None, "registry_tool_unavailable")
    registry_host = publication_registry_host(plan.targets)
    capacity_roots = publication_capacity_roots(
        environment, _capacity_roots=_capacity_roots
    )
    try:
        _execution.prepare_capacity_roots(capacity_roots)
    except _execution.OciBuildError as error:
        raise OciPublishError(error.code) from error
    _prepare_publication_runtime(capacity_roots)
    authfile = _create_empty_authfile(capacity_roots)
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
                registry_host,
            ],
            capacity_roots=capacity_roots,
            input_bytes=token.encode("utf-8"),
            stdout_limit=_MAX_REGISTRY_LOGIN_STDOUT_BYTES,
            stderr_limit=_MAX_REGISTRY_LOGIN_STDERR_BYTES,
            overflow_code="registry_auth_failed",
            retain_output=False,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        raise OciPublishError("registry_auth_failed") from error
    _secure_existing_authfile(capacity_roots)
    _write_publication_plan_state(plan, capacity_roots)
    return {"result": "authenticated", "failure_code": ""}


def _blob_path(layout: Path, digest: str) -> Path:
    _require(_DIGEST.fullmatch(digest) is not None, "oci_layout_malformed")
    path = layout / "blobs" / "sha256" / digest.removeprefix("sha256:")
    _require(path.is_file() and not path.is_symlink(), "oci_layout_malformed")
    blob_hash = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            blob_hash.update(chunk)
    actual = "sha256:" + blob_hash.hexdigest()
    _require(actual == digest, "oci_digest_mismatch")
    return path


def _bounded_json_file(path: Path, maximum_bytes: int) -> Any:
    """Read one local-layout JSON file through a bounded no-follow descriptor."""

    no_follow = getattr(os, "O_NOFOLLOW", None)
    _require(no_follow is not None, "oci_layout_malformed")
    descriptor = -1
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY | no_follow | getattr(os, "O_CLOEXEC", 0),
        )
        info = os.fstat(descriptor)
        _require(
            stat.S_ISREG(info.st_mode) and 0 < info.st_size <= maximum_bytes,
            "oci_layout_malformed",
        )
        payload = b""
        remaining = info.st_size
        while remaining:
            chunk = os.read(descriptor, min(remaining, 64 * 1024))
            _require(bool(chunk), "oci_layout_malformed")
            payload += chunk
            remaining -= len(chunk)
        _require(os.read(descriptor, 1) == b"", "oci_layout_malformed")
        return json.loads(payload.decode("utf-8"))
    except OciPublishError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise OciPublishError("oci_layout_malformed") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _json_blob(layout: Path, descriptor: Mapping[str, Any], allowed_media: set[str]) -> Mapping[str, Any]:
    media = descriptor.get("mediaType")
    digest = descriptor.get("digest")
    size = descriptor.get("size")
    _require(
        media in allowed_media
        and isinstance(digest, str)
        and type(size) is int
        and 0 < size <= _MAX_LAYOUT_JSON_BYTES,
        "oci_layout_malformed",
    )
    path = _blob_path(layout, digest)
    _require(path.stat().st_size == size, "oci_layout_malformed")
    value = _bounded_json_file(path, _MAX_LAYOUT_JSON_BYTES)
    return _mapping(value, "oci_layout_malformed")


def _root_descriptor(layout: Path, ref_name: str) -> Mapping[str, Any]:
    marker = layout / "oci-layout"
    index_path = layout / "index.json"
    _require(
        _bounded_json_file(marker, 1024) == {"imageLayoutVersion": "1.0.0"},
        "oci_layout_malformed",
    )
    root = _bounded_json_file(index_path, _MAX_LAYOUT_ROOT_BYTES)
    root = _mapping(root, "oci_layout_malformed")
    manifests = root.get("manifests")
    _require(
        root.get("schemaVersion") == 2
        and root.get("mediaType") in {None, _INDEX_MEDIA_TYPE}
        and isinstance(manifests, list)
        and len(manifests) == 1,
        "oci_layout_malformed",
    )
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
    _require(set(labels) == _REQUIRED_LABELS, "metadata_mismatch")
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
    expected_user = "" if target.required_user is None else target.required_user
    _require(runtime.get("User", "") == expected_user, "assertion_failed")
    _require(
        tuple(runtime.get("Entrypoint") or ()) == target.required_entrypoint,
        "assertion_failed",
    )
    _require(
        tuple(runtime.get("Cmd") or ()) == target.required_command,
        "assertion_failed",
    )
    ports = runtime.get("ExposedPorts") or {}
    _require(isinstance(ports, Mapping), "assertion_failed")
    _require(
        tuple(sorted(ports)) == tuple(sorted(target.required_ports)),
        "assertion_failed",
    )


def inspect_layout(layout: Path, target: PublishTarget, ref_name: str) -> Mapping[str, Any]:
    _require(layout.is_dir() and not layout.is_symlink(), "oci_layout_malformed")
    descriptor = _root_descriptor(layout, ref_name)
    top_digest = descriptor["digest"]
    media = descriptor.get("mediaType")
    if media == _INDEX_MEDIA_TYPE:
        index = _json_blob(layout, descriptor, {_INDEX_MEDIA_TYPE})
        manifests = index.get("manifests")
        _require(
            index.get("schemaVersion") == 2
            and index.get("mediaType") in {None, _INDEX_MEDIA_TYPE}
            and isinstance(manifests, list)
            and manifests,
            "oci_layout_malformed",
        )
        _require(
            len(manifests) <= _MAX_PUBLIC_PLATFORMS,
            "oci_layout_malformed",
        )
        manifest_descriptors = tuple(
            _mapping(item, "oci_layout_malformed") for item in manifests
        )
    elif media == _MANIFEST_MEDIA_TYPE:
        manifest_descriptors = (descriptor,)
    else:
        raise OciPublishError("oci_layout_malformed")
    rows: dict[str, Any] = {}
    for manifest_descriptor in manifest_descriptors:
        manifest = _json_blob(layout, manifest_descriptor, {_MANIFEST_MEDIA_TYPE})
        _require(
            manifest.get("schemaVersion") == 2
            and manifest.get("mediaType") in {None, _MANIFEST_MEDIA_TYPE},
            "oci_layout_malformed",
        )
        config_descriptor = _mapping(manifest.get("config"), "oci_layout_malformed")
        config = _json_blob(layout, config_descriptor, {_CONFIG_MEDIA_TYPE})
        platform = _platform_name(config)
        declared_platform = manifest_descriptor.get("platform")
        if declared_platform is not None:
            _require(
                _platform_name(_mapping(declared_platform, "oci_layout_malformed"))
                == platform,
                "oci_layout_malformed",
            )
        _require(platform not in rows, "oci_layout_malformed")
        runtime = _mapping(config.get("config") or {}, "oci_layout_malformed")
        labels = _mapping(runtime.get("Labels") or {}, "metadata_mismatch")
        _validate_runtime(target, config, labels)
        layers = manifest.get("layers")
        rootfs = config.get("rootfs")
        _require(
            isinstance(layers, list)
            and bool(layers)
            and len(layers) <= _MAX_PUBLIC_LAYERS
            and isinstance(rootfs, Mapping)
            and rootfs.get("type") == "layers"
            and isinstance(rootfs.get("diff_ids"), list)
            and len(rootfs["diff_ids"]) == len(layers)
            and all(
                isinstance(diff_id, str) and _DIGEST.fullmatch(diff_id) is not None
                for diff_id in rootfs["diff_ids"]
            ),
            "oci_layout_malformed",
        )
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
            "labels": _project_metadata_labels(
                target,
                labels,
                "publication_output_invalid",
            ),
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


def _inspect_remote_digest(
    reference: str,
    authfile: Path | _AuthfileBinding,
    capacity_roots: _execution.CapacityRoots,
) -> str | None:
    authfile_path = _authfile_path(authfile)
    try:
        result = _run(
            ["skopeo", "inspect", "--authfile", str(authfile_path), "--raw", f"docker://{reference}"],
            check=False,
            capacity_roots=capacity_roots,
            stdout_limit=_MAX_REGISTRY_RAW_MANIFEST_BYTES,
            stderr_limit=_MAX_REGISTRY_INSPECTION_STDERR_BYTES,
            overflow_code="registry_inspection_failed",
            expected_auth_state=_authfile_expected_state(authfile),
        )
    except OSError as error:
        raise OciPublishError("registry_inspection_failed") from error
    if result.returncode == 0:
        return raw_manifest_digest(result.stdout)
    stderr = result.stderr.lower()
    if any(marker in stderr for marker in (b"manifest unknown", b"name unknown", b"not found")):
        return None
    raise OciPublishError("registry_inspection_failed")


def _copy(
    source: str,
    destination: str,
    authfile: Path | _AuthfileBinding,
    capacity_roots: _execution.CapacityRoots,
) -> None:
    authfile_path = _authfile_path(authfile)
    try:
        _run(
            [
                "skopeo",
                "copy",
                "--all",
                "--preserve-digests",
                "--authfile",
                str(authfile_path),
                source,
                destination,
            ],
            capacity_roots=capacity_roots,
            stdout_limit=_MAX_REGISTRY_COPY_STDOUT_BYTES,
            stderr_limit=_MAX_REGISTRY_COPY_STDERR_BYTES,
            overflow_code="registry_copy_failed",
            retain_output=False,
            expected_auth_state=_authfile_expected_state(authfile),
        )
    except (OSError, subprocess.CalledProcessError) as error:
        raise OciPublishError("registry_copy_failed") from error


def _require_new_state_path(
    roots: _execution.CapacityRoots, name: str
) -> None:
    """Reject any preexisting state entry before a privileged phase begins."""

    _require(
        name in {"publication.json", "readback.json"},
        "publication_state_missing",
    )
    parent_fd, parent_id, root_fd, root_id = _open_verified_state_root(roots)
    try:
        _revalidate_state_root(
            roots,
            parent_fd,
            parent_id,
            root_fd,
            root_id,
            "publication_state_missing",
        )
        try:
            os.stat(name, dir_fd=root_fd, follow_symlinks=False)
        except FileNotFoundError:
            _revalidate_state_root(
                roots,
                parent_fd,
                parent_id,
                root_fd,
                root_id,
                "publication_state_missing",
            )
            return
        raise OciPublishError("publication_state_missing")
    except OciPublishError:
        raise
    except OSError as error:
        raise OciPublishError("publication_state_missing") from error
    finally:
        _close_state_root(parent_fd, root_fd)


def _write_state(
    roots: _execution.CapacityRoots,
    name: str,
    value: Mapping[str, Any],
) -> None:
    """Exclusively persist one bounded canonical phase record without symlinks."""

    try:
        payload = _canonical_state_bytes(value)
    except (TypeError, ValueError) as error:
        raise OciPublishError("publication_state_missing") from error
    _require(
        0 < len(payload) <= _MAX_PHASE_STATE_BYTES,
        "publication_state_missing",
    )
    _write_bound_state_file(
        roots,
        name,
        payload,
        _MAX_PHASE_STATE_BYTES,
        "publication_state_missing",
    )


def _read_state(
    roots: _execution.CapacityRoots, name: str
) -> Mapping[str, Any]:
    """Load one bounded canonical phase record through its exact descriptor."""

    _require(
        name in {"publication.json", "readback.json"},
        "publication_state_missing",
    )
    parent_fd, parent_id, root_fd, root_id = _open_verified_state_root(roots)
    try:
        _revalidate_state_root(
            roots,
            parent_fd,
            parent_id,
            root_fd,
            root_id,
            "publication_state_missing",
        )
        contents = _read_secure_state_file(
            roots.scratch_root / name,
            _MAX_PHASE_STATE_BYTES,
            "publication_state_missing",
            directory_fd=root_fd,
        )
        value = _mapping(json.loads(contents), "publication_state_missing")
        _require(
            contents == _canonical_state_bytes(value),
            "publication_state_missing",
        )
        _revalidate_state_root(
            roots,
            parent_fd,
            parent_id,
            root_fd,
            root_id,
            "publication_state_missing",
        )
        return value
    except OciPublishError:
        raise
    except (TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise OciPublishError("publication_state_missing") from error
    finally:
        _close_state_root(parent_fd, root_fd)


def publish(
    plan: PublishPlan,
    environment: Mapping[str, str],
    *,
    _capacity_roots: _execution.CapacityRoots | None = None,
    _build_capacity_roots: _execution.CapacityRoots | None = None,
) -> dict[str, str]:
    # Keep the historical module-level entry point, but make it use the same
    # exact-source, build-evidence, assertion, replay, and pre-copy barriers as
    # the public facade.  The import is local to avoid a module cycle.
    from . import oci_publish_guards as guarded

    return guarded.publish(
        plan,
        environment,
        _capacity_roots=_capacity_roots,
        _build_capacity_roots=_build_capacity_roots,
    )


def read_back(
    plan: PublishPlan,
    environment: Mapping[str, str],
    *,
    _capacity_roots: _execution.CapacityRoots | None = None,
) -> dict[str, str]:
    capacity_roots = publication_capacity_roots(
        environment, _capacity_roots=_capacity_roots
    )
    _validate_active_publication_capacity(capacity_roots)
    root = capacity_roots.scratch_root
    authfile = _load_publication_plan_state(plan, capacity_roots)
    _require_new_state_path(capacity_roots, "readback.json")
    published = _read_state(capacity_roots, "publication.json")
    rows = _mapping(published.get("targets"), "publication_state_missing")
    readback_root = root / "readback"
    _require(not readback_root.exists() and not readback_root.is_symlink(), "residue_detected")
    readback_root.mkdir(mode=0o700)
    verified: dict[str, Any] = {}
    for target in plan.targets:
        row = _mapping(rows.get(target.target_id), "publication_state_missing")
        destination = readback_root / target.target_id
        _copy(
            f"docker://{target.version_reference}",
            f"oci:{destination}:readback",
            authfile,
            capacity_roots,
        )
        remote = inspect_layout(destination, target, "readback")
        local = _mapping(row.get("local"), "publication_state_missing")
        _require(remote == local, "registry_readback_mismatch")
        source_digest = _inspect_remote_digest(
            target.source_reference, authfile, capacity_roots
        )
        _require(source_digest == remote["manifest_digest"], "registry_readback_mismatch")
        verified[target.target_id] = {
            "repository": target.registry_repository,
            "version_reference": target.version_reference,
            "source_reference": target.source_reference,
            "manifest_digest": remote["manifest_digest"],
            "platforms": remote["platforms"],
            "replayed": bool(row.get("replayed")),
        }
    _write_state(capacity_roots, "readback.json", {"targets": verified})
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


def verify(
    plan: PublishPlan,
    environment: Mapping[str, str],
    *,
    _capacity_roots: _execution.CapacityRoots | None = None,
) -> dict[str, str]:
    roots = publication_capacity_roots(
        environment, _capacity_roots=_capacity_roots
    )
    _validate_active_publication_capacity(roots)
    root = roots.scratch_root
    _load_publication_plan_state(plan, roots)
    readback = _read_state(roots, "readback.json")
    rows = _mapping(readback.get("targets"), "publication_state_missing")
    build = _mapping(readback.get("build"), "publication_state_missing")
    _require(
        build.get("source_sha") == plan.admitted_sha
        and build.get("product_id") == plan.product_id
        and build.get("release_version") == plan.release_version
        and isinstance(build.get("evidence_id"), str)
        and re.fullmatch(r"[0-9a-f]{64}", build["evidence_id"]) is not None,
        "publication_state_missing",
    )
    _require(set(rows) == {target.target_id for target in plan.targets}, "publication_state_missing")
    repositories: dict[str, str] = {}
    versions: dict[str, str] = {}
    sources: dict[str, str] = {}
    manifests: dict[str, str] = {}
    platforms: dict[str, Any] = {}
    resolved_inputs: dict[str, Mapping[str, Any]] = {}
    assertion_evidence: dict[str, Mapping[str, Any]] = {}
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
        resolved_inputs[target.target_id] = _validate_resolved_input_evidence(
            row.get("resolved_inputs"),
            target,
            "registry_readback_mismatch",
        )
        assertions = _mapping(
            row.get("assertions"), "publication_state_missing"
        )
        _require(
            set(assertions)
            == {
                "result",
                "verified_platforms",
                "contract_digest",
                "runtime",
                "filesystem",
                "healthcheck",
            }
            and assertions.get("result") == "passed"
            and assertions.get("verified_platforms") == list(target.platforms)
            and isinstance(assertions.get("contract_digest"), str)
            and _DIGEST.fullmatch(assertions["contract_digest"]) is not None,
            "registry_readback_mismatch",
        )
        assertion_evidence[target.target_id] = assertions
        replayed = replayed or bool(row.get("replayed"))
    evidence = {
        "api": "oci.publish",
        "version": "1.0.0",
        "source": plan.admitted_sha,
        "product": plan.product_id,
        "release_version": plan.release_version,
        "registry_write_policy": registry_write_policy_evidence(plan),
        "manifests": manifests,
        "platforms": platforms,
        "resolved_inputs": resolved_inputs,
        "assertions": assertion_evidence,
        "build_evidence_id": build["evidence_id"],
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
        "resolved_inputs_json": json.dumps(
            resolved_inputs, sort_keys=True, separators=(",", ":")
        ),
        "assertion_evidence_json": json.dumps(
            assertion_evidence, sort_keys=True, separators=(",", ":")
        ),
        "replayed": str(replayed).lower(),
        "evidence_id": evidence_id,
        "canary_id": plan.canary_id or "",
        "previous_known_good": plan.previous_known_good or "",
        "rollback_id": plan.rollback_id or "",
        "failure_code": "",
    }


def cleanup(
    environment: Mapping[str, str],
    *,
    _capacity_roots: _execution.CapacityRoots | None = None,
) -> None:
    roots = publication_capacity_roots(
        environment, _capacity_roots=_capacity_roots
    )
    try:
        _execution._validate_capacity_parents(roots)  # noqa: SLF001
        capacity_clean = _execution.remove_capacity_roots(roots)
    except _execution.OciBuildError as error:
        raise OciPublishError(error.code) from error
    if not capacity_clean:
        raise OciPublishError("cleanup_failed")


def residue(
    environment: Mapping[str, str],
    *,
    _capacity_roots: _execution.CapacityRoots | None = None,
) -> None:
    roots = publication_capacity_roots(
        environment, _capacity_roots=_capacity_roots
    )
    try:
        _execution._validate_capacity_parents(roots)  # noqa: SLF001
        capacity_residue = any(
            _execution._capacity_residue_names(parent, roots)  # noqa: SLF001
            for _key, parent in _execution._capacity_rows(roots)  # noqa: SLF001
        )
    except _execution.OciBuildError as error:
        raise OciPublishError(error.code) from error
    _require(not capacity_residue, "residue_detected")
