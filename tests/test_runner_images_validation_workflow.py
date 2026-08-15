from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/runner-images-validation.yml"


def test_runner_image_validation_is_a_thin_product_caller() -> None:
    source = WORKFLOW.read_text(encoding="utf-8")
    assert "pull_request:" in source
    assert "workflow_dispatch:" in source
    assert "uses: ./.github/workflows/reusable-oci-build.yml" in source
    assert "product_id: ciw-runner-images" in source
    assert "platform_set: linux-amd64" in source
    assert "github.event.pull_request.head.sha" in source
    assert "runner-images/**" in source
    assert ".ciw/oci-build-inputs/runner-*.json" in source


def test_runner_image_validation_has_no_publication_or_artifact_path() -> None:
    source = WORKFLOW.read_text(encoding="utf-8")
    assert "permissions:\n  contents: read" in source
    for forbidden in (
        "upload-artifact",
        "download-artifact",
        "packages: write",
        "id-token: write",
        "registry_username",
        "registry_token",
        "secrets:",
        "reusable-oci-publish.yml",
        "kubectl apply",
        "flux reconcile",
    ):
        assert forbidden not in source
