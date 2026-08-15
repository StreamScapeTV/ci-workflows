from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = ROOT / "runner-images/general/Dockerfile"
INPUT_LOCK = ROOT / ".ciw/oci-build-inputs/runner-general-linux-amd64.json"
PRODUCT = ROOT / "runner-images/general/product.json"
SMOKE = ROOT / "runner-images/general/smoke.sh"
TOOLCHAIN = ROOT / "runner-images/general/toolchain.lock.json"
ZIP_TOOL = ROOT / "runner-images/general/zip.py"


def test_general_runner_uses_pinned_debian_only_stages() -> None:
    source = DOCKERFILE.read_text(encoding="utf-8")
    from_lines = [line for line in source.splitlines() if line.startswith("FROM ")]
    assert len(from_lines) == 3
    assert all("docker.io/library/" in line and "@sha256:" in line for line in from_lines)
    assert "buildpack-deps@sha256:" in from_lines[0]
    assert "node@sha256:" in from_lines[1]
    assert "python@sha256:" in from_lines[2]
    assert "buildpack-deps:trixie@" not in source
    assert "node:24.19.0-trixie-slim@" not in source
    assert "python:3.12.14-slim-trixie@" not in source
    assert "# syntax=" not in source.lower()
    assert "ghcr.io/actions/actions-runner" not in source
    assert "ubuntu" not in "\n".join(from_lines).lower()
    assert "git.faruqi.dev" not in source


def test_general_runner_build_is_networkless_and_engine_free() -> None:
    source = DOCKERFILE.read_text(encoding="utf-8").lower()
    for token in (
        "apt-get update",
        "apt-get install",
        "apt update",
        "apt install",
        "apk add",
        "dnf install",
        "curl http",
        "wget http",
        "git clone",
    ):
        assert token not in source
    assert ".ciw-build-inputs/actions-runner-linux-x64-2.336.0.tar.gz" in source
    assert "rm -f" in source and "/usr/bin/apt-get" in source
    assert "for forbidden in docker dockerd containerd ctr runc buildah podman skopeo sudo" in source


def test_general_runner_input_lock_matches_debian_stages() -> None:
    lock = json.loads(INPUT_LOCK.read_text(encoding="utf-8"))
    assert lock["product_id"] == "ciw-runner-images"
    assert lock["target_id"] == "runner-general"
    assert lock["input_policy_id"] == "runner-image-public-v1"
    assert [base["stage_id"] for base in lock["bases"]] == [
        "build-tools",
        "node-runtime",
        "final",
    ]
    assert [base["stage_marker"] for base in lock["bases"]] == [
        "intermediate",
        "intermediate",
        "final",
    ]
    assert [base["declared_reference"].split("@", 1)[0] for base in lock["bases"]] == [
        "docker.io/library/buildpack-deps",
        "docker.io/library/node",
        "docker.io/library/python",
    ]
    for ordinal, base in enumerate(lock["bases"], start=1):
        assert base["from_ordinal"] == ordinal
        assert base["declared_reference"].startswith("docker.io/library/")
        assert "@sha256:" in base["declared_reference"]
        identity = base["platform_identities"][0]
        assert identity["platform"] == "linux/amd64"
        assert identity["manifest_digest"].startswith("sha256:")
        assert identity["config_digest"].startswith("sha256:")


def test_general_runner_external_inputs_are_checksum_locked() -> None:
    lock = json.loads(INPUT_LOCK.read_text(encoding="utf-8"))
    assert len(lock["external_inputs"]) == 7
    by_id = {item["input_id"]: item for item in lock["external_inputs"]}
    runner = by_id["actions-runner-linux-amd64"]
    assert runner["sha256"] == (
        "04cf0be1aff4c3ec3554466c39124ca250e3effd8873bb7e8d68535aa9505d5d"
    )
    assert runner["maximum_bytes"] >= 226035903
    assert by_id["jq-linux-amd64"]["sha256"] == (
        "b1c22172dd303f3be49e935aa56aa48a8b7a46e0bc838b4997d3bb451495870f"
    )
    assert by_id["kubectl-linux-amd64"]["sha256"] == (
        "1e9045ec32bea85da43de85f0065358529ea7c7a152eca78154fba5b58c27d82"
    )
    assert by_id["kustomize-linux-amd64"]["sha256"] == (
        "029a7f0f4e1932c52a0476cf02a0fd855c0bb85694b82c338fc648dcb53a819d"
    )
    for item in lock["external_inputs"]:
        assert item["url"].startswith("https://")
        assert len(item["sha256"]) == 64
        int(item["sha256"], 16)
        assert 0 < item["maximum_bytes"] <= 2 * 1024**3
        assert item["destination"].startswith(".ciw-build-inputs/")


def test_general_runner_toolchain_is_release_readable() -> None:
    toolchain = json.loads(TOOLCHAIN.read_text(encoding="utf-8"))
    assert toolchain["policy"]["final_distribution"] == "debian-trixie"
    assert toolchain["policy"]["release_authority"] == "ci-workflows-git-tag"
    assert toolchain["toolchain"] == {
        "actions_runner": "2.336.0",
        "python": "3.12.14",
        "node": "24.19.0",
        "jq": "1.8.2",
        "yq": "4.53.3",
        "zstd": "1.5.7",
        "kubectl": "1.36.2",
        "helm": "4.2.4",
        "kustomize": "5.8.1",
    }
    assert set(toolchain["oci_stages"]) == {
        "build_tools",
        "node_runtime",
        "final_python_debian",
    }
    assert toolchain["external_assets"]["actions_runner"] == (
        "sha256:04cf0be1aff4c3ec3554466c39124ca250e3effd8873bb7e8d68535aa9505d5d"
    )
    assert all(value.startswith("sha256:") for value in toolchain["external_assets"].values())


def test_general_runner_smoke_proves_runtime_and_trust_boundary() -> None:
    product = json.loads(PRODUCT.read_text(encoding="utf-8"))
    assert product["product_id"] == "runner-general"
    assert product["image_repository"] == "git.faruqi.dev/mimranfaruqi/github-actions-runner-general"
    assert product["platform"] == "linux/amd64"

    smoke = SMOKE.read_text(encoding="utf-8")
    for token in (
        "ID=debian",
        "VERSION_CODENAME=trixie",
        "/home/runner/bin/Runner.Listener --version",
        "libatomic.so.1",
        "Python 3.12.14",
        "python3 -m venv",
        "v24.19.0",
        "jq-1.8.2",
        "v4.53.3",
        "v1.5.7",
        "v4.2.4",
        "v5.8.1",
        "v1.36.2",
        "/home/runner/.kube",
        "/var/run/secrets/kubernetes.io/serviceaccount/token",
    ):
        assert token in smoke
    for forbidden in ("docker", "dockerd", "containerd", "runc", "buildah", "podman", "skopeo", "sudo", "apt-get", "dpkg"):
        assert forbidden in smoke


def test_general_runner_zip_compatibility_tool_is_bounded() -> None:
    source = ZIP_TOOL.read_text(encoding="utf-8")
    assert "zipfile.ZipFile" in source
    assert "source.rglob" in source
    assert "subprocess" not in source
    assert "urllib" not in source
