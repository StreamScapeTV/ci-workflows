from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = ROOT / "runner-images/buildah/Dockerfile"
INPUT_LOCK = ROOT / ".ciw/oci-build-inputs/runner-buildah-linux-amd64.json"
PRODUCT = ROOT / "runner-images/buildah/product.json"
SMOKE = ROOT / "runner-images/buildah/smoke.sh"
TOOLCHAIN = ROOT / "runner-images/buildah/toolchain.lock.json"


def test_buildah_runner_uses_one_pinned_official_runner_base() -> None:
    source = DOCKERFILE.read_text(encoding="utf-8")
    from_lines = [line for line in source.splitlines() if line.startswith("FROM ")]
    assert len(from_lines) == 1
    assert from_lines[0].startswith("FROM ghcr.io/actions/actions-runner:2.336.0@sha256:")
    assert from_lines[0].endswith(" AS actions-runner")


def test_buildah_runner_pins_primary_tool_versions() -> None:
    source = DOCKERFILE.read_text(encoding="utf-8")
    expected = {
        "BUILDAH_PACKAGE_VERSION": "1.33.7+ds1-1ubuntu0.24.04.3",
        "SKOPEO_PACKAGE_VERSION": "1.13.3+ds1-2ubuntu0.24.04.3",
        "PODMAN_PACKAGE_VERSION": "4.9.3+ds1-1ubuntu0.2",
        "PODMAN_COMPOSE_PACKAGE_VERSION": "1.0.6-1",
    }
    for name, version in expected.items():
        assert f"ARG {name}={version}" in source
        assert f'"${{{name}}}"' in source

    lock = json.loads(TOOLCHAIN.read_text(encoding="utf-8"))
    assert lock["packages"] == {
        "buildah": expected["BUILDAH_PACKAGE_VERSION"],
        "skopeo": expected["SKOPEO_PACKAGE_VERSION"],
        "podman": expected["PODMAN_PACKAGE_VERSION"],
        "podman-compose": expected["PODMAN_COMPOSE_PACKAGE_VERSION"],
    }


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


def test_buildah_runner_input_lock_matches_exact_base() -> None:
    lock = json.loads(INPUT_LOCK.read_text(encoding="utf-8"))
    assert lock["product_id"] == "runner-buildah"
    assert lock["target_id"] == "linux-amd64"
    assert lock["platforms"] == ["linux/amd64"]
    assert lock["external_inputs"] == []
    assert len(lock["bases"]) == 1
    base = lock["bases"][0]
    assert base["stage_id"] == "actions-runner"
    assert base["stage_marker"] == "final"
    assert "@sha256:" in base["declared_reference"]
    identity = base["platform_identities"][0]
    assert identity["platform"] == "linux/amd64"
    assert identity["manifest_digest"].startswith("sha256:")
    assert identity["config_digest"].startswith("sha256:")


def test_buildah_runner_product_and_smoke_contract() -> None:
    product = json.loads(PRODUCT.read_text(encoding="utf-8"))
    assert product["product_id"] == "runner-buildah"
    assert product["platform"] == "linux/amd64"
    assert product["release_authority"] == "ci-workflows-git-tag"

    smoke = SMOKE.read_text(encoding="utf-8")
    assert 'test "$(id -un)" = runner' in smoke
    assert "/home/runner/run.sh" in smoke
    for token in (
        "buildah --version",
        "skopeo --version",
        "podman --version",
        "podman-compose --version",
        "! command -v docker",
        "! command -v dockerd",
        "! command -v containerd",
    ):
        assert token in smoke
