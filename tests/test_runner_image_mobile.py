from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = ROOT / "runner-images/mobile/Dockerfile"
INPUT_LOCK = ROOT / ".ciw/oci-build-inputs/runner-mobile-linux-amd64.json"
PRODUCT = ROOT / "runner-images/mobile/product.json"
SMOKE = ROOT / "runner-images/mobile/smoke.sh"
TOOLCHAIN = ROOT / "runner-images/mobile/toolchain.lock.json"
ASSEMBLE = ROOT / "runner-images/mobile/assemble.py"
UNZIP_TOOL = ROOT / "runner-images/mobile/unzip.py"


def test_mobile_runner_uses_one_pinned_registry_profile() -> None:
    source = DOCKERFILE.read_text(encoding="utf-8")
    from_lines = [line for line in source.splitlines() if line.startswith("FROM ")]
    assert len(from_lines) == 4
    assert all("docker.io/library/" in line and "@sha256:" in line for line in from_lines)
    assert "gcc@sha256:" in from_lines[0]
    assert "eclipse-temurin@sha256:" in from_lines[1]
    assert "node@sha256:" in from_lines[2]
    assert "python@sha256:" in from_lines[3]
    assert "ghcr.io/" not in source
    assert "git.faruqi.dev" not in source
    assert source.rstrip().endswith("CMD []")


def test_mobile_runner_build_is_networkless_and_frontend_neutral() -> None:
    source = DOCKERFILE.read_text(encoding="utf-8").lower()
    for token in (
        "apt-get update",
        "apt-get install",
        "apt update",
        "apt install",
        "curl http",
        "wget http",
        "git clone",
        "sdkmanager --install",
        "sdkmanager --licenses",
        "flutter precache",
        "# syntax=",
        "<<'py'",
    ):
        assert token not in source
    assert ".ciw-build-inputs/actions-runner-linux-x64-2.336.0.tar.gz" in source
    assert "runner-mobile-assemble" in source
    assert "ldconfig -p" in source
    assert "/out/usr/lib/x86_64-linux-gnu/libatomic.so.1" in source
    assert "/node-root/usr/lib/x86_64-linux-gnu/libatomic.so.1" not in source
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
    assert "/usr/bin/apt-get" in source
    assert "for forbidden in docker dockerd containerd ctr runc buildah podman skopeo sudo" in source


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
    assert toolchain["policy"] == {
        "final_distribution": "debian-trixie",
        "release_authority": "ci-workflows-git-tag",
        "independent_product": True,
    }
    assert toolchain["toolchain"]["actions_runner"] == "2.336.0"
    assert toolchain["toolchain"]["java"] == "25.0.3+9"
    assert toolchain["toolchain"]["node"] == "24.19.0"
    assert toolchain["toolchain"]["python"] == "3.12.14"
    assert toolchain["toolchain"]["flutter"] == "3.44.8"
    assert toolchain["toolchain"]["dart"] == "3.12.2"
    assert toolchain["toolchain"]["android_platform_tools"] == "36.0.0"
    assert toolchain["toolchain"]["android_ndk"] == "28.2.13676358"
    assert len(toolchain["checksums"]) == 9
    assert toolchain["checksums"]["actions_runner_linux_x64"] == (
        "04cf0be1aff4c3ec3554466c39124ca250e3effd8873bb7e8d68535aa9505d5d"
    )
    for digest in toolchain["checksums"].values():
        assert len(digest) == 64
        int(digest, 16)
    assert set(toolchain["oci_stages"]) == {
        "build_tools",
        "java",
        "node",
        "final_python_debian",
    }
    assert toolchain["oci_stages"]["build_tools"]["reference"] == "docker.io/library/gcc:15-trixie"


def test_mobile_runner_materializes_required_android_components() -> None:
    source = DOCKERFILE.read_text(encoding="utf-8")
    for path in (
        "/opt/android-sdk/cmdline-tools/latest",
        "/opt/android-sdk/platform-tools",
        "/opt/android-sdk/platforms",
        "/opt/android-sdk/build-tools",
        "/opt/android-sdk/ndk",
    ):
        assert path in source or path in ASSEMBLE.read_text(encoding="utf-8")
    assert "android-${ANDROID_PLATFORM_PACKAGE_VERSION}" in source
    assert "android-${ANDROID_PLATFORM_VERSION}" in source

    assemble = ASSEMBLE.read_text(encoding="utf-8")
    assert "tarfile.open" in assemble
    assert "zipfile.ZipFile" in assemble
    assert '".." in relative.parts' in assemble
    assert "shutil.copytree" in assemble
    assert "subprocess" not in assemble
    assert "urllib" not in assemble


def test_mobile_runner_has_no_container_engine_or_package_manager_path() -> None:
    source = DOCKERFILE.read_text(encoding="utf-8")
    for command in (
        "docker",
        "dockerd",
        "containerd",
        "ctr",
        "runc",
        "buildah",
        "podman",
        "skopeo",
        "sudo",
    ):
        assert command in source
    for path in ("/usr/bin/apt-get", "/usr/bin/dpkg"):
        assert path in source
    assert "test ! -e /var/run/docker.sock" in source
    assert "test ! -e /run/docker.sock" in source


def test_mobile_runner_input_lock_matches_source_stages_and_assets() -> None:
    lock = json.loads(INPUT_LOCK.read_text(encoding="utf-8"))
    assert lock["product_id"] == "ciw-runner-images"
    assert lock["target_id"] == "runner-mobile"
    assert lock["input_policy_id"] == "runner-image-public-v1"
    assert [base["stage_id"] for base in lock["bases"]] == [
        "build-tools",
        "java-runtime",
        "node-runtime",
        "final",
    ]
    assert [base["stage_marker"] for base in lock["bases"]] == [
        "intermediate",
        "intermediate",
        "intermediate",
        "final",
    ]
    assert [base["from_ordinal"] for base in lock["bases"]] == [1, 2, 3, 4]
    assert [base["declared_reference"].split("@", 1)[0] for base in lock["bases"]] == [
        "docker.io/library/gcc",
        "docker.io/library/eclipse-temurin",
        "docker.io/library/node",
        "docker.io/library/python",
    ]
    for base in lock["bases"]:
        assert base["declared_reference"].startswith("docker.io/library/")
        assert "@sha256:" in base["declared_reference"]
        identity = base["platform_identities"][0]
        assert identity["platform"] == "linux/amd64"
        assert identity["manifest_digest"].startswith("sha256:")
        assert identity["config_digest"].startswith("sha256:")

    inputs = lock["external_inputs"]
    assert len(inputs) == 9
    expected_ids = {
        "actions-runner-linux-amd64",
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
        assert 0 < item["maximum_bytes"] <= 1024**3
        assert item["destination"].startswith(".ciw-build-inputs/")


def test_mobile_runner_product_and_smoke_contract() -> None:
    product = json.loads(PRODUCT.read_text(encoding="utf-8"))
    assert product["product_id"] == "runner-mobile"
    assert product["image_repository"].endswith("/github-actions-runner-mobile")
    assert product["platform"] == "linux/amd64"
    assert product["release_authority"] == "ci-workflows-git-tag"

    smoke = SMOKE.read_text(encoding="utf-8")
    for token in (
        'test "$(id -un)" = runner',
        "bash -n /home/runner/run.sh",
        "/home/runner/bin/Runner.Listener --version",
        "ID=debian",
        "VERSION_CODENAME=trixie",
        "libatomic.so.1",
        "java -version",
        "javac -version",
        "Python 3.12.14",
        "v24.19.0",
        "flutter --version",
        "dart --version",
        "sdkmanager --version",
        "adb version",
        "aapt2",
        "clang",
        "git-remote-https",
        "ci-workflows unzip 1.0",
        "/var/run/secrets/kubernetes.io/serviceaccount/token",
    ):
        assert token in smoke


def test_mobile_unzip_compatibility_tool_is_bounded() -> None:
    source = UNZIP_TOOL.read_text(encoding="utf-8")
    assert "zipfile.ZipFile" in source
    assert "safe_destination" in source
    assert 'option == "o"' in source
    assert 'argument == "-d"' in source
    assert "subprocess" not in source
    assert "urllib" not in source
