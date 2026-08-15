from __future__ import annotations

import json
from pathlib import Path
import tempfile

from ci_workflows.oci_contract import load_contract
from ci_workflows.oci_publish_contract import (
    OciPublishError,
    PublishRequest,
    replay_decision,
    resolve_plan,
    runner_rebuild_decision,
)

ROOT = Path(__file__).resolve().parents[1]


def test_runner_image_policy_and_general_target_are_registered() -> None:
    contract = load_contract(ROOT)
    policy = contract["input_policies"]["runner-image-public-v1"]
    assert policy["allowed_registry_hosts"] == ["docker.io"]
    assert policy["maximum_input_bytes"] == 1024 * 1024 * 1024
    assert policy["allowed_download_hosts"] == [
        "github.com",
        "release-assets.githubusercontent.com",
        "objects.githubusercontent.com",
        "dl.k8s.io",
        "get.helm.sh",
        "storage.googleapis.com",
        "dl.google.com",
    ]

    product = contract["products"]["ciw-runner-images"]
    assert product["repository"] == "StreamScapeTV/ci-workflows"
    assert product["workspace_profile"] == "container"
    assert product["adoption_ready"] is False
    assert product["independent_bootstrap"] is True
    assert [target.target_id for target in contract["_products"]["ciw-runner-images"]["targets"]] == [
        "runner-general"
    ]
    target = product["targets"][0]
    assert target["dockerfile_path"] == "runner-images/general/Dockerfile"
    assert target["smoke_script"] == "runner-images/general/smoke.sh"
    assert target["build_input_lock_path"] == ".ciw/oci-build-inputs/runner-general-linux-amd64.json"
    assert target["input_policy_id"] == "runner-image-public-v1"


def test_runner_publication_uses_repository_release_tag_on_private_forgejo() -> None:
    contract = json.loads((ROOT / "contracts/oci-products.json").read_text(encoding="utf-8"))
    contract["products"]["ciw-runner-images"]["adoption_ready"] = True
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        (root / "contracts").mkdir()
        (root / "contracts/oci-products.json").write_text(
            json.dumps(contract, indent=2) + "\n", encoding="utf-8"
        )
        request = PublishRequest(
            repository="StreamScapeTV/ci-workflows",
            admitted_sha="1" * 40,
            release_authority_sha="1" * 40,
            product_id="ciw-runner-images",
            release_version="1.2.3",
            source_trust="trusted-exact",
        )
        plan = resolve_plan(root, request)
    assert len(plan.targets) == 1
    target = plan.targets[0]
    assert target.registry_repository == (
        "git.faruqi.dev/mimranfaruqi/github-actions-runner-general"
    )
    assert target.version_reference == (
        "git.faruqi.dev/mimranfaruqi/github-actions-runner-general:1.2.3"
    )
    assert target.source_reference.endswith(":sha-" + "1" * 40)


def test_generic_publication_keeps_issue_17_immutable_conflict_guard() -> None:
    local = "sha256:" + "1" * 64
    conflicting = "sha256:" + "2" * 64
    try:
        replay_decision(local, conflicting, None)
    except OciPublishError as error:
        assert error.code == "immutable_reference_conflict"
    else:
        raise AssertionError("generic publication must reject a conflicting immutable tag")


def test_runner_rebuild_may_replace_tags_for_the_same_git_release() -> None:
    local = "sha256:" + "1" * 64
    conflicting = "sha256:" + "2" * 64
    assert runner_rebuild_decision(local, conflicting, None) == (True, True, True)
    assert runner_rebuild_decision(local, local, local) == (False, False, True)


def test_legacy_flux_runner_product_remains_disabled_during_migration() -> None:
    contract = json.loads((ROOT / "contracts/oci-products.json").read_text(encoding="utf-8"))
    legacy = contract["products"]["flux-runner-images"]
    assert legacy["repository"] == "StreamScapeTV/flux"
    assert legacy["adoption_ready"] is False
    assert "Legacy Flux-source" in legacy["metadata"]["description"]
