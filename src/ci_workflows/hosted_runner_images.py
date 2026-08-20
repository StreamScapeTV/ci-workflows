"""Hosted runner-image resource checks and GHCR publication primitives."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Mapping, Sequence

from .runner_images import resolve_image

_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_SOURCE_SHA = re.compile(r"^[0-9a-f]{40}$")
_DECIMAL_SIZE = re.compile(r"^([0-9]+(?:\.[0-9]+)?)(B|kB|MB|GB|TB)$")
_POSITIVE_INTEGER = re.compile(r"^[1-9][0-9]*$")
_GHCR_REFERENCE = re.compile(
    r"^ghcr\.io/streamscapetv/github-actions-runner-[a-z][a-z0-9-]{0,31}:[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$"
)
_LAYER_HARD_LIMIT_BYTES = 10_000_000_000
_LAYER_SAFETY_LIMIT_BYTES = 9_500_000_000
_COMMAND_TIMEOUT_SECONDS = 3600
_SOURCE_LABEL = "org.opencontainers.image.revision"


class HostedRunnerImageError(RuntimeError):
    """Raised when hosted build evidence or GHCR publication fails closed."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class HostedImageMetrics:
    source_sha: str
    image_reference: str
    image_size_bytes: int
    largest_layer_bytes: int
    workspace_free_bytes: int
    docker_root_free_bytes: int

    def to_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))


def require(condition: bool, code: str) -> None:
    if not condition:
        raise HostedRunnerImageError(code)


def parse_docker_size(value: str) -> int:
    """Parse Docker's decimal human-size format into bytes."""

    match = _DECIMAL_SIZE.fullmatch(value.strip())
    require(match is not None, "invalid_docker_size")
    amount = float(match.group(1))
    factors = {
        "B": 1,
        "kB": 1000,
        "MB": 1000**2,
        "GB": 1000**3,
        "TB": 1000**4,
    }
    return int(amount * factors[match.group(2)])


def validate_layer_sizes(values: Sequence[str]) -> int:
    """Return the largest uncompressed layer size after enforcing GHCR headroom."""

    require(bool(values), "missing_layer_measurement")
    sizes = tuple(parse_docker_size(value) for value in values)
    largest = max(sizes)
    require(largest < _LAYER_HARD_LIMIT_BYTES, "ghcr_layer_limit_exceeded")
    require(largest <= _LAYER_SAFETY_LIMIT_BYTES, "ghcr_layer_limit_headroom")
    return largest


def validate_ghcr_reference(reference: str) -> str:
    require(_GHCR_REFERENCE.fullmatch(reference) is not None, "invalid_ghcr_reference")
    return reference


def validate_source_sha(source_sha: str) -> str:
    require(_SOURCE_SHA.fullmatch(source_sha) is not None, "invalid_source_sha")
    return source_sha


def _remove_exact_path(path: Path, *, expected_parent: Path) -> None:
    """Remove one exact bounded path without following a symlink target."""

    require(path.parent == expected_parent, "invalid_cleanup_path")
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)
    elif path.exists():
        raise HostedRunnerImageError("invalid_cleanup_path")
    require(not path.exists() and not path.is_symlink(), "runner_image_cleanup_failed")


def cleanup_hosted_runner_state(
    *,
    image_id: str,
    workspace: Path,
    runner_temp: Path,
    run_id: str,
    run_attempt: str,
) -> None:
    """Remove the exact per-run Docker auth and image-owned build-input paths."""

    image = resolve_image(image_id)
    require(_POSITIVE_INTEGER.fullmatch(run_id) is not None, "invalid_run_identity")
    require(_POSITIVE_INTEGER.fullmatch(run_attempt) is not None, "invalid_run_identity")

    workspace_root = workspace.resolve(strict=True)
    runner_temp_root = runner_temp.resolve(strict=True)
    require(workspace_root.is_dir() and not workspace.is_symlink(), "invalid_workspace")
    require(runner_temp_root.is_dir() and not runner_temp.is_symlink(), "invalid_runner_temp")

    context = workspace_root / image.context_path
    require(context.is_dir() and not context.is_symlink(), "invalid_image_context")
    build_inputs = context / ".ciw-build-inputs"
    docker_config = runner_temp_root / f"ciw-runner-docker-{run_id}-{run_attempt}-{image.image_id}"

    _remove_exact_path(build_inputs, expected_parent=context)
    _remove_exact_path(docker_config, expected_parent=runner_temp_root)


def _completed(
    argv: Sequence[str],
    *,
    check: bool = True,
    input_text: str | None = None,
    environment: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            list(argv),
            check=check,
            text=True,
            input=input_text,
            capture_output=True,
            env=None if environment is None else dict(environment),
            timeout=_COMMAND_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise HostedRunnerImageError("docker_command_failed") from error


def _docker_root() -> Path:
    result = _completed(["docker", "info", "--format", "{{.DockerRootDir}}"])
    value = result.stdout.strip()
    root = Path(value)
    require(value.startswith("/") and root.is_dir(), "invalid_docker_root")
    return root


def collect_metrics(
    *,
    source_sha: str,
    image_reference: str,
    workspace: Path,
) -> HostedImageMetrics:
    """Collect bounded hosted-runner disk/image/layer measurements."""

    validate_source_sha(source_sha)
    require(workspace.is_dir(), "invalid_workspace")
    size = _completed(
        ["docker", "image", "inspect", image_reference, "--format", "{{.Size}}"]
    ).stdout.strip()
    require(size.isdecimal() and int(size) > 0, "invalid_image_size")
    history = _completed(
        ["docker", "history", "--no-trunc", "--format", "{{.Size}}", image_reference]
    ).stdout.splitlines()
    largest = validate_layer_sizes(history)
    workspace_free = shutil.disk_usage(workspace).free
    docker_free = shutil.disk_usage(_docker_root()).free
    require(workspace_free > 0 and docker_free > 0, "invalid_disk_measurement")
    return HostedImageMetrics(
        source_sha=source_sha,
        image_reference=image_reference,
        image_size_bytes=int(size),
        largest_layer_bytes=largest,
        workspace_free_bytes=workspace_free,
        docker_root_free_bytes=docker_free,
    )


def _local_config_digest(reference: str) -> str:
    value = _completed(
        ["docker", "image", "inspect", reference, "--format", "{{.Id}}"]
    ).stdout.strip()
    require(_DIGEST.fullmatch(value) is not None, "invalid_local_config_digest")
    return value


def _imagetools_manifest(reference: str) -> Mapping[str, object]:
    validate_ghcr_reference(reference)
    result = _completed(
        [
            "docker",
            "buildx",
            "imagetools",
            "inspect",
            reference,
            "--format",
            "{{json .Manifest}}",
        ]
    )
    try:
        value = json.loads(result.stdout.strip())
    except json.JSONDecodeError as error:
        raise HostedRunnerImageError("invalid_registry_manifest") from error
    require(isinstance(value, dict), "invalid_registry_manifest")
    return value


def _imagetools_digest(reference: str) -> str:
    value = _imagetools_manifest(reference).get("digest")
    require(isinstance(value, str) and _DIGEST.fullmatch(value) is not None, "invalid_registry_digest")
    return value


def _imagetools_config_digest(reference: str) -> str:
    validate_ghcr_reference(reference)
    result = _completed(
        ["docker", "buildx", "imagetools", "inspect", reference, "--raw"]
    )
    try:
        manifest = json.loads(result.stdout.strip())
    except json.JSONDecodeError as error:
        raise HostedRunnerImageError("invalid_registry_manifest") from error
    require(isinstance(manifest, dict), "invalid_registry_manifest")
    config = manifest.get("config")
    require(isinstance(config, dict), "unsupported_registry_index")
    value = config.get("digest")
    require(isinstance(value, str) and _DIGEST.fullmatch(value) is not None, "invalid_registry_config_digest")
    return value


def _existing_revision(reference: str) -> tuple[str, str, str] | None:
    """Return an existing tag's source revision and manifest/config digests."""

    validate_ghcr_reference(reference)
    result = _completed(
        [
            "docker",
            "buildx",
            "imagetools",
            "inspect",
            reference,
            "--format",
            "{{json .Image.Config.Labels}}",
        ],
        check=False,
    )
    if result.returncode != 0:
        return None
    try:
        labels = json.loads(result.stdout.strip())
    except json.JSONDecodeError as error:
        raise HostedRunnerImageError("invalid_existing_manifest") from error
    require(isinstance(labels, dict), "invalid_existing_manifest")
    revision = labels.get(_SOURCE_LABEL)
    require(isinstance(revision, str), "missing_source_revision")
    return revision, _imagetools_digest(reference), _imagetools_config_digest(reference)


def _copy_exact_manifest_to_latest(
    *, versioned_reference: str, latest_reference: str, digest: str
) -> None:
    require(_DIGEST.fullmatch(digest) is not None, "invalid_registry_digest")
    _completed(
        [
            "docker",
            "buildx",
            "imagetools",
            "create",
            "--prefer-index=false",
            "--tag",
            latest_reference,
            f"{versioned_reference}@{digest}",
        ]
    )


def publish_exact_image(
    *,
    local_reference: str,
    versioned_reference: str,
    latest_reference: str,
    source_sha: str,
) -> str:
    """Publish one already-smoked local image to GHCR without rebuilding it."""

    validate_source_sha(source_sha)
    validate_ghcr_reference(versioned_reference)
    validate_ghcr_reference(latest_reference)
    require(versioned_reference != latest_reference, "duplicate_release_reference")

    local_revision = _completed(
        [
            "docker",
            "image",
            "inspect",
            local_reference,
            "--format",
            f"{{{{index .Config.Labels \"{_SOURCE_LABEL}\"}}}}",
        ]
    ).stdout.strip()
    require(local_revision == source_sha, "local_source_revision_mismatch")
    local_config_digest = _local_config_digest(local_reference)

    existing = _existing_revision(versioned_reference)
    if existing is not None:
        revision, version_digest, remote_config_digest = existing
        require(revision == source_sha, "immutable_release_conflict")
        require(remote_config_digest == local_config_digest, "immutable_release_conflict")
        _copy_exact_manifest_to_latest(
            versioned_reference=versioned_reference,
            latest_reference=latest_reference,
            digest=version_digest,
        )
        require(
            _imagetools_digest(latest_reference) == version_digest,
            "registry_alias_digest_mismatch",
        )
        return version_digest

    _completed(["docker", "tag", local_reference, versioned_reference])
    _completed(["docker", "tag", local_reference, latest_reference])
    _completed(["docker", "push", versioned_reference])
    version_digest = _imagetools_digest(versioned_reference)
    require(
        _imagetools_config_digest(versioned_reference) == local_config_digest,
        "registry_config_digest_mismatch",
    )
    _completed(["docker", "push", latest_reference])
    latest_digest = _imagetools_digest(latest_reference)
    require(version_digest == latest_digest, "registry_alias_digest_mismatch")
    return version_digest


def verify_anonymous_pullability(
    *,
    versioned_reference: str,
    latest_reference: str,
    expected_digest: str,
) -> None:
    """Verify both GHCR tags resolve anonymously to the published manifest."""

    require(_DIGEST.fullmatch(expected_digest) is not None, "invalid_registry_digest")
    for reference in (versioned_reference, latest_reference):
        validate_ghcr_reference(reference)
        digest = _imagetools_digest(reference)
        require(digest == expected_digest, "anonymous_readback_digest_mismatch")


__all__ = (
    "HostedImageMetrics",
    "HostedRunnerImageError",
    "cleanup_hosted_runner_state",
    "collect_metrics",
    "parse_docker_size",
    "publish_exact_image",
    "validate_ghcr_reference",
    "validate_layer_sizes",
    "verify_anonymous_pullability",
)
