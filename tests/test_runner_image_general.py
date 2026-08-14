from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = ROOT / "runner-images/general/Dockerfile"
INPUT_LOCK = ROOT / ".ciw/oci-build-inputs/runner-general-linux-amd64.json"
PRODUCT = ROOT / "runner-images/general/product.json"
SMOKE = ROOT / "runner-images/general/smoke.sh"
TOOLCHAIN = ROOT / "runner-images/general/toolchain.lock.json"


def test_general_runner_uses_one_pinned_official_runner_base() -> None:
    source = DOCKERFILE.read_text(encoding="utf-8")
    from_lines = [line for line in source.splitlines() if line.startswith("FROM ")]
    assert len(from_lines) == 1
    assert from_lines[0].startswith("FROM ghcr.io/actions/actions-runner:2.336.0@sha256:")
    assert "git.faruqi.dev" not in source


def test_general_runner_build_is_offline_and_strips_inherited_engines() -> None:
    source = DOCKERFILE.read_text(encoding="utf-8").lower()
    for token in ("apt-get", "apt update", "apk add", "curl http", "wget http", "git clone"):
        assert token not in source
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
    for command in ("docker", "dockerd", "containerd", "buildah", "podman", "skopeo"):
        assert f"! command -v {command}" in source


def test_general_runner_input_lock_matches_single_source_stage() -> None:
    lock = json.loads(INPUT_LOCK.read_text(encoding="utf-8"))
    assert lock["product_id"] == "runner-general"
    assert lock["target_id"] == "linux-amd64"
    assert lock["input_policy_id"] == "runner-image-public-v1"
    assert [base["stage_id"] for base in lock["bases"]] == ["actions-runner"]
    base = lock["bases"][0]
    assert base["from_ordinal"] == 1
    assert base["stage_marker"] == "final"
    assert "@sha256:" in base["declared_reference"]
    identity = base["platform_identities"][0]
    assert identity["platform"] == "linux/amd64"
    assert identity["manifest_digest"].startswith("sha256:")
    assert identity["config_digest"].startswith("sha256:")


def test_general_runner_external_inputs_are_checksum_locked() -> None:
    lock = json.loads(INPUT_LOCK.read_text(encoding="utf-8"))
    assert len(lock["external_inputs"]) == 8
    by_id = {item["input_id"]: item for item in lock["external_inputs"]}
    assert by_id["cpython-linux-amd64"]["sha256"] == "5bd6f36fd7ef02b909234c94dca9994ef0da06ace3bc3cece4fe27870e9cdbbe"
    assert by_id["node-linux-amd64"]["sha256"] == "f625d97cd707df4ff96254916fbc5ff014f09c09effe5a1e0ca8f6d41a8789d4"
    assert by_id["kustomize-linux-amd64"]["sha256"] == "029a7f0f4e1932c52a0476cf02a0fd855c0bb85694b82c338fc648dcb53a819d"
    for item in lock["external_inputs"]:
        assert item["url"].startswith("https://")
        assert len(item["sha256"]) == 64
        int(item["sha256"], 16)
        assert item["maximum_bytes"] > 0
        assert item["destination"].startswith(".ciw-build-inputs/")


def test_general_runner_toolchain_records_external_runtimes() -> None:
    toolchain = json.loads(TOOLCHAIN.read_text(encoding="utf-8"))
    assert toolchain["toolchain"]["python"] == "3.12.13"
    assert toolchain["toolchain"]["node"] == "24.19.0"
    assert list(toolchain["oci_stages"]) == ["actions_runner"]
    assert toolchain["external_runtime_assets"]["python"]["sha256"] == "5bd6f36fd7ef02b909234c94dca9994ef0da06ace3bc3cece4fe27870e9cdbbe"
    assert toolchain["external_runtime_assets"]["node"]["sha256"] == "f625d97cd707df4ff96254916fbc5ff014f09c09effe5a1e0ca8f6d41a8789d4"


def test_general_runner_product_and_smoke_are_checked_in() -> None:
    product = json.loads(PRODUCT.read_text(encoding="utf-8"))
    assert product["product_id"] == "runner-general"
    assert product["image_repository"] == "git.faruqi.dev/mimranfaruqi/github-actions-runner-general"
    assert product["platform"] == "linux/amd64"
    smoke = SMOKE.read_text(encoding="utf-8")
    assert 'test "$(id -un)" = runner' in smoke
    assert "/home/runner/run.sh" in smoke
    assert "v24.19.0" in smoke
    for token in (
        "python3 --version",
        "node --version",
        "npm --version",
        "corepack --version",
        "git --version",
        "jq --version",
        "yq --version",
        "helm version",
        "kustomize version",
        "kubectl version",
    ):
        assert token in smoke
