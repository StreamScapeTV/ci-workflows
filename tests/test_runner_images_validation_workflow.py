from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/runner-images-validation.yml"


def test_runner_image_validation_uses_exact_branch_contract_and_builder() -> None:
    source = WORKFLOW.read_text(encoding="utf-8")
    assert "pull_request:" in source
    assert "workflow_dispatch:" in source
    assert "uses: ./.ciw/actions/validate-oci" in source
    assert "uses: ./.github/workflows/reusable-oci-build.yml" not in source
    assert "product_id: ciw-runner-images" in source
    assert "platform_set: linux-amd64" in source
    assert "runs-on: ${{ fromJSON(needs.plan.outputs.runs_on_json) }}" in source
    assert "github.event.pull_request.head.sha" in source
    assert "runner-images/**" in source
    assert ".ciw/oci-build-inputs/runner-*.json" in source
    assert "Check out exact admitted runner-image source" in source
    assert "Verify exact runner-image source remained clean" in source


def test_runner_image_validation_has_cleanup_and_zero_artifact_audit() -> None:
    source = WORKFLOW.read_text(encoding="utf-8")
    assert "permissions:\n  actions: read\n  contents: read" in source
    assert "if: always()" in source
    assert "phase: cleanup" in source
    assert "phase: residue" in source
    assert "Verify runner-image validation artifacts remain zero" in source
    assert "/artifacts?per_page=100" in source
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
