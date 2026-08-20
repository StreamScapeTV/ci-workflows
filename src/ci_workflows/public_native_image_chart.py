"""Public GHCR native image + Helm publication helpers."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path
from typing import Mapping

from .packaging_primitives import (
    PackagingError,
    helm_push,
    push_image,
    registry_authenticate,
    registry_logout,
)

REGISTRY = "ghcr.io"
REGISTRY_NAMESPACE = "streamscapetv"
CHART_NAMESPACE = "streamscapetv/helm-charts"
_DIGEST_PREFIX = "sha256:"


class PublicNativeReleaseError(RuntimeError):
    """Raised when public release state or remote evidence is invalid."""


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise PublicNativeReleaseError(code)


def _run(
    argv: list[str],
    *,
    environment: Mapping[str, str],
    cwd: Path | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            argv,
            cwd=cwd,
            env={str(key): str(value) for key, value in environment.items()},
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=300,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise PublicNativeReleaseError("tool_execution_failed") from error
    if check and result.returncode:
        raise PublicNativeReleaseError("tool_execution_failed")
    return result


def image_reference(image_name: str, version: str) -> str:
    return f"{REGISTRY}/{REGISTRY_NAMESPACE}/{image_name}:{version}"


def chart_reference(chart_name: str, version: str) -> str:
    return f"{REGISTRY}/{CHART_NAMESPACE}/{chart_name}:{version}"


def verify_host(environment: Mapping[str, str]) -> None:
    """Require the standard hosted Linux tools used by the existing primitives."""

    _require(environment.get("RUNNER_ENVIRONMENT") == "github-hosted", "not_github_hosted")
    _require(environment.get("RUNNER_OS") == "Linux", "not_linux")
    machine = _run(["uname", "-m"], environment=environment).stdout.strip()
    _require(machine == "x86_64", "not_amd64")
    for tool in ("buildah", "skopeo", "helm"):
        _run([tool, "--version"], environment=environment)


def authenticate(
    *,
    environment: Mapping[str, str],
    cwd: Path,
) -> None:
    """Authenticate Buildah and Helm to fixed GHCR using the repository token."""

    values = dict(environment)
    _require(bool(values.get("CIW_REGISTRY_USERNAME")), "missing_registry_username")
    _require(bool(values.get("CIW_REGISTRY_TOKEN")), "missing_registry_token")
    registry_authenticate(REGISTRY, environment=values, tool="buildah", cwd=cwd)
    registry_authenticate(REGISTRY, environment=values, tool="helm", cwd=cwd)


def require_unused_version(
    *,
    image_name: str,
    chart_name: str,
    version: str,
    environment: Mapping[str, str],
) -> None:
    """Reject an already-published immutable image or chart version."""

    authfile = environment.get("REGISTRY_AUTH_FILE", "")
    _require(bool(authfile), "missing_registry_auth_file")
    for reference in (
        image_reference(image_name, version),
        chart_reference(chart_name, version),
    ):
        result = _run(
            ["skopeo", "inspect", "--authfile", authfile, f"docker://{reference}"],
            environment=environment,
            check=False,
        )
        if result.returncode == 0:
            raise PublicNativeReleaseError("immutable_version_exists")


def publish(
    *,
    image_name: str,
    chart_name: str,
    version: str,
    package_path: Path,
    environment: Mapping[str, str],
    cwd: Path,
) -> None:
    """Publish the already-built image and already-packaged chart once."""

    values = dict(environment)
    push_image(
        image_reference(image_name, version),
        environment=values,
        tool="buildah",
        cwd=cwd,
    )
    helm_push(
        package_path,
        f"oci://{REGISTRY}/{CHART_NAMESPACE}",
        environment=values,
    )


def _anonymous_inspect(
    reference: str,
    *,
    environment: Mapping[str, str],
    authfile: Path,
) -> tuple[dict[str, object], str]:
    raw = _run(
        ["skopeo", "inspect", "--authfile", str(authfile), "--raw", f"docker://{reference}"],
        environment=environment,
    ).stdout.encode()
    inspection = _run(
        ["skopeo", "inspect", "--authfile", str(authfile), f"docker://{reference}"],
        environment=environment,
    ).stdout
    try:
        payload = json.loads(inspection)
    except json.JSONDecodeError as error:
        raise PublicNativeReleaseError("invalid_remote_inspection") from error
    _require(isinstance(payload, dict), "invalid_remote_inspection")
    digest = f"{_DIGEST_PREFIX}{hashlib.sha256(raw).hexdigest()}"
    advertised = payload.get("Digest")
    _require(advertised == digest, "remote_digest_mismatch")
    return payload, digest


def readback_public(
    *,
    image_name: str,
    chart_name: str,
    version: str,
    anonymous_authfile: Path,
    environment: Mapping[str, str],
    cwd: Path,
) -> dict[str, str]:
    """Drop authenticated state, then prove anonymous image/chart read-back."""

    values = dict(environment)
    for tool in ("helm", "buildah"):
        registry_logout(REGISTRY, environment=values, tool=tool, cwd=cwd)
    _require(not anonymous_authfile.exists(), "anonymous_authfile_exists")
    anonymous_authfile.parent.mkdir(parents=True, exist_ok=True)
    anonymous_authfile.write_text("{}\n", encoding="utf-8")
    anonymous_authfile.chmod(0o600)

    image = image_reference(image_name, version)
    chart = chart_reference(chart_name, version)
    image_payload, image_digest = _anonymous_inspect(
        image, environment=values, authfile=anonymous_authfile
    )
    _require(
        image_payload.get("Os") == "linux" and image_payload.get("Architecture") == "amd64",
        "remote_image_platform_mismatch",
    )
    _, chart_digest = _anonymous_inspect(
        chart, environment=values, authfile=anonymous_authfile
    )
    return {
        "image_reference": image,
        "image_digest": image_digest,
        "chart_reference": f"oci://{chart}",
        "chart_digest": chart_digest,
    }


def cleanup(
    *,
    image_name: str,
    version: str,
    state_root: Path,
    environment: Mapping[str, str],
    cwd: Path,
) -> None:
    """Remove exact hosted publication state without weakening cleanup failures."""

    values = dict(environment)
    for tool in ("helm", "buildah"):
        try:
            registry_logout(REGISTRY, environment=values, tool=tool, cwd=cwd)
        except (PackagingError, PublicNativeReleaseError):
            pass
    _run(
        ["buildah", "rmi", image_reference(image_name, version)],
        environment=values,
        cwd=cwd,
        check=False,
    )
    if state_root.exists() or state_root.is_symlink():
        if state_root.is_symlink():
            state_root.unlink()
        else:
            for child in sorted(state_root.rglob("*"), reverse=True):
                if child.is_symlink() or child.is_file():
                    child.unlink()
                elif child.is_dir():
                    child.rmdir()
            state_root.rmdir()
    _require(not state_root.exists() and not state_root.is_symlink(), "cleanup_failed")


__all__ = (
    "CHART_NAMESPACE",
    "PublicNativeReleaseError",
    "REGISTRY",
    "REGISTRY_NAMESPACE",
    "authenticate",
    "chart_reference",
    "cleanup",
    "image_reference",
    "publish",
    "readback_public",
    "require_unused_version",
    "verify_host",
)
