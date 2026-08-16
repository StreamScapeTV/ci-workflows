"""Product-neutral OCI/Docker/Helm packaging process primitives."""

from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

REGISTRY_USERNAME_ENV = "CIW_REGISTRY_USERNAME"
REGISTRY_TOKEN_ENV = "CIW_REGISTRY_TOKEN"

_BUILD_ARG_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]*$")
_IMAGE_TOOLS = frozenset({"buildah", "docker", "podman"})
_REGISTRY_TOOLS = frozenset({"buildah", "docker", "helm", "podman"})
_HELM_TOOLS = frozenset({"helm"})


class PackagingError(RuntimeError):
    """Raised when a packaging primitive receives invalid input or a tool fails."""


@dataclass(frozen=True)
class ImageReference:
    """Structured image reference returned by build/tag/push primitives."""

    reference: str


@dataclass(frozen=True)
class ImageInspection:
    """Local image inspection without publication/read-back policy."""

    reference: str
    image_id: str | None
    repo_digests: tuple[str, ...]
    metadata: Mapping[str, object]


@dataclass(frozen=True)
class HelmPackage:
    """One chart archive created by ``helm package``."""

    archive: Path


@dataclass(frozen=True)
class HelmPublishResult:
    """Structured result of an ordinary Helm OCI push."""

    repository: str
    archive: Path


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise PackagingError(message)


def _text(value: str, field: str) -> str:
    _require(isinstance(value, str) and bool(value), f"invalid {field}")
    _require(
        "\x00" not in value and "\n" not in value and "\r" not in value,
        f"invalid {field}",
    )
    return value


def _tool(value: str, allowed: frozenset[str]) -> str:
    value = _text(value, "tool")
    _require(value in allowed, f"unsupported tool: {value}")
    return value


def _environment(environment: Mapping[str, str] | None) -> dict[str, str]:
    source = os.environ if environment is None else environment
    return {str(key): str(value) for key, value in source.items()}


def _run(
    argv: Sequence[str],
    *,
    cwd: Path | None,
    environment: Mapping[str, str] | None,
    stdin: str | None = None,
    timeout: int = 300,
) -> subprocess.CompletedProcess[str]:
    _require(
        bool(argv) and all(isinstance(item, str) and item for item in argv),
        "invalid command",
    )
    try:
        result = subprocess.run(
            list(argv),
            cwd=cwd,
            env=_environment(environment),
            input=stdin,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise PackagingError(f"tool execution failed: {argv[0]}") from error
    if result.returncode:
        raise PackagingError(f"tool execution failed: {argv[0]}")
    return result


def _secret(environment: Mapping[str, str], name: str) -> str:
    value = environment.get(name, "")
    _require(bool(value), f"missing environment secret: {name}")
    return value


def registry_authenticate(
    registry: str,
    *,
    environment: Mapping[str, str],
    tool: str = "docker",
    cwd: Path | None = None,
) -> None:
    """Authenticate using the fixed central registry credential environment names."""

    registry = _text(registry, "registry")
    tool = _tool(tool, _REGISTRY_TOOLS)
    username = _secret(environment, REGISTRY_USERNAME_ENV)
    token = _secret(environment, REGISTRY_TOKEN_ENV)
    if tool == "helm":
        argv = [
            "helm",
            "registry",
            "login",
            registry,
            "--username",
            username,
            "--password-stdin",
        ]
    elif tool == "buildah":
        argv = [
            "buildah",
            "login",
            "--username",
            username,
            "--password-stdin",
            registry,
        ]
    else:
        argv = [
            tool,
            "login",
            registry,
            "--username",
            username,
            "--password-stdin",
        ]
    _run(
        argv,
        cwd=cwd,
        environment=environment,
        stdin=f"{token}\n",
        timeout=60,
    )


def registry_logout(
    registry: str,
    *,
    environment: Mapping[str, str] | None = None,
    tool: str = "docker",
    cwd: Path | None = None,
) -> None:
    """Remove registry login state for a supported client."""

    registry = _text(registry, "registry")
    tool = _tool(tool, _REGISTRY_TOOLS)
    if tool == "helm":
        argv = ["helm", "registry", "logout", registry]
    elif tool == "buildah":
        argv = ["buildah", "logout", registry]
    else:
        argv = [tool, "logout", registry]
    _run(argv, cwd=cwd, environment=environment, timeout=60)


def build_image(
    context: Path,
    dockerfile: Path,
    tag: str,
    *,
    build_args: Mapping[str, str] | None = None,
    environment: Mapping[str, str] | None = None,
    tool: str = "docker",
) -> ImageReference:
    """Build one local image with Docker, Podman, or Buildah."""

    _require(
        context.is_dir() and not context.is_symlink(),
        "invalid build context",
    )
    _require(
        dockerfile.is_file() and not dockerfile.is_symlink(),
        "invalid dockerfile",
    )
    tag = _text(tag, "image tag")
    tool = _tool(tool, _IMAGE_TOOLS)
    command = "bud" if tool == "buildah" else "build"
    argv = [tool, command, "--file", str(dockerfile), "--tag", tag]
    for key, value in sorted((build_args or {}).items()):
        _require(
            _BUILD_ARG_NAME.fullmatch(_text(key, "build argument name")) is not None,
            "invalid build argument name",
        )
        value = _text(value, "build argument value")
        argv.extend(["--build-arg", f"{key}={value}"])
    argv.append(str(context))
    _run(argv, cwd=context, environment=environment)
    return ImageReference(reference=tag)


def inspect_image(
    reference: str,
    *,
    environment: Mapping[str, str] | None = None,
    tool: str = "docker",
    cwd: Path | None = None,
) -> ImageInspection:
    """Inspect one local image and normalize common identity fields."""

    reference = _text(reference, "image reference")
    tool = _tool(tool, _IMAGE_TOOLS)
    if tool == "buildah":
        argv = [tool, "inspect", reference]
    else:
        argv = [tool, "image", "inspect", reference]
    result = _run(argv, cwd=cwd, environment=environment)
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise PackagingError("invalid image inspection output") from error
    if isinstance(payload, list):
        _require(
            len(payload) == 1 and isinstance(payload[0], dict),
            "invalid image inspection output",
        )
        data = payload[0]
    else:
        _require(isinstance(payload, dict), "invalid image inspection output")
        data = payload
    image_id = data.get("Id", data.get("ID"))
    _require(
        image_id is None or isinstance(image_id, str),
        "invalid image inspection output",
    )
    raw_digests = data.get("RepoDigests", ())
    if raw_digests is None:
        raw_digests = ()
    _require(
        isinstance(raw_digests, (list, tuple))
        and all(isinstance(item, str) for item in raw_digests),
        "invalid image inspection output",
    )
    return ImageInspection(
        reference=reference,
        image_id=image_id,
        repo_digests=tuple(raw_digests),
        metadata=dict(data),
    )


def tag_image(
    source: str,
    target: str,
    *,
    environment: Mapping[str, str] | None = None,
    tool: str = "docker",
    cwd: Path | None = None,
) -> ImageReference:
    """Apply one caller-owned image tag."""

    source = _text(source, "source image")
    target = _text(target, "target image")
    tool = _tool(tool, _IMAGE_TOOLS)
    _run([tool, "tag", source, target], cwd=cwd, environment=environment)
    return ImageReference(reference=target)


def push_image(
    reference: str,
    *,
    environment: Mapping[str, str] | None = None,
    tool: str = "docker",
    cwd: Path | None = None,
) -> ImageReference:
    """Push one caller-owned image reference without adding read-back policy."""

    reference = _text(reference, "image reference")
    tool = _tool(tool, _IMAGE_TOOLS)
    _run([tool, "push", reference], cwd=cwd, environment=environment)
    return ImageReference(reference=reference)


def _chart(chart: Path) -> Path:
    _require(
        chart.is_dir() and not chart.is_symlink(),
        "invalid chart path",
    )
    chart_yaml = chart / "Chart.yaml"
    _require(
        chart_yaml.is_file() and not chart_yaml.is_symlink(),
        "invalid chart path",
    )
    return chart


def _values(values: Sequence[Path]) -> list[str]:
    argv: list[str] = []
    for path in values:
        _require(
            path.is_file() and not path.is_symlink(),
            "invalid values path",
        )
        argv.extend(["--values", str(path)])
    return argv


def helm_dependency_build(
    chart: Path,
    *,
    environment: Mapping[str, str] | None = None,
    tool: str = "helm",
) -> None:
    """Run ``helm dependency build`` for a caller-owned chart."""

    tool = _tool(tool, _HELM_TOOLS)
    chart = _chart(chart)
    _run(
        [tool, "dependency", "build", str(chart)],
        cwd=chart,
        environment=environment,
    )


def helm_lint(
    chart: Path,
    *,
    values: Sequence[Path] = (),
    strict: bool = True,
    environment: Mapping[str, str] | None = None,
    tool: str = "helm",
) -> None:
    """Lint a caller-owned chart with optional values files."""

    tool = _tool(tool, _HELM_TOOLS)
    chart = _chart(chart)
    argv = [tool, "lint"]
    if strict:
        argv.append("--strict")
    argv.append(str(chart))
    argv.extend(_values(values))
    _run(argv, cwd=chart, environment=environment)


def helm_template(
    chart: Path,
    *,
    values: Sequence[Path] = (),
    release_name: str = "release",
    namespace: str | None = None,
    environment: Mapping[str, str] | None = None,
    tool: str = "helm",
) -> str:
    """Render a caller-owned chart and return the rendered manifest."""

    tool = _tool(tool, _HELM_TOOLS)
    release_name = _text(release_name, "release name")
    chart = _chart(chart)
    argv = [tool, "template", release_name, str(chart)]
    argv.extend(_values(values))
    if namespace is not None:
        argv.extend(["--namespace", _text(namespace, "namespace")])
    return _run(argv, cwd=chart, environment=environment).stdout


def helm_package(
    chart: Path,
    destination: Path,
    *,
    version: str | None = None,
    app_version: str | None = None,
    environment: Mapping[str, str] | None = None,
    tool: str = "helm",
) -> HelmPackage:
    """Package a caller-owned chart and return the created archive path."""

    chart = _chart(chart)
    _require(not destination.is_symlink(), "invalid package destination")
    destination.mkdir(parents=True, exist_ok=True)
    _require(destination.is_dir(), "invalid package destination")
    tool = _tool(tool, _HELM_TOOLS)
    before = {path.resolve() for path in destination.glob("*.tgz")}
    argv = [tool, "package", str(chart), "--destination", str(destination)]
    if version is not None:
        argv.extend(["--version", _text(version, "chart version")])
    if app_version is not None:
        argv.extend(["--app-version", _text(app_version, "app version")])
    result = _run(argv, cwd=chart, environment=environment)
    marker = "saved it to:"
    archive: Path | None = None
    for line in result.stdout.splitlines():
        if marker in line.casefold():
            candidate = Path(line.split(":", 1)[1].strip())
            if not candidate.is_absolute():
                candidate = destination / candidate
            archive = candidate
            break
    if archive is None or not archive.is_file() or archive.is_symlink():
        created = [
            path
            for path in destination.glob("*.tgz")
            if path.resolve() not in before
            and path.is_file()
            and not path.is_symlink()
        ]
        _require(len(created) == 1, "helm package output missing")
        archive = created[0]
    return HelmPackage(archive=archive.resolve())


def helm_push(
    package: Path | HelmPackage,
    repository: str,
    *,
    environment: Mapping[str, str] | None = None,
    tool: str = "helm",
) -> HelmPublishResult:
    """Push one chart archive to a caller-owned OCI repository."""

    archive = package.archive if isinstance(package, HelmPackage) else package
    _require(
        archive.is_file() and not archive.is_symlink(),
        "invalid chart package",
    )
    repository = _text(repository, "helm repository")
    _require(
        repository.startswith("oci://"),
        "helm repository must use oci://",
    )
    tool = _tool(tool, _HELM_TOOLS)
    _run(
        [tool, "push", str(archive), repository],
        cwd=archive.parent,
        environment=environment,
    )
    return HelmPublishResult(
        repository=repository,
        archive=archive.resolve(),
    )


def _remove_no_follow(path: Path) -> None:
    if not path.exists() and not path.is_symlink():
        return
    if path.is_symlink() or path.is_file():
        path.unlink()
        return
    _require(path.is_dir(), "invalid cleanup path")
    for child in path.iterdir():
        _remove_no_follow(child)
    path.rmdir()


def cleanup_packaging_state(paths: Sequence[Path]) -> None:
    """Remove caller-designated registry/layout/package state without following symlinks."""

    for path in paths:
        _remove_no_follow(path)


OCI_NATIVE_PLATFORM_ENV = "CIW_OCI_NATIVE_PLATFORM"
OCI_EMULATED_PLATFORMS_ENV = "CIW_OCI_EMULATED_PLATFORMS"

_PLATFORM_ALIASES = {
    "linux/amd64": "linux/amd64",
    "linux/x86_64": "linux/amd64",
    "linux/arm64": "linux/arm64/v8",
    "linux/arm64/v8": "linux/arm64/v8",
    "linux/aarch64": "linux/arm64/v8",
}
_PLATFORM_ORDER = ("linux/amd64", "linux/arm64/v8")
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_INDEX_MEDIA_TYPES = frozenset(
    {
        "application/vnd.oci.image.index.v1+json",
        "application/vnd.docker.distribution.manifest.list.v2+json",
    }
)
_MANIFEST_MEDIA_TYPES = frozenset(
    {
        "application/vnd.oci.image.manifest.v1+json",
        "application/vnd.docker.distribution.manifest.v2+json",
    }
)


@dataclass(frozen=True)
class PlatformExecution:
    """One requested platform and the runner execution strategy that can run it."""

    platform: str
    strategy: str
    native_platform: str


@dataclass(frozen=True)
class PlatformBuildResult:
    """Structured result of one platform-specific image build."""

    platform: str
    strategy: str
    reference: str


@dataclass(frozen=True)
class OCIManifestDescriptor:
    """Normalized descriptor in an OCI index or Docker manifest list."""

    platform: str
    digest: str
    size: int
    media_type: str


@dataclass(frozen=True)
class OCIIndexInspection:
    """Structured local multi-platform manifest/index inspection."""

    reference: str
    platforms: tuple[str, ...]
    descriptors: tuple[OCIManifestDescriptor, ...]
    metadata: Mapping[str, object]


@dataclass(frozen=True)
class MultiPlatformBuildResult:
    """Per-platform builds together with the assembled final index."""

    builds: tuple[PlatformBuildResult, ...]
    index: OCIIndexInspection


def _normalize_platform(value: str) -> str:
    raw = _text(value, "platform").casefold()
    canonical = _PLATFORM_ALIASES.get(raw)
    _require(canonical is not None, f"unsupported platform: {raw}")
    assert canonical is not None
    return canonical


def normalize_platforms(platforms: Sequence[str]) -> tuple[str, ...]:
    """Normalize a caller platform set into one deterministic canonical tuple."""

    _require(
        not isinstance(platforms, (str, bytes)) and bool(platforms),
        "invalid platform set",
    )
    normalized = tuple(_normalize_platform(item) for item in platforms)
    _require(len(set(normalized)) == len(normalized), "duplicate platform")
    requested = set(normalized)
    return tuple(platform for platform in _PLATFORM_ORDER if platform in requested)


def _emulated_platforms(environment: Mapping[str, str]) -> tuple[str, ...]:
    raw = environment.get(OCI_EMULATED_PLATFORMS_ENV, "").strip()
    if not raw:
        return ()
    return normalize_platforms(tuple(part.strip() for part in raw.split(",")))


def resolve_platform_execution(
    platform: str,
    *,
    environment: Mapping[str, str] | None = None,
) -> PlatformExecution:
    """Resolve native versus explicit runner-provided emulation for one platform."""

    values = _environment(environment)
    target = _normalize_platform(platform)
    native_raw = values.get(OCI_NATIVE_PLATFORM_ENV, "").strip()
    _require(bool(native_raw), f"missing runner capability: {OCI_NATIVE_PLATFORM_ENV}")
    native = _normalize_platform(native_raw)
    emulated = _emulated_platforms(values)
    _require(native not in emulated, "native platform cannot be declared emulated")
    if target == native:
        return PlatformExecution(target, "native", native)
    _require(
        target in emulated,
        f"unsupported foreign platform execution: {target}",
    )
    return PlatformExecution(target, "emulated", native)


def plan_platform_executions(
    platforms: Sequence[str],
    *,
    environment: Mapping[str, str] | None = None,
) -> tuple[PlatformExecution, ...]:
    """Preflight every requested platform before any Dockerfile build starts."""

    normalized = normalize_platforms(platforms)
    values = _environment(environment)
    return tuple(
        resolve_platform_execution(platform, environment=values)
        for platform in normalized
    )


def _platform_tags(platform_tags: Mapping[str, str]) -> dict[str, str]:
    _require(isinstance(platform_tags, Mapping), "invalid platform image tags")
    result: dict[str, str] = {}
    for raw_platform, raw_tag in platform_tags.items():
        platform = _normalize_platform(raw_platform)
        _require(platform not in result, "duplicate platform image tag")
        result[platform] = _text(raw_tag, "platform image tag")
    return result


def _platform_build_argv(
    context: Path,
    dockerfile: Path,
    tag: str,
    execution: PlatformExecution,
    *,
    build_args: Mapping[str, str] | None,
    tool: str,
) -> list[str]:
    _require(context.is_dir() and not context.is_symlink(), "invalid build context")
    _require(dockerfile.is_file() and not dockerfile.is_symlink(), "invalid dockerfile")
    tag = _text(tag, "image tag")
    tool = _tool(tool, _IMAGE_TOOLS)
    command = "bud" if tool == "buildah" else "build"
    argv = [
        tool,
        command,
        "--platform",
        execution.platform,
        "--file",
        str(dockerfile),
        "--tag",
        tag,
    ]
    for key, value in sorted((build_args or {}).items()):
        _require(
            _BUILD_ARG_NAME.fullmatch(_text(key, "build argument name")) is not None,
            "invalid build argument name",
        )
        argv.extend(["--build-arg", f"{key}={_text(value, 'build argument value')}"])
    argv.append(str(context))
    return argv


def _execute_platform_build(
    context: Path,
    dockerfile: Path,
    tag: str,
    execution: PlatformExecution,
    *,
    build_args: Mapping[str, str] | None,
    environment: Mapping[str, str] | None,
    tool: str,
) -> PlatformBuildResult:
    argv = _platform_build_argv(
        context,
        dockerfile,
        tag,
        execution,
        build_args=build_args,
        tool=tool,
    )
    _run(argv, cwd=context, environment=environment)
    return PlatformBuildResult(
        platform=execution.platform,
        strategy=execution.strategy,
        reference=_text(tag, "image tag"),
    )


def build_platform_image(
    context: Path,
    dockerfile: Path,
    tag: str,
    platform: str,
    *,
    build_args: Mapping[str, str] | None = None,
    environment: Mapping[str, str] | None = None,
    tool: str = "docker",
) -> PlatformBuildResult:
    """Build one platform only after the runner proves compatible execution capacity."""

    execution = resolve_platform_execution(platform, environment=environment)
    return _execute_platform_build(
        context,
        dockerfile,
        tag,
        execution,
        build_args=build_args,
        environment=environment,
        tool=tool,
    )


def build_platform_images(
    context: Path,
    dockerfile: Path,
    platforms: Sequence[str],
    platform_tags: Mapping[str, str],
    *,
    build_args: Mapping[str, str] | None = None,
    environment: Mapping[str, str] | None = None,
    tool: str = "docker",
) -> tuple[PlatformBuildResult, ...]:
    """Preflight the full platform set, then build every platform in canonical order."""

    executions = plan_platform_executions(platforms, environment=environment)
    tags = _platform_tags(platform_tags)
    expected = {execution.platform for execution in executions}
    _require(set(tags) == expected, "platform image tags do not match platform set")
    return tuple(
        _execute_platform_build(
            context,
            dockerfile,
            tags[execution.platform],
            execution,
            build_args=build_args,
            environment=environment,
            tool=tool,
        )
        for execution in executions
    )


def _descriptor_platform(value: Mapping[str, object]) -> str:
    operating_system = value.get("os")
    architecture = value.get("architecture")
    variant = value.get("variant")
    _require(
        isinstance(operating_system, str) and isinstance(architecture, str),
        "invalid manifest platform",
    )
    if variant is not None:
        _require(isinstance(variant, str), "invalid manifest platform")
    raw = f"{operating_system}/{architecture}"
    if variant:
        raw += f"/{variant}"
    return _normalize_platform(raw)


def inspect_multi_platform_index(
    reference: str,
    expected_platforms: Sequence[str],
    *,
    environment: Mapping[str, str] | None = None,
    tool: str = "buildah",
    cwd: Path | None = None,
) -> OCIIndexInspection:
    """Inspect and normalize one local OCI index or Docker manifest list."""

    reference = _text(reference, "manifest reference")
    tool = _tool(tool, _IMAGE_TOOLS)
    expected = normalize_platforms(expected_platforms)
    result = _run(
        [tool, "manifest", "inspect", reference],
        cwd=cwd,
        environment=environment,
    )
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise PackagingError("invalid manifest inspection output") from error
    _require(isinstance(payload, dict), "invalid manifest inspection output")
    _require(payload.get("schemaVersion") == 2, "invalid manifest schema")
    media_type = payload.get("mediaType")
    if media_type is not None:
        _require(media_type in _INDEX_MEDIA_TYPES, "invalid manifest index media type")
    raw_manifests = payload.get("manifests")
    _require(isinstance(raw_manifests, list), "invalid manifest inspection output")
    descriptors: list[OCIManifestDescriptor] = []
    seen: set[str] = set()
    for raw_descriptor in raw_manifests:
        _require(isinstance(raw_descriptor, dict), "invalid manifest descriptor")
        platform_value = raw_descriptor.get("platform")
        _require(isinstance(platform_value, dict), "invalid manifest platform")
        platform = _descriptor_platform(platform_value)
        _require(platform not in seen, "duplicate manifest platform")
        seen.add(platform)
        digest = raw_descriptor.get("digest")
        size = raw_descriptor.get("size")
        descriptor_media_type = raw_descriptor.get("mediaType")
        _require(
            isinstance(digest, str) and _DIGEST.fullmatch(digest) is not None,
            "invalid manifest digest",
        )
        _require(type(size) is int and size > 0, "invalid manifest size")
        _require(
            descriptor_media_type in _MANIFEST_MEDIA_TYPES,
            "invalid manifest media type",
        )
        descriptors.append(
            OCIManifestDescriptor(
                platform=platform,
                digest=digest,
                size=size,
                media_type=str(descriptor_media_type),
            )
        )
    _require(seen == set(expected), "manifest platform set mismatch")
    descriptor_by_platform = {item.platform: item for item in descriptors}
    ordered = tuple(descriptor_by_platform[platform] for platform in expected)
    return OCIIndexInspection(
        reference=reference,
        platforms=expected,
        descriptors=ordered,
        metadata=dict(payload),
    )


def assemble_multi_platform_index(
    reference: str,
    builds: Sequence[PlatformBuildResult],
    *,
    environment: Mapping[str, str] | None = None,
    tool: str = "buildah",
    cwd: Path | None = None,
) -> OCIIndexInspection:
    """Assemble successful platform images and verify the final local index shape."""

    _require(
        not isinstance(builds, (str, bytes)) and len(builds) >= 2,
        "multi-platform index requires at least two builds",
    )
    platforms = normalize_platforms(tuple(build.platform for build in builds))
    _require(len(platforms) == len(builds), "duplicate platform build")
    by_platform: dict[str, PlatformBuildResult] = {}
    for build in builds:
        _require(isinstance(build, PlatformBuildResult), "invalid platform build result")
        _require(build.strategy in {"native", "emulated"}, "invalid execution strategy")
        platform = _normalize_platform(build.platform)
        _require(platform not in by_platform, "duplicate platform build")
        _text(build.reference, "platform image reference")
        by_platform[platform] = build
    ordered = tuple(by_platform[platform] for platform in platforms)
    reference = _text(reference, "manifest reference")
    tool = _tool(tool, _IMAGE_TOOLS)
    if tool == "docker":
        _run(
            [tool, "manifest", "create", reference, *(item.reference for item in ordered)],
            cwd=cwd,
            environment=environment,
        )
    else:
        _run(
            [tool, "manifest", "create", reference],
            cwd=cwd,
            environment=environment,
        )
        for build in ordered:
            _run(
                [tool, "manifest", "add", reference, build.reference],
                cwd=cwd,
                environment=environment,
            )
    return inspect_multi_platform_index(
        reference,
        platforms,
        environment=environment,
        tool=tool,
        cwd=cwd,
    )


def build_multi_platform_image(
    context: Path,
    dockerfile: Path,
    index_reference: str,
    platforms: Sequence[str],
    platform_tags: Mapping[str, str],
    *,
    build_args: Mapping[str, str] | None = None,
    environment: Mapping[str, str] | None = None,
    tool: str = "buildah",
    cwd: Path | None = None,
) -> MultiPlatformBuildResult:
    """Build every compatible platform and return the verified assembled index."""

    builds = build_platform_images(
        context,
        dockerfile,
        platforms,
        platform_tags,
        build_args=build_args,
        environment=environment,
        tool=tool,
    )
    index = assemble_multi_platform_index(
        index_reference,
        builds,
        environment=environment,
        tool=tool,
        cwd=cwd,
    )
    return MultiPlatformBuildResult(builds=builds, index=index)


def cleanup_multi_platform_state(paths: Sequence[Path]) -> None:
    """Remove caller-designated per-platform OCI layouts/build state no-follow."""

    cleanup_packaging_state(paths)
