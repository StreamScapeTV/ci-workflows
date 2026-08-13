from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = ROOT / "runner-images/docker/Dockerfile"
INPUT_LOCK = ROOT / ".ciw/oci-build-inputs/runner-docker-linux-amd64.json"
TOOLCHAIN_LOCK = ROOT / "runner-images/docker/toolchain.lock.json"


def test_docker_runner_derives_only_from_official_upstream_images() -> None:
    source = DOCKERFILE.read_text(encoding="utf-8")
    from_lines = [line for line in source.splitlines() if line.startswith("FROM ")]

    assert from_lines == [
        "FROM docker.io/library/docker:29.7.2-cli@sha256:000bb62ff495f986c9f5578eb67cc2cb98b91138eda81d7762d5371eb8a497fe AS docker-cli",
        "FROM ghcr.io/actions/actions-runner:2.336.0@sha256:0cfdcc701ce933c6d243c6b0b2da767366dc9f2e99961d4c3754b0b78084cdda",
    ]
    assert "git.faruqi.dev" not in source
    assert "runner-images/general" not in source


def test_docker_runner_contains_clients_not_a_daemon_or_runtime_socket() -> None:
    source = DOCKERFILE.read_text(encoding="utf-8")

    assert "/usr/local/bin/docker" in source
    assert "docker-buildx" in source
    assert "docker-compose" in source
    assert "docker:dind" not in source
    assert "DOCKER_HOST=" not in source
    assert "/var/run/docker.sock" not in source
    assert "COPY --from=docker-cli" in source
    assert "COPY --from=docker-cli --chmod=0755 /usr/local/bin/dockerd" not in source
    assert "COPY --from=docker-cli --chmod=0755 /usr/local/bin/containerd" not in source


def test_docker_runner_input_lock_is_oci_only_and_immutable() -> None:
    lock = json.loads(INPUT_LOCK.read_text(encoding="utf-8"))

    assert lock["product_id"] == "runner-docker"
    assert lock["target_id"] == "linux-amd64"
    assert lock["platforms"] == ["linux/amd64"]
    assert lock["external_inputs"] == []
    assert [item["stage_id"] for item in lock["bases"]] == [
        "docker-cli",
        "actions-runner",
    ]
    for base in lock["bases"]:
        assert "@sha256:" in base["declared_reference"]
        assert base["platform_identities"] == [
            {
                "platform": "linux/amd64",
                "manifest_digest": base["platform_identities"][0]["manifest_digest"],
                "config_digest": base["platform_identities"][0]["config_digest"],
            }
        ]
        assert base["platform_identities"][0]["manifest_digest"].startswith("sha256:")
        assert base["platform_identities"][0]["config_digest"].startswith("sha256:")


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
