from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = ROOT / "runner-images/mobile/Dockerfile"
INPUT_LOCK = ROOT / ".ciw/oci-build-inputs/runner-mobile-linux-amd64.json"
PRODUCT = ROOT / "runner-images/mobile/product.json"
SMOKE = ROOT / "runner-images/mobile/smoke.sh"
TOOLCHAIN = ROOT / "runner-images/mobile/toolchain.lock.json"


def test_mobile_runner_uses_only_pinned_upstream_images() -> None:
    source = DOCKERFILE.read_text(encoding="utf-8")
    from_lines = [line for line in source.splitlines() if line.startswith("FROM ")]
    assert len(from_lines) == 3
    assert all("@sha256:" in line for line in from_lines)
    assert "eclipse-temurin:25-jdk-noble@sha256:" in from_lines[0]
    assert "node:24.18.0-bookworm-slim@sha256:" in from_lines[1]
    assert "actions/actions-runner:2.336.0@sha256:" in from_lines[2]


def test_mobile_runner_locks_compatible_toolchain() -> None:
    source = DOCKERFILE.read_text(encoding="utf-8")
    for value in (
        "ARG FLUTTER_VERSION=3.44.8",
        "ARG DART_VERSION=3.12.2",
        "ARG ANDROID_CMDLINE_TOOLS_VERSION=15859902",
        "ARG ANDROID_COMPAT_PLATFORM_VERSION=36",
        "ARG ANDROID_PLATFORM_VERSION=37",
        "ARG ANDROID_PLATFORM_PACKAGE_VERSION=37.0",
        "ARG ANDROID_COMPAT_BUILD_TOOLS_VERSION=36.0.0",
        "ARG ANDROID_BUILD_TOOLS_VERSION=37.0.0",
        "ARG ANDROID_NDK_VERSION=28.2.13676358",
    ):
        assert value in source
    assert "672089e001571a9fbb209a495c583580c0c6c73ef98999264ba07fa93ace332d" in source
    assert "4e4c464f145a7512b57d088ac6c278c03c9eea610886b35a5e0804e74eedf583" in source

    toolchain = json.loads(TOOLCHAIN.read_text(encoding="utf-8"))
    assert toolchain["toolchain"]["java"] == "25.0.3+9"
    assert toolchain["toolchain"]["node"] == "24.18.0"
    assert toolchain["toolchain"]["flutter"] == "3.44.8"
    assert toolchain["toolchain"]["dart"] == "3.12.2"
    assert toolchain["toolchain"]["android_ndk"] == "28.2.13676358"


def test_mobile_runner_installs_required_android_components() -> None:
    source = DOCKERFILE.read_text(encoding="utf-8")
    for package in (
        '"platform-tools"',
        '"platforms;android-${ANDROID_COMPAT_PLATFORM_VERSION}"',
        '"platforms;android-${ANDROID_PLATFORM_PACKAGE_VERSION}"',
        '"build-tools;${ANDROID_COMPAT_BUILD_TOOLS_VERSION}"',
        '"build-tools;${ANDROID_BUILD_TOOLS_VERSION}"',
        '"ndk;${ANDROID_NDK_VERSION}"',
    ):
        assert package in source
    assert "flutter precache --android --web --linux" in source


def test_mobile_runner_strips_container_engines() -> None:
    source = DOCKERFILE.read_text(encoding="utf-8")
    for path in (
        "/usr/bin/docker",
        "/usr/bin/dockerd",
        "/usr/bin/containerd",
        "/usr/bin/containerd-shim-runc-v2",
        "/usr/bin/ctr",
        "/usr/bin/docker-init",
        "/usr/bin/docker-proxy",
        "/usr/bin/runc",
    ):
        assert path in source
    for command in ("docker", "dockerd", "buildah", "podman", "skopeo"):
        assert f"! command -v {command}" in source


def test_mobile_runner_input_lock_matches_source_stages() -> None:
    lock = json.loads(INPUT_LOCK.read_text(encoding="utf-8"))
    assert lock["product_id"] == "runner-mobile"
    assert lock["target_id"] == "linux-amd64"
    assert [base["stage_id"] for base in lock["bases"]] == [
        "java-runtime",
        "node-runtime",
        "actions-runner",
    ]
    for base in lock["bases"]:
        assert "@sha256:" in base["declared_reference"]
        identity = base["platform_identities"][0]
        assert identity["platform"] == "linux/amd64"
        assert identity["manifest_digest"].startswith("sha256:")
        assert identity["config_digest"].startswith("sha256:")


def test_mobile_runner_product_and_smoke_contract() -> None:
    product = json.loads(PRODUCT.read_text(encoding="utf-8"))
    assert product["product_id"] == "runner-mobile"
    assert product["image_repository"].endswith("/github-actions-runner-mobile")
    assert product["platform"] == "linux/amd64"
    assert product["release_authority"] == "ci-workflows-git-tag"

    smoke = SMOKE.read_text(encoding="utf-8")
    assert 'test "$(id -un)" = runner' in smoke
    assert "/home/runner/run.sh" in smoke
    for token in (
        "java -version",
        "javac -version",
        "flutter --version",
        "dart --version",
        "node --version",
        "npm --version",
        "corepack --version",
        "sdkmanager --version",
        "adb version",
    ):
        assert token in smoke
