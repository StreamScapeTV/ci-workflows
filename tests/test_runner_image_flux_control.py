from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IMAGE_ROOT = ROOT / "runner-images/flux-control"
DOCKERFILE = IMAGE_ROOT / "Dockerfile"
PRODUCT = IMAGE_ROOT / "product.json"
TOOLCHAIN = IMAGE_ROOT / "toolchain.lock.json"
SMOKE = IMAGE_ROOT / "smoke.sh"
INPUT_LOCK = ROOT / ".ciw/oci-build-inputs/runner-flux-control-linux-amd64.json"


def test_flux_control_uses_official_actions_runner_directly() -> None:
    source = DOCKERFILE.read_text(encoding="utf-8")
    from_lines = [line for line in source.splitlines() if line.startswith("FROM ")]
    assert from_lines == [
        "FROM ghcr.io/actions/actions-runner:2.336.0@sha256:0cfdcc701ce933c6d243c6b0b2da767366dc9f2e99961d4c3754b0b78084cdda AS actions-runner"
    ]
    assert "git.faruqi.dev" not in source
    assert "runner-images/general" not in source
    for token in ("curl http", "wget http", "apt-get", "apk add"):
        assert token not in source.lower()


def test_flux_control_product_has_no_baked_cluster_authority() -> None:
    product = json.loads(PRODUCT.read_text(encoding="utf-8"))
    assert product["image_repository"] == "git.faruqi.dev/mimranfaruqi/github-actions-runner-flux-control"
    assert product["selection"]["direct_labels"] == ["linux", "amd64", "flux-control"]
    assert product["authority"] == {
        "cluster_credentials_baked_in": False,
        "service_account_token_baked_in": False,
        "kubeconfig_baked_in": False,
        "registry_credentials_baked_in": False,
    }
    assert product["composition"]["inherits_streamscape_runner_image"] is False


def test_flux_control_versions_are_explicit_and_compatible() -> None:
    toolchain = json.loads(TOOLCHAIN.read_text(encoding="utf-8"))
    assert toolchain["actions_runner"]["version"] == "2.336.0"
    assert toolchain["flux"]["version"] == "2.9.4"
    assert toolchain["kubectl"]["version"] == "1.34.8"
    assert toolchain["kubectl"]["compatibility_minor"] == "1.34"
    assert toolchain["helm"]["version"] == "4.2.4"
    assert toolchain["kustomize"]["version"] == "5.8.1"
    assert toolchain["yq"]["version"] == "4.53.3"


def test_flux_control_inputs_are_checksum_locked() -> None:
    lock = json.loads(INPUT_LOCK.read_text(encoding="utf-8"))
    assert lock["product_id"] == "runner-flux-control"
    assert lock["platforms"] == ["linux/amd64"]
    assert lock["input_policy_id"] == "runner-image-public-v1"
    assert len(lock["bases"]) == 1
    assert "@sha256:" in lock["bases"][0]["declared_reference"]
    assert [item["input_id"] for item in lock["external_inputs"]] == [
        "flux-linux-amd64",
        "kubectl-linux-amd64",
        "yq-linux-amd64",
        "helm-linux-amd64",
        "kustomize-linux-amd64",
    ]
    for item in lock["external_inputs"]:
        assert item["url"].startswith("https://")
        assert len(item["sha256"]) == 64
        int(item["sha256"], 16)
        assert item["maximum_bytes"] > 0
        assert item["destination"].startswith(".ciw-build-inputs/")


def test_flux_control_smoke_requires_clients_and_rejects_authority() -> None:
    source = SMOKE.read_text(encoding="utf-8")
    for token in ("flux --version", "kubectl version", "helm version", "kustomize version", "jq --version", "yq --version"):
        assert token in source
    assert "KUBECONFIG" in source
    assert "serviceaccount/token" in source
    for forbidden in ("command -v docker", "command -v dockerd", "command -v buildah", "command -v podman", "command -v skopeo"):
        assert forbidden in source
