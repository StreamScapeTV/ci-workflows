from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = ROOT / "runner-images/buildah/Dockerfile"
PRODUCT = ROOT / "runner-images/buildah/product.json"
SMOKE = ROOT / "runner-images/buildah/smoke.sh"
TOOLCHAIN = ROOT / "runner-images/buildah/toolchain.lock.json"


def _toolchain() -> dict[str, object]:
    return json.loads(TOOLCHAIN.read_text(encoding="utf-8"))


def test_buildah_runner_uses_reviewed_actions_runner_release() -> None:
    source = DOCKERFILE.read_text(encoding="utf-8")
    lock = _toolchain()
    runner_version = lock["actions_runner"]["version"]
    from_lines = [line for line in source.splitlines() if line.startswith("FROM ")]

    assert len(from_lines) == 1
    assert from_lines[0].startswith(
        f"FROM ghcr.io/actions/actions-runner:{runner_version}"
    )
    assert from_lines[0].endswith(" AS actions-runner")
    assert set(lock["actions_runner"]) == {"version"}


def test_buildah_runner_uses_checked_in_primary_tool_versions() -> None:
    source = DOCKERFILE.read_text(encoding="utf-8")
    packages = _toolchain()["packages"]
    argument_names = {
        "buildah": "BUILDAH_PACKAGE_VERSION",
        "skopeo": "SKOPEO_PACKAGE_VERSION",
        "podman": "PODMAN_PACKAGE_VERSION",
        "podman-compose": "PODMAN_COMPOSE_PACKAGE_VERSION",
    }

    for package, argument_name in argument_names.items():
        version = packages[package]
        assert f"ARG {argument_name}={version}" in source
        assert f'{package}="${{{argument_name}}}"' in source


def test_buildah_runner_is_daemonless_with_private_storage() -> None:
    source = DOCKERFILE.read_text(encoding="utf-8")
    for path in (
        "/usr/bin/docker",
        "/usr/bin/dockerd",
        "/usr/bin/containerd",
        "/usr/bin/containerd-shim-runc-v2",
        "/usr/bin/ctr",
        "/usr/bin/docker-init",
        "/usr/bin/docker-proxy",
    ):
        assert path in source
    assert "! command -v docker" in source
    assert "! command -v dockerd" in source
    assert "! command -v containerd" in source
    assert "runner:100000:65536" in source
    assert 'driver = "vfs"' in source
    assert 'runtime = "crun"' in source


def test_buildah_runner_product_and_smoke_contract() -> None:
    product = json.loads(PRODUCT.read_text(encoding="utf-8"))
    packages = _toolchain()["packages"]
    smoke = SMOKE.read_text(encoding="utf-8")

    assert product["product_id"] == "runner-buildah"
    assert product["image_repository"].endswith("/github-actions-runner-buildah")
    assert product["platform"] == "linux/amd64"
    assert product["dockerfile"] == "runner-images/buildah/Dockerfile"
    assert product["smoke"] == "runner-images/buildah/smoke.sh"
    assert product["release_authority"] == "ci-workflows-git-tag"

    assert 'test "$(id -un)" = runner' in smoke
    assert "/home/runner/run.sh" in smoke
    for package, command in (
        ("buildah", "buildah --version"),
        ("skopeo", "skopeo --version"),
        ("podman", "podman --version"),
        ("podman-compose", "podman-compose --version"),
    ):
        version = packages[package].split("+")[0].split("-")[0]
        assert f"{command} | grep -F '{version}'" in smoke

    for token in (
        "! command -v docker",
        "! command -v dockerd",
        "! command -v containerd",
        "test -w /home/runner/.local/share/containers/storage",
        "test -w /home/runner/.local/share/containers/runroot",
    ):
        assert token in smoke
