#!/usr/bin/env python3
"""Build the native image and validate/package the caller-owned Helm chart."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

from ci_workflows.packaging_primitives import (
    build_image,
    helm_lint,
    helm_package,
    helm_template,
    inspect_image,
)


def _output(name: str, value: str) -> None:
    target = Path(os.environ["GITHUB_OUTPUT"])
    if "\n" in value or "\r" in value:
        raise SystemExit(f"invalid output {name}")
    with target.open("a", encoding="utf-8") as stream:
        stream.write(f"{name}={value}\n")


def main() -> int:
    source = Path(os.environ.get("SOURCE_ROOT", "source")).resolve()
    context = (source / os.environ["BUILD_CONTEXT"]).resolve()
    dockerfile = (source / os.environ["DOCKERFILE_PATH"]).resolve()
    chart = (source / os.environ["CHART_PATH"]).resolve()
    package_root = Path(os.environ["PACKAGE_ROOT"]).resolve()
    image_reference = (
        f"{os.environ['REGISTRY']}/{os.environ['REGISTRY_NAMESPACE']}/"
        f"{os.environ['IMAGE_NAME']}:{os.environ['VERSION']}"
    )
    environment = dict(os.environ)

    build_image(
        context,
        dockerfile,
        image_reference,
        environment=environment,
        tool="buildah",
    )
    inspect_image(
        image_reference,
        environment=environment,
        tool="buildah",
        cwd=source,
    )
    helm_lint(chart, environment=environment)
    rendered = helm_template(
        chart,
        release_name=os.environ["CHART_NAME"],
        environment=environment,
    )
    if not rendered.strip():
        raise SystemExit("Helm render is empty")
    package = helm_package(
        chart,
        package_root,
        version=os.environ["VERSION"],
        app_version=os.environ["VERSION"],
        environment=environment,
    )
    package_digest = hashlib.sha256(package.archive.read_bytes()).hexdigest()

    _output("image_reference", image_reference)
    _output("package_path", str(package.archive))
    _output("chart_package_sha256", package_digest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
