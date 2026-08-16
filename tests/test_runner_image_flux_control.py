from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IMAGE_ROOT = ROOT / "runner-images/flux-control"
DOCKERFILE = IMAGE_ROOT / "Dockerfile"
PRODUCT = IMAGE_ROOT / "product.json"
TOOLCHAIN = IMAGE_ROOT / "toolchain.lock.json"
SMOKE = IMAGE_ROOT / "smoke.sh"
PREPARE_INPUTS = IMAGE_ROOT / "prepare_inputs.py"


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


def test_flux_control_product_has_no_baked_authority() -> None:
    product = json.loads(PRODUCT.read_text(encoding="utf-8"))
    assert product["image_repository"] == "git.faruqi.dev/mimranfaruqi/github-actions-runner-flux-control"
    assert product["selection"] == {
        "semantic_profile": "flux-control",
        "direct_labels": ["linux", "amd64", "flux-control"],
    }
    assert product["authority"] == {
        "cluster_credentials_baked_in": False,
        "service_account_token_baked_in": False,
        "kubeconfig_baked_in": False,
        "registry_credentials_baked_in": False,
        "sops_credentials_baked_in": False,
        "github_credentials_baked_in": False,
    }
    assert product["composition"] == {"inherits_streamscape_runner_image": False}


def test_flux_control_versions_are_explicit_and_compatible() -> None:
    toolchain = json.loads(TOOLCHAIN.read_text(encoding="utf-8"))
    assert toolchain["actions_runner"]["version"] == "2.336.0"
    assert toolchain["flux"]["version"] == "2.9.4"
    assert toolchain["kubectl"]["version"] == "1.34.8"
    assert toolchain["kubectl"]["compatibility_minor"] == "1.34"
    assert toolchain["helm"]["version"] == "4.2.4"
    assert toolchain["kustomize"]["version"] == "5.8.1"
    assert toolchain["yq"]["version"] == "4.53.3"
    for tool in ("flux", "kubectl", "helm", "kustomize", "yq"):
        digest = toolchain[tool]["linux_amd64_sha256"]
        assert len(digest) == 64
        int(digest, 16)


def test_flux_control_input_preparation_is_fixed_checksum_bounded_and_self_cleaning() -> None:
    source = PREPARE_INPUTS.read_text(encoding="utf-8")
    assert ".ciw/oci-build-inputs" not in source
    assert 'TOOLCHAIN = IMAGE_ROOT / "toolchain.lock.json"' in source
    assert 'raise RuntimeError(f"invalid toolchain row: {name}")' in source
    assert 'raise RuntimeError("invalid Flux-control toolchain version")' in source
    for origin in (
        "github.com/fluxcd/flux2/releases/download",
        "dl.k8s.io/release",
        "github.com/mikefarah/yq/releases/download",
        "get.helm.sh/helm-v",
        "github.com/kubernetes-sigs/kustomize/releases/download",
    ):
        assert origin in source
    assert "hashlib.sha256(data).hexdigest()" in source
    assert "response.read(maximum_bytes + 1)" in source
    assert "DESTINATION.mkdir(exist_ok=False)" in source
    assert "shutil.rmtree(DESTINATION, ignore_errors=True)" in source


def test_flux_control_image_strips_inherited_runtime_and_credential_state() -> None:
    source = DOCKERFILE.read_text(encoding="utf-8")
    for path in (
        "/usr/bin/containerd",
        "/usr/bin/ctr",
        "/usr/bin/docker",
        "/usr/bin/dockerd",
        "/usr/bin/runc",
        "/etc/docker",
        "/home/runner/.docker",
        "/usr/libexec/docker",
        "/usr/local/lib/docker",
        "/var/lib/docker",
        "/home/runner/.kube",
        "/home/runner/.config/containers",
        "/home/runner/.config/sops",
    ):
        assert path in source
    assert "USER runner\nRUN /usr/local/bin/runner-image-smoke" in source


def test_flux_control_smoke_requires_clients_and_rejects_authority() -> None:
    source = SMOKE.read_text(encoding="utf-8")
    assert 'test "$(id -un)" = runner' in source
    for token in (
        "flux --version",
        "kubectl version",
        "helm version",
        "kustomize version",
        "jq --version",
        "yq --version",
    ):
        assert token in source
    assert "for forbidden in docker dockerd containerd ctr runc buildah podman skopeo; do" in source
    assert '! command -v "${forbidden}"' in source
    for state in (
        "/var/run/docker.sock",
        "/run/docker.sock",
        "/home/runner/.docker/config.json",
        "/home/runner/.config/containers/auth.json",
        "/home/runner/.kube/config",
        "/var/run/secrets/kubernetes.io/serviceaccount/token",
        "/home/runner/.config/sops/age/keys.txt",
    ):
        assert state in source
    for variable in (
        "KUBECONFIG",
        "SOPS_AGE_KEY",
        "SOPS_AGE_KEY_FILE",
        "GITHUB_TOKEN",
        "GH_TOKEN",
        "REGISTRY_AUTH_FILE",
        "DOCKER_CONFIG",
    ):
        assert variable in source
