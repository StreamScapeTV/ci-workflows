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
    assert len(from_lines) == 4
    assert all("@sha256:" in line for line in from_lines)
    assert "eclipse-temurin:25-jdk-noble@sha256:" in from_lines[0]
    assert "node:24.18.0-bookworm-slim@sha256:" in from_lines[1]
    assert "python:3.12.13-slim-bookworm@sha256:" in from_lines[2]
    assert "actions/actions-runner:2.336.0@sha256:" in from_lines[3]


def test_mobile_runner_build_is_networkless() -> None:
    source = DOCKERFILE.read_text(encoding="utf-8").lower()
    for token in (
        "apt-get",
        "apt update",
        "curl http",
        "wget http",
        "git clone",
        "sdkmanager --install",
        "sdkmanager --licenses",
        "flutter precache",
    ):
        assert token not in source
    for destination in (
        "flutter-3.44.8-linux-amd64.tar.xz",
        "android-command-line-tools-15859902-linux.zip",
        "android-platform-tools-36.0.0-linux.zip",
        "android-platform-36-r02.zip",
        "android-platform-37.0-r01.zip",
        "android-build-tools-36.0.0-linux.zip",
        "android-build-tools-37.0.0-linux.zip",
        "android-ndk-r28c-linux.zip",
    ):
        assert f".ciw-build-inputs/{destination}" in source


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

    toolchain = json.loads(TOOLCHAIN.read_text(encoding="utf-8"))
    assert toolchain["toolchain"]["java"] == "25.0.3+9"
    assert toolchain["toolchain"]["node"] == "24.18.0"
    assert toolchain["toolchain"]["python"] == "3.12.13"
    assert toolchain["toolchain"]["flutter"] == "3.44.8"
    assert toolchain["toolchain"]["dart"] == "3.12.2"
    assert toolchain["toolchain"]["android_platform_tools"] == "36.0.0"
    assert toolchain["toolchain"]["android_ndk"] == "28.2.13676358"
    assert len(toolchain["checksums"]) == 8
    for digest in toolchain["checksums"].values():
        assert len(digest) == 64
        int(digest, 16)


def test_mobile_runner_materializes_required_android_components() -> None:
    source = DOCKERFILE.read_text(encoding="utf-8")
    for path in (
        "/opt/android-sdk/cmdline-tools/latest",
        "/opt/android-sdk/platform-tools",
        "/opt/android-sdk/platforms",
        "/opt/android-sdk/build-tools",
        "/opt/android-sdk/ndk",
    ):
        assert path in source
    assert "android-${ANDROID_PLATFORM_PACKAGE_VERSION}" in source
    assert "android-${ANDROID_PLATFORM_VERSION}" in source


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


def test_mobile_runner_input_lock_matches_source_stages_and_assets() -> None:
    lock = json.loads(INPUT_LOCK.read_text(encoding="utf-8"))
    assert lock["product_id"] == "runner-mobile"
    assert lock["target_id"] == "linux-amd64"
    assert lock["input_policy_id"] == "runner-image-public-v1"
    assert [base["stage_id"] for base in lock["bases"]] == [
        "java-runtime",
        "node-runtime",
        "python-runtime",
        "actions-runner",
    ]
    assert [base["from_ordinal"] for base in lock["bases"]] == [1, 2, 3, 4]
    for base in lock["bases"]:
        assert "@sha256:" in base["declared_reference"]
        identity = base["platform_identities"][0]
        assert identity["platform"] == "linux/amd64"
        assert identity["manifest_digest"].startswith("sha256:")
        assert identity["config_digest"].startswith("sha256:")

    inputs = lock["external_inputs"]
    assert len(inputs) == 8
    expected_ids = {
        "flutter-linux-amd64",
        "android-command-line-tools-linux",
        "android-platform-tools-linux",
        "android-platform-36",
        "android-platform-37",
        "android-build-tools-36-linux",
        "android-build-tools-37-linux",
        "android-ndk-r28c-linux",
    }
    assert {item["input_id"] for item in inputs} == expected_ids
    for item in inputs:
        assert item["url"].startswith("https://")
        assert len(item["sha256"]) == 64
        int(item["sha256"], 16)
        assert item["maximum_bytes"] > 0
        assert item["destination"].startswith(".ciw-build-inputs/")


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
