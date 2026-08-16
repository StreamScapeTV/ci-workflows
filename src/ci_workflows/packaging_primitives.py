"""Product-neutral OCI/Docker/Helm packaging process primitives."""

from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_BUILD_ARG_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]*$")
_IMAGE_TOOLS = frozenset({"buildah", "docker", "podman"})
_REGISTRY_TOOLS = frozenset({"buildah", "docker", "helm", "podman"})


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
    _require(
        _ENV_NAME.fullmatch(_text(name, "secret environment name")) is not None,
        "invalid secret environment name",
    )
    value = environment.get(name, "")
    _require(bool(value), f"missing environment secret: {name}")
    return value


def registry_authenticate(
    registry: str,
    *,
    username_env: str,
    password_env: str,
    environment: Mapping[str, str],
    tool: str = "docker",
    cwd: Path | None = None,
) -> None:
    """Authenticate a supported registry client using named environment secrets."""

    registry = _text(registry, "registry")
    tool = _tool(tool, _REGISTRY_TOOLS)
    username = _secret(environment, username_env)
    password = _secret(environment, password_env)
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
        stdin=f"{password}\n",
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

    tool = _text(tool, "helm tool")
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

    tool = _text(tool, "helm tool")
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

    tool = _text(tool, "helm tool")
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
    tool = _text(tool, "helm tool")
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
    tool = _text(tool, "helm tool")
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
