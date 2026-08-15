"""Independent Helm OCI raw-manifest digest and layer read-back proof."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Mapping

from .helm_contract import require
from .helm_execution import _run, _runtime_environment
from .helm_types import HelmValidationError


def remote_chart_manifest_digest(
    source_root: Path,
    state_root: Path,
    chart_reference: str,
    release_version: str,
    expected_package_sha256: str,
    inherited: Mapping[str, str],
) -> str:
    """Hash the exact raw remote manifest bytes and verify the Helm content layer."""

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
    raw = manifest.encode("utf-8")
    require(0 < len(raw) <= 2_000_000, "remote_manifest_read_back_failed")
    digest = "sha256:" + hashlib.sha256(raw).hexdigest()

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
    return digest


__all__ = ["remote_chart_manifest_digest"]
