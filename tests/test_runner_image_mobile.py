from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = ROOT / "runner-images/mobile/Dockerfile"
PRODUCT = ROOT / "runner-images/mobile/product.json"
SMOKE = ROOT / "runner-images/mobile/smoke.sh"
TOOLCHAIN = ROOT / "runner-images/mobile/toolchain.lock.json"
ASSEMBLE = ROOT / "runner-images/mobile/assemble.py"
UNZIP_TOOL = ROOT / "runner-images/mobile/unzip.py"
RETIRED_INPUT_LOCK = ROOT / ".ciw/oci-build-inputs/runner-mobile-linux-amd64.json"


def _lock() -> dict[str, object]:
    return json.loads(TOOLCHAIN.read_text(encoding="utf-8"))


def test_mobile_runner_uses_reviewed_base_releases_without_digest_contract() -> None:
    source = DOCKERFILE.read_text(encoding="utf-8")
    lock = _lock()
    from_lines = [line for line in source.splitlines() if line.startswith("FROM ")]
    expected = (
        ("build_tools", "build-tools"),
        ("java", "java-runtime"),
        ("node", "node-runtime"),
        ("final_python_debian", "final"),
    )

    assert len(from_lines) == len(expected)
    for line, (lock_key, stage) in zip(from_lines, expected, strict=True):
        assert line.startswith(f"FROM {lock['base_images'][lock_key]}@sha256:")
        assert line.endswith(f" AS {stage}")

    serialized_lock = json.dumps(lock, sort_keys=True)
    assert "root_digest" not in serialized_lock
    assert "manifest_digest" not in serialized_lock
    assert "config_digest" not in serialized_lock


def test_mobile_runner_downloads_reviewed_public_payloads_directly() -> None:
    source = DOCKERFILE.read_text(encoding="utf-8")
    downloads = _lock()["downloads"]
    argument_prefixes = {
        "actions_runner_linux_x64": "ACTIONS_RUNNER",
        "flutter_linux_x64": "FLUTTER",
        "android_command_line_tools_linux": "ANDROID_CMDLINE_TOOLS",
        "android_platform_tools_linux": "ANDROID_PLATFORM_TOOLS",
        "android_platform_36": "ANDROID_PLATFORM_36",
        "android_platform_37": "ANDROID_PLATFORM_37",
        "android_build_tools_36_linux": "ANDROID_BUILD_TOOLS_36",
        "android_build_tools_37_linux": "ANDROID_BUILD_TOOLS_37",
        "android_ndk_r28c_linux": "ANDROID_NDK",
    }

    assert not RETIRED_INPUT_LOCK.exists()
    assert ".ciw-build-inputs" not in source
    assert "curl --fail --location --retry 3" in source
    assert "sha256sum -c -" in source

    for key, prefix in argument_prefixes.items():
        item = downloads[key]
        assert item["url"].startswith("https://")
        assert len(item["sha256"]) == 64
        int(item["sha256"], 16)
        assert f"ARG {prefix}_URL={item['url']}" in source
        assert f"ARG {prefix}_SHA256={item['sha256']}" in source


def test_mobile_runner_locks_compatible_toolchain() -> None:
    source = DOCKERFILE.read_text(encoding="utf-8")
    lock = _lock()
    toolchain = lock["toolchain"]
    argument_values = {
        "FLUTTER_VERSION": toolchain["flutter"],
        "DART_VERSION": toolchain["dart"],
        "ANDROID_CMDLINE_TOOLS_VERSION": toolchain["android_command_line_tools"],
        "ANDROID_COMPAT_PLATFORM_VERSION": toolchain["android_platforms"][0],
        "ANDROID_PLATFORM_VERSION": toolchain["android_platforms"][1].split(".")[0],
        "ANDROID_PLATFORM_PACKAGE_VERSION": toolchain["android_platforms"][1],
        "ANDROID_COMPAT_BUILD_TOOLS_VERSION": toolchain["android_build_tools"][0],
        "ANDROID_BUILD_TOOLS_VERSION": toolchain["android_build_tools"][1],
        "ANDROID_NDK_VERSION": toolchain["android_ndk"],
    }

    assert lock["policy"] == {
        "final_distribution": "debian-trixie",
        "release_authority": "ci-workflows-git-tag",
        "independent_product": True,
    }
    for name, value in argument_values.items():
        assert f"ARG {name}={value}" in source


def test_mobile_runner_materializes_required_android_components_safely() -> None:
    source = DOCKERFILE.read_text(encoding="utf-8")
    assemble = ASSEMBLE.read_text(encoding="utf-8")

    for path in (
        "/opt/android-sdk/cmdline-tools/latest",
        "/opt/android-sdk/platform-tools",
        "/opt/android-sdk/platforms",
        "/opt/android-sdk/build-tools",
        "/opt/android-sdk/ndk",
    ):
        assert path in source or path in assemble

    assert "android-${ANDROID_PLATFORM_PACKAGE_VERSION}" in source
    assert "android-${ANDROID_PLATFORM_VERSION}" in source
    assert "tarfile.open" in assemble
    assert "zipfile.ZipFile" in assemble
    assert '".." in relative.parts' in assemble
    assert "shutil.copytree" in assemble
    assert "subprocess" not in assemble
    assert "urllib" not in assemble


def test_mobile_runner_has_no_container_engine_or_privileged_runtime_path() -> None:
    source = DOCKERFILE.read_text(encoding="utf-8")
    smoke = SMOKE.read_text(encoding="utf-8")

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
        assert command in smoke

    assert "/usr/bin/apt-get" in source
    assert "/usr/bin/dpkg" in source
    assert "test ! -e /var/run/docker.sock" in source
    assert "test ! -e /run/docker.sock" in source
    assert "/var/run/secrets/kubernetes.io/serviceaccount/token" in smoke
    assert 'test -z "${KUBECONFIG:-}"' in smoke


def test_mobile_runner_product_and_smoke_contract() -> None:
    product = json.loads(PRODUCT.read_text(encoding="utf-8"))
    toolchain = _lock()["toolchain"]
    smoke = SMOKE.read_text(encoding="utf-8")

    assert product["product_id"] == "runner-mobile"
    assert product["image_repository"].endswith("/github-actions-runner-mobile")
    assert product["platform"] == "linux/amd64"
    assert product["dockerfile"] == "runner-images/mobile/Dockerfile"
    assert product["smoke"] == "runner-images/mobile/smoke.sh"
    assert product["release_authority"] == "ci-workflows-git-tag"

    expected_versions = (
        toolchain["actions_runner"],
        toolchain["java"].split("+")[0],
        f"Python {toolchain['python']}",
        f"v{toolchain['node']}",
        f"Flutter {toolchain['flutter']}",
        f"Dart SDK version: {toolchain['dart']}",
        toolchain["android_build_tools"][1],
        toolchain["android_ndk"],
    )
    for value in expected_versions:
        assert value in smoke

    for token in (
        'test "$(id -un)" = runner',
        "bash -n /home/runner/run.sh",
        "ID=debian",
        "VERSION_CODENAME=trixie",
        "java -version",
        "javac -version",
        "npm --version",
        "corepack --version",
        "sdkmanager --version",
        "adb version",
        "aapt2",
        "clang",
        "git-remote-https",
        "ci-workflows unzip 1.0",
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
