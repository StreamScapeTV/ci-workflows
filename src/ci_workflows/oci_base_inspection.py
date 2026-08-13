"""Offline inspection of an immutable OCI base-image layout.

The caller is responsible for copying a digest-pinned public image into an OCI
layout.  This module deliberately has no transport, authentication, subprocess,
or write capability: it only validates the resulting descriptor graph and
returns redacted, deterministic evidence.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_REFERENCE = re.compile(r"^[^\s@]+@(sha256:[0-9a-f]{64})$")
_INDEX_MEDIA_TYPE = "application/vnd.oci.image.index.v1+json"
_MANIFEST_MEDIA_TYPE = "application/vnd.oci.image.manifest.v1+json"
_CONFIG_MEDIA_TYPE = "application/vnd.oci.image.config.v1+json"
_LAYER_MEDIA_TYPES = frozenset(
    {
        "application/vnd.oci.image.layer.v1.tar",
        "application/vnd.oci.image.layer.v1.tar+gzip",
        "application/vnd.oci.image.layer.nondistributable.v1.tar",
        "application/vnd.oci.image.layer.nondistributable.v1.tar+gzip",
    }
)
_SUPPORTED_PLATFORMS = frozenset({"linux/amd64", "linux/arm64/v8"})
_MAX_JSON_BYTES = 32 * 1024 * 1024


class OciBaseInspectionError(RuntimeError):
    """Fail-closed inspection error carrying one stable, non-secret code."""

    def __init__(self, code: str) -> None:
        if re.fullmatch(r"[a-z][a-z0-9_]{2,95}", code) is None:
            raise ValueError("OCI base inspection error code must be safe")
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class OciBasePlatformIdentity:
    """Exact immutable identities for one selected base-image platform."""

    platform: str
    manifest_digest: str
    config_digest: str

    def to_dict(self) -> dict[str, str]:
        return {
            "platform": self.platform,
            "manifest_digest": self.manifest_digest,
            "config_digest": self.config_digest,
        }


@dataclass(frozen=True)
class OciBaseManifestSelection:
    """One requested manifest selected from the authenticated root object."""

    platform: str
    manifest_digest: str
    manifest_size: int

    def to_dict(self) -> dict[str, object]:
        return {
            "platform": self.platform,
            "manifest_digest": self.manifest_digest,
            "manifest_size": self.manifest_size,
        }


@dataclass(frozen=True)
class OciBaseRootInspection:
    """Redacted result of authenticating an index-only or direct root layout."""

    reference_digest: str
    root_digest: str
    root_media_type: str
    manifests: tuple[OciBaseManifestSelection, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "reference_digest": self.reference_digest,
            "root_digest": self.root_digest,
            "root_media_type": self.root_media_type,
            "manifests": [manifest.to_dict() for manifest in self.manifests],
        }


@dataclass(frozen=True)
class OciBaseInspectionEvidence:
    """Canonical evidence with the registry/repository name intentionally removed."""

    reference_digest: str
    root_digest: str
    root_media_type: str
    platforms: tuple[OciBasePlatformIdentity, ...]
    evidence_id: str

    def evidence_payload(self) -> dict[str, object]:
        return {
            "schema": "oci-base-inspection/v1",
            "reference_digest": self.reference_digest,
            "root_digest": self.root_digest,
            "root_media_type": self.root_media_type,
            "platforms": [item.to_dict() for item in self.platforms],
        }

    def to_dict(self) -> dict[str, object]:
        return {**self.evidence_payload(), "evidence_id": self.evidence_id}

    def canonical_json(self) -> str:
        return _canonical_json(self.to_dict())


def _fail(code: str) -> None:
    raise OciBaseInspectionError(code)


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            _fail("oci_json_duplicate_key")
        result[key] = value
    return result


def _reject_nonfinite(_value: str) -> object:
    _fail("oci_json_invalid")


def _open_regular(path: Path) -> int:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise OciBaseInspectionError("oci_layout_malformed") from error
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            _fail("oci_layout_malformed")
    except BaseException:
        os.close(descriptor)
        raise
    return descriptor


def _read_regular(
    path: Path,
    *,
    maximum: int | None = None,
    capture: bool = True,
) -> tuple[bytes, int, str]:
    descriptor = _open_regular(path)
    digest = hashlib.sha256()
    content = bytearray()
    size = 0
    try:
        while True:
            block = os.read(descriptor, 1024 * 1024)
            if not block:
                break
            size += len(block)
            if maximum is not None and size > maximum:
                _fail("oci_json_too_large")
            digest.update(block)
            if capture:
                content.extend(block)
    except OciBaseInspectionError:
        raise
    except OSError as error:
        raise OciBaseInspectionError("oci_layout_malformed") from error
    finally:
        os.close(descriptor)
    return bytes(content), size, f"sha256:{digest.hexdigest()}"


def _read_json(path: Path) -> object:
    content, _, _ = _read_regular(path, maximum=_MAX_JSON_BYTES)
    try:
        return json.loads(
            content.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_nonfinite,
        )
    except OciBaseInspectionError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise OciBaseInspectionError("oci_json_invalid") from error


def _descriptor_fields(
    descriptor: object,
    allowed_media_types: frozenset[str],
) -> tuple[str, str, int]:
    if not isinstance(descriptor, Mapping):
        _fail("oci_descriptor_invalid")
    media_type = descriptor.get("mediaType")
    digest = descriptor.get("digest")
    size = descriptor.get("size")
    if (
        not isinstance(media_type, str)
        or media_type not in allowed_media_types
        or not isinstance(digest, str)
        or _DIGEST.fullmatch(digest) is None
        or type(size) is not int
        or size < 0
    ):
        _fail("oci_descriptor_invalid")
    return media_type, digest, size


def _blob_path(layout: Path, digest: str) -> Path:
    blobs = layout / "blobs"
    algorithm = blobs / "sha256"
    try:
        if (
            not blobs.is_dir()
            or blobs.is_symlink()
            or not algorithm.is_dir()
            or algorithm.is_symlink()
        ):
            _fail("oci_layout_malformed")
    except OSError as error:
        raise OciBaseInspectionError("oci_layout_malformed") from error
    return algorithm / digest.removeprefix("sha256:")


def _verify_descriptor_blob(
    layout: Path,
    descriptor: object,
    allowed_media_types: frozenset[str],
    *,
    json_blob: bool,
) -> tuple[str, str, object | None]:
    media_type, expected_digest, expected_size = _descriptor_fields(
        descriptor, allowed_media_types
    )
    content, actual_size, actual_digest = _read_regular(
        _blob_path(layout, expected_digest),
        maximum=_MAX_JSON_BYTES if json_blob else None,
        capture=json_blob,
    )
    if actual_digest != expected_digest:
        _fail("oci_digest_mismatch")
    if actual_size != expected_size:
        _fail("oci_size_mismatch")
    if not json_blob:
        return media_type, expected_digest, None
    try:
        payload = json.loads(
            content.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_nonfinite,
        )
    except OciBaseInspectionError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise OciBaseInspectionError("oci_json_invalid") from error
    return media_type, expected_digest, payload


def _platform_identity(value: object, *, expected: str | None = None) -> str:
    if not isinstance(value, Mapping):
        _fail("oci_platform_invalid")
    os_name = value.get("os")
    architecture = value.get("architecture")
    variant = value.get("variant")
    if (
        not isinstance(os_name, str)
        or not isinstance(architecture, str)
        or variant is not None and not isinstance(variant, str)
    ):
        _fail("oci_platform_invalid")
    if expected is not None:
        expected_parts = expected.split("/")
        expected_variant = expected_parts[2] if len(expected_parts) == 3 else None
        if (
            os_name != expected_parts[0]
            or architecture != expected_parts[1]
            or variant is not None and variant != expected_variant
        ):
            _fail("oci_platform_mismatch")
        return expected
    if os_name == "linux" and architecture == "arm64" and variant is None:
        platform = "linux/arm64/v8"
    else:
        platform = f"{os_name}/{architecture}" + (f"/{variant}" if variant else "")
    if platform not in _SUPPORTED_PLATFORMS:
        _fail("oci_platform_unsupported")
    return platform


def _validate_document_type(payload: object, media_type: str) -> Mapping[str, object]:
    if not isinstance(payload, Mapping) or payload.get("schemaVersion") != 2:
        _fail("oci_document_invalid")
    declared_media_type = payload.get("mediaType")
    if declared_media_type not in {None, media_type}:
        _fail("oci_media_type_mismatch")
    return payload


def _inspect_manifest(
    layout: Path,
    descriptor: object,
    *,
    expected_platform: str,
) -> OciBasePlatformIdentity:
    if not isinstance(descriptor, Mapping):
        _fail("oci_descriptor_invalid")
    declared_platform = descriptor.get("platform")
    declared_identity = (
        None
        if declared_platform is None
        else _platform_identity(declared_platform, expected=expected_platform)
    )
    media_type, manifest_digest, payload = _verify_descriptor_blob(
        layout,
        descriptor,
        frozenset({_MANIFEST_MEDIA_TYPE}),
        json_blob=True,
    )
    manifest = _validate_document_type(payload, media_type)
    config_descriptor = manifest.get("config")
    layers = manifest.get("layers")
    if not isinstance(config_descriptor, Mapping) or not isinstance(layers, list):
        _fail("oci_manifest_invalid")
    _, config_digest, config_payload = _verify_descriptor_blob(
        layout,
        config_descriptor,
        frozenset({_CONFIG_MEDIA_TYPE}),
        json_blob=True,
    )
    if not isinstance(config_payload, Mapping):
        _fail("oci_config_invalid")
    config_identity = _platform_identity(config_payload, expected=expected_platform)
    if declared_identity is not None and declared_identity != config_identity:
        _fail("oci_platform_mismatch")
    runtime_config = config_payload.get("config")
    rootfs = config_payload.get("rootfs")
    if (
        runtime_config is not None and not isinstance(runtime_config, Mapping)
        or not isinstance(rootfs, Mapping)
        or rootfs.get("type") != "layers"
        or not isinstance(rootfs.get("diff_ids"), list)
        or len(rootfs["diff_ids"]) != len(layers)
        or any(
            not isinstance(diff_id, str) or _DIGEST.fullmatch(diff_id) is None
            for diff_id in rootfs["diff_ids"]
        )
    ):
        _fail("oci_config_invalid")
    if isinstance(runtime_config, Mapping):
        onbuild = runtime_config.get("OnBuild")
        if onbuild is not None and (
            not isinstance(onbuild, list) or len(onbuild) != 0
        ):
            _fail("oci_config_invalid")
    for layer in layers:
        _verify_descriptor_blob(
            layout,
            layer,
            _LAYER_MEDIA_TYPES,
            json_blob=False,
        )
    return OciBasePlatformIdentity(
        platform=expected_platform,
        manifest_digest=manifest_digest,
        config_digest=config_digest,
    )


def _requested_platforms(values: Sequence[str]) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)):
        _fail("oci_platform_invalid")
    requested = tuple(values)
    if (
        not requested
        or any(not isinstance(item, str) for item in requested)
        or len(requested) != len(set(requested))
        or any(item not in _SUPPORTED_PLATFORMS for item in requested)
    ):
        _fail("oci_platform_invalid")
    return tuple(sorted(requested))


def _reference_digest(reference: str) -> str:
    if not isinstance(reference, str):
        _fail("oci_reference_invalid")
    match = _REFERENCE.fullmatch(reference)
    if match is None or "://" in reference:
        _fail("oci_reference_invalid")
    return match.group(1)


def _layout_root(layout: Path) -> tuple[Mapping[str, object], object]:
    try:
        if not layout.is_dir() or layout.is_symlink():
            _fail("oci_layout_malformed")
    except OSError as error:
        raise OciBaseInspectionError("oci_layout_malformed") from error
    if _read_json(layout / "oci-layout") != {"imageLayoutVersion": "1.0.0"}:
        _fail("oci_layout_malformed")
    root_document = _read_json(layout / "index.json")
    root_index = _validate_document_type(root_document, _INDEX_MEDIA_TYPE)
    root_descriptors = root_index.get("manifests")
    if not isinstance(root_descriptors, list) or len(root_descriptors) != 1:
        _fail("oci_root_descriptor_invalid")
    descriptor = root_descriptors[0]
    if not isinstance(descriptor, Mapping):
        _fail("oci_root_descriptor_invalid")
    return descriptor, root_document


def _descriptor_declared_platform(descriptor: object) -> str | None:
    if not isinstance(descriptor, Mapping):
        _fail("oci_descriptor_invalid")
    # Validate immutable descriptor identity even when the platform is not one
    # requested by this execution.  Its blob may intentionally be absent from
    # an index-only acquisition.
    media_type = descriptor.get("mediaType")
    digest = descriptor.get("digest")
    size = descriptor.get("size")
    if (
        not isinstance(media_type, str)
        or not media_type
        or not isinstance(digest, str)
        or _DIGEST.fullmatch(digest) is None
        or type(size) is not int
        or size < 0
    ):
        _fail("oci_descriptor_invalid")
    platform = descriptor.get("platform")
    if platform is None:
        return None
    if not isinstance(platform, Mapping):
        _fail("oci_platform_invalid")
    os_name = platform.get("os")
    architecture = platform.get("architecture")
    variant = platform.get("variant")
    if (
        not isinstance(os_name, str)
        or not isinstance(architecture, str)
        or variant is not None and not isinstance(variant, str)
    ):
        _fail("oci_platform_invalid")
    if os_name == "linux" and architecture == "arm64" and variant is None:
        return "linux/arm64/v8"
    return f"{os_name}/{architecture}" + (f"/{variant}" if variant else "")


def _inspect_child_layout(
    child_layout: Path,
    expected_descriptor: Mapping[str, object],
    expected_platform: str,
) -> OciBasePlatformIdentity:
    child_descriptor, _ = _layout_root(child_layout)
    _, expected_digest, expected_size = _descriptor_fields(
        expected_descriptor,
        frozenset({_MANIFEST_MEDIA_TYPE}),
    )
    _, child_digest, child_size = _descriptor_fields(
        child_descriptor,
        frozenset({_MANIFEST_MEDIA_TYPE}),
    )
    if child_digest != expected_digest or child_size != expected_size:
        _fail("oci_child_digest_mismatch")
    return _inspect_manifest(
        child_layout,
        child_descriptor,
        expected_platform=expected_platform,
    )


def _inspect_root_layout(
    root_layout: Path,
    declared_reference: str,
    requested: tuple[str, ...],
) -> tuple[OciBaseRootInspection, Mapping[str, Mapping[str, object]]]:
    reference_digest = _reference_digest(declared_reference)
    root_descriptor, _ = _layout_root(root_layout)
    root_media_type, root_digest, root_payload = _verify_descriptor_blob(
        root_layout,
        root_descriptor,
        frozenset({_INDEX_MEDIA_TYPE, _MANIFEST_MEDIA_TYPE}),
        json_blob=True,
    )
    if root_digest != reference_digest:
        _fail("oci_reference_digest_mismatch")

    selected: dict[str, Mapping[str, object]] = {}
    if root_media_type == _MANIFEST_MEDIA_TYPE:
        if len(requested) != 1:
            _fail("oci_platform_set_mismatch")
        declared_platform = root_descriptor.get("platform")
        if declared_platform is not None:
            _platform_identity(declared_platform, expected=requested[0])
        selected[requested[0]] = root_descriptor
    else:
        nested_index = _validate_document_type(root_payload, _INDEX_MEDIA_TYPE)
        manifests = nested_index.get("manifests")
        if not isinstance(manifests, list) or not manifests:
            _fail("oci_index_invalid")
        for descriptor in manifests:
            platform = _descriptor_declared_platform(descriptor)
            if platform not in requested:
                continue
            if not isinstance(descriptor, Mapping):
                _fail("oci_descriptor_invalid")
            if descriptor.get("mediaType") != _MANIFEST_MEDIA_TYPE:
                _fail("oci_descriptor_invalid")
            if platform in selected:
                _fail("oci_platform_duplicate")
            selected[platform] = descriptor
        if tuple(sorted(selected)) != requested:
            _fail("oci_platform_set_mismatch")

    manifests_result: list[OciBaseManifestSelection] = []
    for platform in requested:
        _, digest, size = _descriptor_fields(
            selected[platform],
            frozenset({_MANIFEST_MEDIA_TYPE}),
        )
        manifests_result.append(
            OciBaseManifestSelection(
                platform=platform,
                manifest_digest=digest,
                manifest_size=size,
            )
        )
    return (
        OciBaseRootInspection(
            reference_digest=reference_digest,
            root_digest=root_digest,
            root_media_type=root_media_type,
            manifests=tuple(manifests_result),
        ),
        selected,
    )


def inspect_oci_base_root_layout(
    root_layout: Path,
    declared_reference: str,
    requested_platforms: Sequence[str],
) -> OciBaseRootInspection:
    """Authenticate a root layout and select exact requested child manifests.

    This first acquisition phase supports an index-only layout: unrequested
    child blobs need not exist locally.  The returned manifest digests are safe
    inputs for separate exact child acquisitions.
    """

    requested = _requested_platforms(requested_platforms)
    result, _ = _inspect_root_layout(
        root_layout,
        declared_reference,
        requested,
    )
    return result


def inspect_oci_base_layout(
    root_layout: Path,
    declared_reference: str,
    requested_platforms: Sequence[str],
    child_layouts: Mapping[str, Path] | None = None,
) -> OciBaseInspectionEvidence:
    """Validate a copied OCI layout and return canonical, redacted evidence.

    ``declared_reference`` must be a public image name followed by an exact
    ``@sha256:...`` digest.  Only the digest is retained.  The layout root must
    contain exactly one descriptor whose digest agrees with that declaration.
    A direct manifest is validated in ``root_layout``.  For an image index,
    ``root_layout`` may contain only the exact root index blob and
    ``child_layouts`` must map every requested platform to a separately copied
    exact child-manifest layout.  Unrequested platforms and attestations in the
    authenticated root index need not be fetched.
    """

    requested = _requested_platforms(requested_platforms)
    root_inspection, selected = _inspect_root_layout(
        root_layout,
        declared_reference,
        requested,
    )

    if root_inspection.root_media_type == _MANIFEST_MEDIA_TYPE:
        if len(requested) != 1 or child_layouts:
            _fail("oci_child_layout_set_mismatch")
        identities = (
            _inspect_manifest(
                root_layout,
                selected[requested[0]],
                expected_platform=requested[0],
            ),
        )
    else:
        if child_layouts is not None and not isinstance(child_layouts, Mapping):
            _fail("oci_child_layout_set_mismatch")
        children = {} if child_layouts is None else dict(child_layouts)
        if (
            any(not isinstance(platform, str) for platform in children)
            or tuple(sorted(children)) != requested
            or any(not isinstance(path, Path) for path in children.values())
        ):
            _fail("oci_child_layout_set_mismatch")
        identities = tuple(
            _inspect_child_layout(children[platform], selected[platform], platform)
            for platform in requested
        )

    platforms = tuple(identities)
    payload = {
        "schema": "oci-base-inspection/v1",
        "reference_digest": root_inspection.reference_digest,
        "root_digest": root_inspection.root_digest,
        "root_media_type": root_inspection.root_media_type,
        "platforms": [item.to_dict() for item in platforms],
    }
    evidence_id = hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()
    return OciBaseInspectionEvidence(
        reference_digest=root_inspection.reference_digest,
        root_digest=root_inspection.root_digest,
        root_media_type=root_inspection.root_media_type,
        platforms=platforms,
        evidence_id=evidence_id,
    )
