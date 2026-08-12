from __future__ import annotations

import json
import unittest
from pathlib import Path

from ci_workflows.flux_assets import FluxAssetError, build_release_plan, load_contract
from ci_workflows.flux_assets_guards import (
    compose_guarded_release,
    validate_dependency_evidence,
    validate_operation_context,
    validate_runtime_probe_strict,
)

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = load_contract(ROOT / "contracts/flux-infrastructure-products.json")
SHA = "1" * 40
VERSION = "1.2.3"
D1 = "sha256:" + "1" * 64
D2 = "sha256:" + "2" * 64
D3 = "sha256:" + "3" * 64
D4 = "sha256:" + "4" * 64


def _plan(product_id: str, operation: str = "release"):
    if operation == "release":
        ref_type, ref_name = "tag", f"v{VERSION}"
    elif operation == "verify-only":
        ref_type, ref_name = "branch", "main"
    else:
        ref_type, ref_name = "", ""
    policy = (
        "images/github-actions-runner-buildah/Dockerfile"
        if product_id == "flux-runner-images"
        else "apps/github-actions-runner/Chart.yaml"
    )
    return build_release_plan(
        CONTRACT,
        admitted_sha=SHA,
        product_id=product_id,
        release_version=VERSION,
        operation=operation,
        policy_path=policy,
        request_id="guard-test",
        source_ref_type=ref_type,
        source_ref_name=ref_name,
    )


def _platforms() -> dict[str, object]:
    return {
        target: {
            "linux/amd64": {
                "manifest_digest": D1 if target == "runner-buildah" else D2,
                "config_digest": D3,
                "layer_digests": [D4],
                "labels": {"org.opencontainers.image.revision": SHA},
            }
        }
        for target in ("runner-buildah", "runner-mobile")
    }


def _oci_publish_evidence(*, source_sha: str = SHA, version: str = VERSION):
    repositories = {
        "runner-buildah": "ghcr.io/streamscapetv/flux-runner-buildah",
        "runner-mobile": "ghcr.io/streamscapetv/flux-runner-mobile",
    }
    digests = {"runner-buildah": D1, "runner-mobile": D2}
    targets = {
        target: {
            "repository": repository,
            "version": f"{repository}:{version}",
            "source_sha": f"{repository}:sha-{source_sha}",
            "manifest_digest": digests[target],
        }
        for target, repository in repositories.items()
    }
    immutable = {
        "targets": targets,
        "release": {"source_sha": source_sha, "version": version},
        "flux": {
            "canary_id": "flux-runner-images-canary",
            "previous_known_good": "flux-policy:runner-images/current-known-good",
            "rollback_id": "flux-runner-images-rollback",
        },
    }
    return {
        "result": "success",
        "image_digest": json.dumps(digests, sort_keys=True),
        "platform_digests_json": json.dumps(_platforms(), sort_keys=True),
        "immutable_references_json": json.dumps(immutable, sort_keys=True),
    }


def _oci_build_evidence():
    return {
        "result": "success",
        "image_digest": json.dumps(
            {"runner-buildah": D1, "runner-mobile": D2}, sort_keys=True
        ),
        "platform_digests_json": json.dumps(_platforms(), sort_keys=True),
        "artifact_exception_used": "false",
    }


def _helm_publish_evidence(*, version: str = VERSION):
    immutable = {
        "chart": (
            "oci://git.faruqi.dev/mimranfaruqi/helm-charts/"
            f"github-actions-runner:{version}"
        ),
        "chart_digest": D1,
        "package_sha256": "a" * 64,
    }
    return {
        "result": "success",
        "chart_digest": D1,
        "immutable_references_json": json.dumps(immutable, sort_keys=True),
    }


class OperationContextTests(unittest.TestCase):
    def test_release_is_tag_push_only(self) -> None:
        validate_operation_context(
            operation="release",
            event_name="push",
            ref_type="tag",
            ref_name=f"v{VERSION}",
            default_branch="main",
            release_version=VERSION,
        )
        with self.assertRaisesRegex(FluxAssetError, "release_event_forbidden"):
            validate_operation_context(
                operation="release",
                event_name="workflow_dispatch",
                ref_type="tag",
                ref_name=f"v{VERSION}",
                default_branch="main",
                release_version=VERSION,
            )

    def test_verify_only_is_manual_default_branch_only(self) -> None:
        validate_operation_context(
            operation="verify-only",
            event_name="workflow_dispatch",
            ref_type="branch",
            ref_name="main",
            default_branch="main",
            release_version=VERSION,
        )
        with self.assertRaisesRegex(FluxAssetError, "verify_ref_mismatch"):
            validate_operation_context(
                operation="verify-only",
                event_name="workflow_dispatch",
                ref_type="branch",
                ref_name="feature",
                default_branch="main",
                release_version=VERSION,
            )
        with self.assertRaisesRegex(FluxAssetError, "verify_event_forbidden"):
            validate_operation_context(
                operation="verify-only",
                event_name="push",
                ref_type="branch",
                ref_name="main",
                default_branch="main",
                release_version=VERSION,
            )


class DependencyIdentityTests(unittest.TestCase):
    def test_runner_release_preserves_nested_platform_evidence(self) -> None:
        plan = _plan("flux-runner-images")
        evidence = {
            "oci.build": _oci_build_evidence(),
            "oci.publish": _oci_publish_evidence(),
        }
        normalized = validate_dependency_evidence(plan, evidence)
        self.assertEqual(
            set(normalized["oci.publish"]["platform_digests_json"]),
            {"runner-buildah", "runner-mobile"},
        )
        result = compose_guarded_release(
            CONTRACT,
            request={
                "admitted_sha": SHA,
                "product_id": "flux-runner-images",
                "release_version": VERSION,
                "operation": "release",
                "policy_path": "images/github-actions-runner-buildah/Dockerfile",
                "request_id": "guard-test",
                "source_ref_type": "tag",
                "source_ref_name": f"v{VERSION}",
            },
            dependency_outputs=evidence,
        )
        self.assertEqual(result["result"], "verified")
        immutable = json.loads(result["immutable_references_json"])
        self.assertFalse(immutable["flux_handoff"]["mutation_authorized"])
        self.assertEqual(
            immutable["assets"]["oci.publish"]["release"]["source_sha"], SHA
        )

    def test_oci_publication_source_or_version_mismatch_fails(self) -> None:
        plan = _plan("flux-runner-images", "verify-only")
        with self.assertRaisesRegex(FluxAssetError, "dependency_identity_mismatch"):
            validate_dependency_evidence(
                plan,
                {"oci.publish": _oci_publish_evidence(source_sha="2" * 40)},
            )
        with self.assertRaisesRegex(FluxAssetError, "dependency_identity_mismatch"):
            validate_dependency_evidence(
                plan,
                {"oci.publish": _oci_publish_evidence(version="9.9.9")},
            )

    def test_dependency_set_must_match_operation_exactly(self) -> None:
        plan = _plan("flux-runner-images", "verify-only")
        with self.assertRaisesRegex(FluxAssetError, "dependency_identity_mismatch"):
            validate_dependency_evidence(
                plan,
                {
                    "oci.publish": _oci_publish_evidence(),
                    "oci.build": _oci_build_evidence(),
                },
            )

    def test_chart_publication_binds_version_and_digest(self) -> None:
        plan = _plan("flux-runner-chart-assets", "verify-only")
        normalized = validate_dependency_evidence(
            plan, {"helm.publish": _helm_publish_evidence()}
        )
        self.assertEqual(normalized["helm.publish"]["chart_digest"], D1)
        with self.assertRaisesRegex(FluxAssetError, "dependency_identity_mismatch"):
            validate_dependency_evidence(
                plan, {"helm.publish": _helm_publish_evidence(version="9.9.9")}
            )


class RuntimeProbeStrictnessTests(unittest.TestCase):
    def test_buildah_probe_requires_labels_subids_and_storage_driver(self) -> None:
        member = CONTRACT["products"]["flux-runner-images"]["members"][0]
        expected = member["runtime_assertions"]
        probe = {
            "os": expected["os"],
            "architecture": expected["architecture"],
            "tools": dict(expected["required_tools"]),
            "forbidden_tools_present": [],
            "forbidden_sockets_present": [],
            "credential_paths_present": [],
            "service_account_token_present": False,
            "kubeconfig_present": False,
            "labels": {label: "present" for label in expected["required_labels"]},
            "subordinate_id": expected["subordinate_id"],
            "storage_driver": expected["storage_driver"],
        }
        result = validate_runtime_probe_strict(expected, probe)
        self.assertEqual(result["storage_driver"], "vfs")
        broken = dict(probe)
        broken["labels"] = {}
        with self.assertRaisesRegex(FluxAssetError, "runtime_capability_mismatch"):
            validate_runtime_probe_strict(expected, broken)


class WorkflowRecoveryTests(unittest.TestCase):
    def test_public_workflow_uses_called_workflow_identity_and_event_guards(self) -> None:
        source = (
            ROOT / ".github/workflows/reusable-flux-infrastructure-assets.yml"
        ).read_text(encoding="utf-8")
        self.assertIn("repository: ${{ job.workflow_repository }}", source)
        self.assertIn("ref: ${{ job.workflow_sha }}", source)
        self.assertNotIn("ref: ${{ github.workflow_sha }}", source)
        self.assertIn("source_event_name: ${{ github.event_name }}", source)
        self.assertIn(
            "source_default_branch: ${{ github.event.repository.default_branch }}",
            source,
        )

    def test_internal_leaf_exists_without_nested_reusable_workflow_calls(self) -> None:
        source = (
            ROOT / ".github/workflows/internal-flux-infrastructure-assets.yml"
        ).read_text(encoding="utf-8")
        self.assertIn("workflow_call:", source)
        self.assertIn("runs-on: [linux, amd64, general]", source)
        self.assertIn("timeout-minutes: 30", source)
        self.assertNotIn("runs_on_json", source)
        self.assertNotIn("uses: ./.github/workflows/", source)
        self.assertNotIn("KUBECONFIG", source)
        self.assertNotIn("sops", source.casefold())
        self.assertIn("Confirm zero Actions artifacts", source)


if __name__ == "__main__":
    unittest.main()
