"""Typed models and stable errors for non-publishing OCI builds."""
from __future__ import annotations

import json
import hashlib
import re
from dataclasses import dataclass, field
from typing import Mapping, Sequence

_SAFE_CODE = re.compile(r"^[a-z][a-z0-9_]{2,95}$")
_BASE_NAME = re.compile(r"^[a-z0-9](?:[a-z0-9._:/-]{0,253}[a-z0-9])?$")
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
OCI_TAG_MAX_LENGTH = 128
_PUBLICATION_HOST = re.compile(
    r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
    r"(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$"
)
_PUBLICATION_PATH_SEGMENT = re.compile(
    r"^[a-z0-9]+(?:(?:[._-]|__)[a-z0-9]+)*$"
)


def is_exact_base_reference(value: object) -> bool:
    """Return whether value is a bounded canonical scratch/digest base identity."""

    if value == "scratch":
        return True
    if not isinstance(value, str) or len(value) > 327:
        return False
    name, separator, digest = value.rpartition("@")
    if (
        separator != "@"
        or _BASE_NAME.fullmatch(name) is None
        or _DIGEST.fullmatch(digest) is None
        or any(part in {"", ".", ".."} for part in name.split("/"))
    ):
        return False
    return True


def is_canonical_publication_repository(value: object) -> bool:
    """Return whether value is a fixed lowercase registry repository identity."""

    if not isinstance(value, str) or not value or len(value) > 255:
        return False
    if value != value.strip() or value != value.lower() or ":" in value or "@" in value:
        return False
    parts = value.split("/")
    if len(parts) < 3 or _PUBLICATION_HOST.fullmatch(parts[0]) is None:
        return False
    if any(_PUBLICATION_PATH_SEGMENT.fullmatch(part) is None for part in parts[1:]):
        return False
    return parts[-1] != "latest"


def has_valid_oci_tag_length(value: object) -> bool:
    """Return whether an already-validated tag fits the OCI/Docker tag bound."""

    return isinstance(value, str) and 1 <= len(value) <= OCI_TAG_MAX_LENGTH


class OciBuildError(RuntimeError):
    """Fail-closed OCI build error carrying one stable code."""

    def __init__(self, code: str) -> None:
        if _SAFE_CODE.fullmatch(code) is None:
            raise ValueError("OCI build error code must be safe")
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class OciBuildRequest:
    repository: str
    admitted_sha: str
    product_id: str
    release_version: str | None
    platform_set: str | None
    artifact_exception_id: str | None
    source_trust: str


@dataclass(frozen=True)
class OciInputPolicy:
    policy_id: str
    allowed_registry_hosts: tuple[str, ...]
    allowed_registry_api_hosts: tuple[str, ...]
    allowed_registry_token_hosts: tuple[str, ...]
    allowed_registry_blob_hosts: tuple[str, ...]
    allowed_download_hosts: tuple[str, ...]
    https_only: bool
    ambient_auth: bool
    redirect_policy: str
    maximum_redirects: int
    maximum_input_bytes: int


@dataclass(frozen=True)
class OciRegistryWritePolicy:
    """Closed registry authority required before an immutable tag write."""

    policy_id: str
    registry_host: str
    required_enforcement: str
    status: str
    authority_repository: str
    authority_source_sha: str | None
    evidence_id: str | None

    def evidence(self) -> dict[str, str]:
        """Project the verified, redacted authority bound to publication."""

        if self.authority_source_sha is None or self.evidence_id is None:
            raise ValueError("registry write policy is not verified")
        return {
            "policy_id": self.policy_id,
            "registry_host": self.registry_host,
            "required_enforcement": self.required_enforcement,
            "status": self.status,
            "authority_repository": self.authority_repository,
            "authority_source_sha": self.authority_source_sha,
            "evidence_id": self.evidence_id,
        }


@dataclass(frozen=True)
class OciTarget:
    target_id: str
    context_path: str
    dockerfile_path: str
    target_stage: str | None
    platforms: tuple[str, ...]
    smoke_script: str | None
    required_user: str | None
    required_entrypoint: tuple[str, ...]
    required_command: tuple[str, ...]
    required_ports: tuple[str, ...]
    required_files: tuple[str, ...]
    required_tools: tuple[str, ...]
    forbidden_tools: tuple[str, ...]
    fixed_build_args: Mapping[str, str]
    secret_mount_ids: tuple[str, ...]
    build_input_lock_path: str
    input_policy_id: str
    publication_repository: str = ""


@dataclass(frozen=True)
class OciBuildPlan:
    repository: str
    admitted_sha: str
    product_id: str
    release_version: str
    source_trust: str
    runner_profile: str
    runs_on: tuple[str, ...]
    workspace_profile: str
    timeout_minutes: int
    builder_id: str
    storage_driver: str
    targets: tuple[OciTarget, ...]
    flux_asset: bool
    canary_id: str | None
    previous_known_good: str | None
    rollback_id: str | None
    adoption_ready: bool
    input_policies: Mapping[str, OciInputPolicy] = field(default_factory=dict)

    def planning_outputs(self) -> dict[str, str]:
        return {
            "result": "planned",
            "product_id": self.product_id,
            "release_version": self.release_version,
            "runner_profile": self.runner_profile,
            "runs_on_json": json.dumps(list(self.runs_on), separators=(",", ":")),
            "workspace_profile": self.workspace_profile,
            "timeout_minutes": str(self.timeout_minutes),
            "builder_id": self.builder_id,
            "source_trust": self.source_trust,
            "platforms_json": json.dumps(
                {target.target_id: list(target.platforms) for target in self.targets},
                sort_keys=True,
                separators=(",", ":"),
            ),
            "flux_asset": str(self.flux_asset).lower(),
            "canary_id": self.canary_id or "",
            "previous_known_good": self.previous_known_good or "",
            "rollback_id": self.rollback_id or "",
            "failure_code": "",
        }


@dataclass(frozen=True)
class OciPlatformResult:
    platform: str
    manifest_digest: str
    config_digest: str
    layer_digests: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "platform": self.platform,
            "manifest_digest": self.manifest_digest,
            "config_digest": self.config_digest,
            "layer_digests": list(self.layer_digests),
        }


@dataclass(frozen=True)
class OciResolvedBasePlatform:
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
class OciResolvedBase:
    stage_id: str
    declared_reference: str
    root_digest: str
    platforms: tuple[OciResolvedBasePlatform, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "stage_id": self.stage_id,
            "declared_reference": self.declared_reference,
            "root_digest": self.root_digest,
            "platforms": [item.to_dict() for item in self.platforms],
        }


@dataclass(frozen=True)
class OciResolvedExternalInput:
    input_id: str
    digest: str
    size_bytes: int

    def to_dict(self) -> dict[str, object]:
        return {
            "input_id": self.input_id,
            "digest": self.digest,
            "size_bytes": self.size_bytes,
        }


@dataclass(frozen=True)
class OciBuildInputEvidence:
    lock_digest: str
    acquisition_policy_id: str
    resolved_bases: tuple[OciResolvedBase, ...]
    resolved_external_inputs: tuple[OciResolvedExternalInput, ...]
    evidence_id: str

    @classmethod
    def empty(cls) -> OciBuildInputEvidence:
        payload = {
            "lock_digest": "none",
            "input_policy_id": "scratch-only-v1",
            "bases": [],
            "external_inputs": [],
        }
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return cls(
            lock_digest="none",
            acquisition_policy_id="scratch-only-v1",
            resolved_bases=(),
            resolved_external_inputs=(),
            evidence_id=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "lock_digest": self.lock_digest,
            "input_policy_id": self.acquisition_policy_id,
            "bases": [item.to_dict() for item in self.resolved_bases],
            "external_inputs": [
                item.to_dict() for item in self.resolved_external_inputs
            ],
            "evidence_id": self.evidence_id,
        }


@dataclass(frozen=True)
class OciTargetResult:
    target_id: str
    index_digest: str
    platform_results: tuple[OciPlatformResult, ...]
    labels: Mapping[str, str]
    smoke_result: str
    publication_manifest_digest: str = ""
    build_input_evidence: OciBuildInputEvidence = field(
        default_factory=OciBuildInputEvidence.empty
    )

    def to_dict(self) -> dict[str, object]:
        return {
            "target_id": self.target_id,
            "index_digest": self.index_digest,
            "publication_manifest_digest": self.publication_manifest_digest,
            "platform_results": [row.to_dict() for row in self.platform_results],
            "labels": dict(sorted(self.labels.items())),
            "smoke_result": self.smoke_result,
            "build_input_evidence": self.build_input_evidence.to_dict(),
        }


def oci_build_evidence_payload(
    admitted_sha: str,
    product_id: str,
    release_version: str,
    targets: Sequence[OciTargetResult | Mapping[str, object]],
    canary_id: str | None,
    previous_known_good: str | None,
    rollback_id: str | None,
) -> dict[str, object]:
    """Return the one canonical exact-build evidence payload."""

    return {
        "api": "oci.build",
        "version": "1.0.0",
        "source": admitted_sha,
        "product": product_id,
        "release_version": release_version,
        "targets": [
            row.to_dict() if isinstance(row, OciTargetResult) else dict(row)
            for row in targets
        ],
        "flux": {
            "canary_id": canary_id,
            "previous_known_good": previous_known_good,
            "rollback_id": rollback_id,
        },
    }


def oci_build_evidence_id(
    admitted_sha: str,
    product_id: str,
    release_version: str,
    targets: Sequence[OciTargetResult | Mapping[str, object]],
    canary_id: str | None,
    previous_known_good: str | None,
    rollback_id: str | None,
) -> str:
    """Hash the canonical exact-build evidence payload."""

    payload = oci_build_evidence_payload(
        admitted_sha,
        product_id,
        release_version,
        targets,
        canary_id,
        previous_known_good,
        rollback_id,
    )
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class OciBuildResult:
    product_id: str
    admitted_sha: str
    release_version: str
    source_date_epoch: int
    targets: tuple[OciTargetResult, ...]
    clean_tree: bool
    cleanup_result: str
    evidence_id: str
    canary_id: str | None
    previous_known_good: str | None
    rollback_id: str | None

    def output_values(self) -> dict[str, str]:
        manifests = {row.target_id: row.index_digest for row in self.targets}
        publication_manifests = {
            row.target_id: row.publication_manifest_digest for row in self.targets
        }
        platforms = {
            row.target_id: [item.to_dict() for item in row.platform_results]
            for row in self.targets
        }
        build_inputs = {
            row.target_id: row.build_input_evidence.to_dict()
            for row in self.targets
        }
        return {
            "result": "success",
            "source_sha": self.admitted_sha,
            "product_id": self.product_id,
            "release_version": self.release_version,
            "manifest_digests_json": json.dumps(
                manifests, sort_keys=True, separators=(",", ":")
            ),
            "publication_manifest_digests_json": json.dumps(
                publication_manifests, sort_keys=True, separators=(",", ":")
            ),
            "platform_results_json": json.dumps(
                platforms, sort_keys=True, separators=(",", ":")
            ),
            "resolved_inputs_json": json.dumps(
                build_inputs, sort_keys=True, separators=(",", ":")
            ),
            "clean_tree": str(self.clean_tree).lower(),
            "cleanup_result": self.cleanup_result,
            "artifact_exception_used": "false",
            "evidence_id": self.evidence_id,
            "canary_id": self.canary_id or "",
            "previous_known_good": self.previous_known_good or "",
            "rollback_id": self.rollback_id or "",
            "failure_code": "",
        }

    def persisted_values(self) -> dict[str, str]:
        """Return internal build state sufficient to verify ``evidence_id``."""

        return {
            **self.output_values(),
            "target_results_json": json.dumps(
                [row.to_dict() for row in self.targets],
                sort_keys=True,
                separators=(",", ":"),
            ),
        }
