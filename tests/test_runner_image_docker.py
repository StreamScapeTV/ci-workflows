from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = ROOT / "runner-images/docker/Dockerfile"
TOOLCHAIN_LOCK = ROOT / "runner-images/docker/toolchain.lock.json"
SMOKE = ROOT / "runner-images/docker/smoke.sh"


def test_docker_runner_derives_only_from_official_upstream_images() -> None:
    source = DOCKERFILE.read_text(encoding="utf-8")
    from_lines = [line for line in source.splitlines() if line.startswith("FROM ")]
    assert len(from_lines) == 2
    assert from_lines[0].startswith("FROM docker.io/library/docker:29.7.2-cli@sha256:")
    assert from_lines[1].startswith("FROM ghcr.io/actions/actions-runner:2.336.0@sha256:")
    assert "git.faruqi.dev" not in source
    assert "runner-images/general" not in source


def test_docker_runner_contains_client_tooling_only() -> None:
    source = DOCKERFILE.read_text(encoding="utf-8")
    assert "COPY --from=docker-cli --chmod=0755 /usr/local/bin/docker /usr/local/bin/docker" in source
    assert "docker-buildx" in source
    assert "docker-compose" in source
    assert "docker:dind" not in source
    assert "DOCKER_HOST=" not in source
    for inherited_runtime in (
        "/usr/bin/containerd",
        "/usr/bin/containerd-shim-runc-v2",
        "/usr/bin/ctr",
        "/usr/bin/docker-init",
        "/usr/bin/docker-proxy",
        "/usr/bin/dockerd",
        "/usr/bin/runc",
    ):
        assert inherited_runtime in source
    for inherited_state in (
        "/etc/docker",
        "/root/.docker",
        "/home/runner/.docker",
        "/usr/libexec/docker",
        "/usr/local/lib/docker",
        "/var/lib/docker",
    ):
        assert inherited_state in source
    assert "USER runner\nRUN /usr/local/bin/runner-image-smoke" in source


def test_docker_runner_tool_versions_are_explicit() -> None:
    lock = json.loads(TOOLCHAIN_LOCK.read_text(encoding="utf-8"))
    assert lock["actions_runner"]["version"] == "2.336.0"
    assert lock["docker_cli_image"]["version"] == "29.7.2"
    assert lock["docker_buildx"]["version"] == "0.36.1"
    assert lock["docker_compose"]["version"] == "5.4.0"
    assert lock["policy"] == {
        "inherits_streamscape_runner_image": False,
        "daemon_in_image": False,
        "host_socket_baked_in": False,
        "kubernetes_credentials_baked_in": False,
    }


def test_docker_runner_smoke_fails_closed_on_runtime_authority_leakage() -> None:
    source = SMOKE.read_text(encoding="utf-8")
    assert 'test "$(id -un)" = runner' in source
    assert "test -x /home/runner/run.sh" in source
    assert "Docker version 29.7.2" in source
    assert "v0.36.1" in source
    assert "v5.4.0" in source
    assert "for forbidden in dockerd containerd ctr runc docker-proxy docker-init; do" in source
    assert "! command -v" in source
    assert "test ! -e /var/run/docker.sock" in source
    assert "test ! -e /run/docker.sock" in source
    assert "test ! -e /home/runner/.docker/config.json" in source
    assert "test ! -e /home/runner/.kube/config" in source
    assert "test ! -e /var/run/secrets/kubernetes.io/serviceaccount/token" in source
    assert 'test -z "${KUBECONFIG:-}"' in source
