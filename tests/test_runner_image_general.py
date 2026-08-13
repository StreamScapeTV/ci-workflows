from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = ROOT / "runner-images/general/Dockerfile"
INPUT_LOCK = ROOT / ".ciw/oci-build-inputs/runner-general-linux-amd64.json"
PRODUCT = ROOT / "runner-images/general/product.json"
SMOKE = ROOT / "runner-images/general/smoke.sh"


def test_general_runner_uses_pinned_upstream_images() -> None:
    source = DOCKERFILE.read_text(encoding="utf-8")
    from_lines = [line for line in source.splitlines() if line.startswith("FROM ")]
    assert len(from_lines) == 3
    assert all("@sha256:" in line for line in from_lines)
    assert "git.faruqi.dev" not in source


def test_general_runner_build_is_offline() -> None:
    source = DOCKERFILE.read_text(encoding="utf-8").lower()
    for token in ("apt-get", "apk add", "curl http", "wget http", "git clone"):
        assert token not in source


def test_general_runner_input_lock_matches_source_stages() -> None:
    lock = json.loads(INPUT_LOCK.read_text(encoding="utf-8"))
    assert lock["product_id"] == "runner-general"
    assert lock["target_id"] == "linux-amd64"
    assert [base["stage_id"] for base in lock["bases"]] == ["python-runtime", "node-runtime", "actions-runner"]
    for base in lock["bases"]:
        assert "@sha256:" in base["declared_reference"]
        identity = base["platform_identities"][0]
        assert identity["platform"] == "linux/amd64"
        assert identity["manifest_digest"].startswith("sha256:")
        assert identity["config_digest"].startswith("sha256:")


def test_general_runner_external_inputs_are_checksum_locked() -> None:
    lock = json.loads(INPUT_LOCK.read_text(encoding="utf-8"))
    assert len(lock["external_inputs"]) == 6
    for item in lock["external_inputs"]:
        assert item["url"].startswith("https://")
        assert len(item["sha256"]) == 64
        int(item["sha256"], 16)
        assert item["destination"].startswith(".ciw-build-inputs/")


def test_general_runner_product_and_smoke_are_checked_in() -> None:
    product = json.loads(PRODUCT.read_text(encoding="utf-8"))
    assert product["product_id"] == "runner-general"
    assert product["image_repository"] == "git.faruqi.dev/mimranfaruqi/github-actions-runner-general"
    assert product["platform"] == "linux/amd64"
    smoke = SMOKE.read_text(encoding="utf-8")
    assert "/home/runner/run.sh" in smoke
    assert "python3 --version" in smoke
    assert "node --version" in smoke
