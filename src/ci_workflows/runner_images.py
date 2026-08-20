"""Simple shared contract for centrally owned runner images."""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TextIO

REGISTRY_HOST = "git.faruqi.dev"
REGISTRY_NAMESPACE = "mimranfaruqi"
SMOKE_COMMAND = "/usr/local/bin/runner-image-smoke"

_SHA = re.compile(r"^[0-9a-f]{40}$")
_OCI_TAG = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$")
_IMAGE_ID = re.compile(r"^[a-z][a-z0-9-]{0,31}$")


class RunnerImageError(ValueError):
    """Raised when a runner-image request is outside the fixed contract."""


@dataclass(frozen=True, slots=True)
class RunnerImage:
    image_id: str
    context_path: str
    registry_repository: str

    @property
    def dockerfile_path(self) -> str:
        return f"{self.context_path}/Dockerfile"

    @property
    def smoke_script_path(self) -> str:
        return f"{self.context_path}/smoke.sh"


@dataclass(frozen=True, slots=True)
class RunnerImagePlan:
    image: str
    source_sha: str
    context_path: str
    dockerfile_path: str
    registry_host: str
    registry_repository: str
    local_reference: str
    remote_reference: str
    latest_reference: str
    smoke_command: str = SMOKE_COMMAND

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


_IMAGES = (
    RunnerImage(
        "general",
        "runner-images/general",
        f"{REGISTRY_HOST}/{REGISTRY_NAMESPACE}/github-actions-runner-general",
    ),
    RunnerImage(
        "mobile",
        "runner-images/mobile",
        f"{REGISTRY_HOST}/{REGISTRY_NAMESPACE}/github-actions-runner-mobile",
    ),
    RunnerImage(
        "buildah",
        "runner-images/buildah",
        f"{REGISTRY_HOST}/{REGISTRY_NAMESPACE}/github-actions-runner-buildah",
    ),
    RunnerImage(
        "service",
        "runner-images/service",
        f"{REGISTRY_HOST}/{REGISTRY_NAMESPACE}/github-actions-runner-service",
    ),
    RunnerImage(
        "docker",
        "runner-images/docker",
        f"{REGISTRY_HOST}/{REGISTRY_NAMESPACE}/github-actions-runner-docker",
    ),
    RunnerImage(
        "flux-control",
        "runner-images/flux-control",
        f"{REGISTRY_HOST}/{REGISTRY_NAMESPACE}/github-actions-runner-flux-control",
    ),
)
IMAGE_IDS = tuple(item.image_id for item in _IMAGES)
_IMAGE_BY_ID = {item.image_id: item for item in _IMAGES}


def validate_source_sha(value: str) -> str:
    if _SHA.fullmatch(value) is None:
        raise RunnerImageError("source_sha must be an exact lowercase 40-character Git SHA")
    return value


def validate_release_tag(value: str) -> str:
    if _OCI_TAG.fullmatch(value) is None or value.casefold() == "latest":
        raise RunnerImageError(
            "release tag must be a human-readable OCI-compatible Git tag and must not be latest; latest is reserved for the mutable runner-image alias"
        )
    return value


def resolve_image(image_id: str) -> RunnerImage:
    if _IMAGE_ID.fullmatch(image_id) is None or image_id not in _IMAGE_BY_ID:
        raise RunnerImageError(
            f"unsupported runner image {image_id!r}; expected one of {', '.join(IMAGE_IDS)}"
        )
    return _IMAGE_BY_ID[image_id]


def _require_regular_file(root: Path, relative: str) -> None:
    root = root.resolve()
    path = root / relative
    try:
        resolved = path.resolve(strict=True)
    except OSError as error:
        raise RunnerImageError(f"missing runner-image source file: {relative}") from error
    if root not in resolved.parents or not resolved.is_file() or path.is_symlink():
        raise RunnerImageError(f"unsafe runner-image source file: {relative}")


def build_plan(
    source_root: Path,
    *,
    image_id: str,
    source_sha: str,
    release_tag: str | None = None,
) -> RunnerImagePlan:
    """Resolve one fixed image into local and optional publication references."""

    image = resolve_image(image_id)
    source_sha = validate_source_sha(source_sha)
    root = source_root.resolve()
    if not root.is_dir() or source_root.is_symlink():
        raise RunnerImageError("source root must be a real directory")
    _require_regular_file(root, image.dockerfile_path)
    _require_regular_file(root, image.smoke_script_path)

    tag = validate_release_tag(release_tag) if release_tag else None
    remote = f"{image.registry_repository}:{tag}" if tag else ""
    return RunnerImagePlan(
        image=image.image_id,
        source_sha=source_sha,
        context_path=image.context_path,
        dockerfile_path=image.dockerfile_path,
        registry_host=REGISTRY_HOST,
        registry_repository=image.registry_repository,
        local_reference=f"ciw-runner-{image.image_id}:sha-{source_sha[:12]}",
        remote_reference=remote,
        latest_reference=f"{image.registry_repository}:latest",
    )


def release_matrix() -> tuple[str, ...]:
    """Return the exact repository-level runner-image release set."""

    return IMAGE_IDS


def write_github_outputs(handle: TextIO, values: dict[str, str]) -> None:
    """Write already-bounded scalar values to a GitHub output file."""

    for key, value in values.items():
        if "\n" in key or "\r" in key or "\n" in value or "\r" in value:
            raise RunnerImageError("GitHub output values must be single-line")
        handle.write(f"{key}={value}\n")


def plan_outputs(plan: RunnerImagePlan) -> dict[str, str]:
    values = plan.to_dict()
    return {key: str(value) for key, value in values.items()}


def release_outputs(tag: str, source_sha: str) -> dict[str, str]:
    return {
        "release_tag": validate_release_tag(tag),
        "source_sha": validate_source_sha(source_sha),
        "images_json": json.dumps(release_matrix(), separators=(",", ":")),
    }
