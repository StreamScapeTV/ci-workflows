from __future__ import annotations

import copy
import json
import os
import tempfile
import unittest
from pathlib import Path

from ci_workflows.flux_assets import (
    FluxAssetError,
    build_release_plan,
    cleanup_state,
    load_contract,
    release,
    validate_bootstrap_independence,
    validate_chart_upstream,
    validate_dockerfile_bases,
    validate_live_inventory,
    validate_runtime_probe,
    validate_source_contract,
    verify_dependency_outputs,
    verify_replay,
    verify_residue_absent,
)

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "contracts/flux-infrastructure-products.json"
FIXTURES = ROOT / "tests/fixtures/flux-infrastructure-assets"
SHA = "1" * 40
DIGEST_A = "sha256:" + "a" * 64
DIGEST_B = "sha256:" + "b" * 64


class ContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.contract = load_contract(CONTRACT)

    def test_live_inventory_is_buildah_mobile_and_confirmed_chart_only(self) -> None:
        inventory = json.loads(
            (FIXTURES / "live-inventory.json").read_text(encoding="utf-8")
        )
        validate_live_inventory(self.contract, inventory["tree_paths"] + inventory["custom_runner_image_roots"])
        members = self.contract["products"]["flux-runner-images"]["members"]
        self.assertEqual(
            {member["id"] for member in members},
            {"github-actions-runner-buildah", "github-actions-runner-mobile"},
        )
        self.assertNotIn(
            "github-actions-runner-portable", {member["id"] for member in members}
        )
        chart = self.contract["products"]["flux-runner-chart-assets"]
        self.assertEqual(chart["chart_root"], "apps/github-actions-runner")
        self.assertEqual(chart["inventory_app_version"], "0.14.2")

    def test_inventory_drift_rejects_new_custom_portable_product(self) -> None:
        inventory = json.loads(
            (FIXTURES / "live-inventory.json").read_text(encoding="utf-8")
        )
        paths = list(inventory["tree_paths"]) + list(inventory["custom_runner_image_roots"])
        paths.append("images/github-actions-runner-portable")
        with self.assertRaisesRegex(FluxAssetError, "inventory_drift"):
            validate_live_inventory(self.contract, paths)

    def test_fixture_catalog_covers_security_and_rollback_cases(self) -> None:
        payload = json.loads((FIXTURES / "cases.json").read_text(encoding="utf-8"))
        names = {item["name"] for item in payload["cases"]}
        required = {
            "buildah-known-good-bootstrap",
            "mobile-known-good-bootstrap",
            "portable-is-upstream-not-custom",
            "mirrored-arc-chart-locked-upstream",
            "malicious-policy-path",
            "mutable-base-image",
            "self-bootstrap-candidate",
            "missing-runtime-tool",
            "forbidden-docker-socket",
            "credential-residue",
            "missing-chart-attribution",
            "conflicting-immutable-version",
            "mutable-latest-reference",
            "review-only-canary-handoff",
            "previous-known-good-rollback",
            "cleanup-residue",
        }
        self.assertTrue(required <= names)


class PlanningTests(unittest.TestCase):
    def setUp(self) -> None:
        self.contract = load_contract(CONTRACT)

    def test_image_release_plan_owns_runner_dependencies_and_handoff(self) -> None:
        plan = build_release_plan(
            self.contract,
            admitted_sha=SHA,
            product_id="flux-runner-images",
            release_version="1.2.3",
            operation="release",
            policy_path="scripts/github-actions-runner/verify_private_runner_image.py",
            request_id="runner-images-1.2.3",
            source_ref_type="tag",
            source_ref_name="v1.2.3",
        )
        self.assertEqual(plan.runs_on, ("linux", "amd64", "buildah", "high"))
        self.assertEqual(
            [dependency.api_name for dependency in plan.dependencies],
            ["oci.build", "oci.publish"],
        )
        self.assertEqual(plan.source_identity, f"sha-{SHA}")
        self.assertEqual(plan.canary_id, "flux-runner-images-canary")
        self.assertTrue(plan.bootstrap_policy["known_good_required"])

    def test_chart_plan_has_no_publication_dependency_and_uses_general_runner(self) -> None:
        plan = build_release_plan(
            self.contract,
            admitted_sha=SHA,
            product_id="flux-runner-chart-assets",
            release_version="1.2.3",
            operation="plan",
            policy_path="apps/github-actions-runner/values.schema.json",
            request_id="chart-plan",
        )
        self.assertEqual(plan.dependencies, ())
        self.assertEqual(plan.runs_on, ("linux", "amd64", "general"))

    def test_release_requires_exact_tag_context(self) -> None:
        with self.assertRaisesRegex(FluxAssetError, "release_tag_required"):
            build_release_plan(
                self.contract,
                admitted_sha=SHA,
                product_id="flux-runner-images",
                release_version="1.2.3",
                operation="release",
                policy_path="scripts/github-actions-runner/verify_private_runner_image.py",
                request_id="bad-tag",
                source_ref_type="branch",
                source_ref_name="main",
            )

    def test_policy_path_cannot_escape_or_select_credentials(self) -> None:
        for policy_path in (
            "../clusters/devops/secret.yaml",
            "clusters/devops/flux-system-common/docker/private-registry.secret.yaml",
            "/tmp/policy.json",
        ):
            with self.subTest(policy_path=policy_path):
                with self.assertRaisesRegex(FluxAssetError, "invalid_policy_path"):
                    build_release_plan(
                        self.contract,
                        admitted_sha=SHA,
                        product_id="flux-runner-chart-assets",
                        release_version="1.2.3",
                        operation="plan",
                        policy_path=policy_path,
                        request_id="bad-policy",
                    )

    def test_unsupported_product_is_closed(self) -> None:
        with self.assertRaisesRegex(FluxAssetError, "unsupported_product"):
            build_release_plan(
                self.contract,
                admitted_sha=SHA,
                product_id="portable-runner",
                release_version="1.2.3",
                operation="plan",
                policy_path="apps/github-actions-runner/values.schema.json",
                request_id="unsupported",
            )


class BootstrapAndSourceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.contract = load_contract(CONTRACT)
        self.plan = build_release_plan(
            self.contract,
            admitted_sha=SHA,
            product_id="flux-runner-images",
            release_version="1.2.3",
            operation="plan",
            policy_path="images/github-actions-runner-buildah/Dockerfile",
            request_id="bootstrap",
        )

    def test_digest_pinned_bases_are_required(self) -> None:
        pinned = (
            f"FROM ghcr.io/actions/actions-runner@{DIGEST_A} AS runner\n"
            f"FROM docker.io/library/node@{DIGEST_B}\n"
        )
        self.assertEqual(len(validate_dockerfile_bases(pinned)), 2)
        for mutable in (
            "FROM ghcr.io/actions/actions-runner:2.336.0\n",
            "ARG VERSION=2.336.0\nFROM ghcr.io/actions/actions-runner:${VERSION}\n",
        ):
            with self.subTest(mutable=mutable):
                with self.assertRaisesRegex(FluxAssetError, "mutable_base_image"):
                    validate_dockerfile_bases(mutable)

    def test_known_good_builder_must_be_distinct_and_digest_pinned(self) -> None:
        known_good = f"registry.invalid/builder@{DIGEST_A}"
        candidate = f"registry.invalid/candidate@{DIGEST_B}"
        validate_bootstrap_independence(
            self.plan,
            known_good_builder_reference=known_good,
            candidate_reference=candidate,
        )
        with self.assertRaisesRegex(FluxAssetError, "self_bootstrap_forbidden"):
            validate_bootstrap_independence(
                self.plan,
                known_good_builder_reference=known_good,
                candidate_reference=known_good,
            )
        with self.assertRaisesRegex(FluxAssetError, "mutable_bootstrap_builder"):
            validate_bootstrap_independence(
                self.plan,
                known_good_builder_reference="registry.invalid/builder:current",
                candidate_reference=candidate,
            )

    def test_source_contract_accepts_only_pinned_image_bases(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for member in self.contract["products"]["flux-runner-images"]["members"]:
                dockerfile = root / member["dockerfile_path"]
                dockerfile.parent.mkdir(parents=True, exist_ok=True)
                dockerfile.write_text(
                    f"FROM ghcr.io/actions/actions-runner@{DIGEST_A}\n",
                    encoding="utf-8",
                )
            result = validate_source_contract(
                self.contract, product_id="flux-runner-images", source_root=root
            )
            self.assertEqual(result["kind"], "runner-image-family")


class RuntimeAndChartTests(unittest.TestCase):
    def setUp(self) -> None:
        self.contract = load_contract(CONTRACT)

    def test_runtime_probe_checks_exact_tools_and_forbidden_state(self) -> None:
        member = self.contract["products"]["flux-runner-images"]["members"][0]
        expected = {
            "os": member["platform"]["os"],
            "architecture": member["platform"]["architecture"],
            "required_tools": member["required_tools"],
        }
        probe = {
            "os": "linux",
            "architecture": "amd64",
            "tools": dict(member["required_tools"]),
            "forbidden_tools_present": [],
            "forbidden_sockets_present": [],
            "credential_paths_present": [],
            "service_account_token_present": False,
            "kubeconfig_present": False,
        }
        validate_runtime_probe(expected, probe)
        bad = copy.deepcopy(probe)
        bad["forbidden_sockets_present"] = ["/var/run/docker.sock"]
        with self.assertRaisesRegex(FluxAssetError, "forbidden_socket_present"):
            validate_runtime_probe(expected, bad)
        bad = copy.deepcopy(probe)
        bad["tools"]["buildah"] = "0.0.0"
        with self.assertRaisesRegex(FluxAssetError, "tool_version_mismatch"):
            validate_runtime_probe(expected, bad)

    def test_chart_upstreams_require_digest_license_and_attribution(self) -> None:
        evidence = json.loads(
            (FIXTURES / "dependency-evidence.json").read_text(encoding="utf-8")
        )
        product = self.contract["products"]["flux-runner-chart-assets"]
        upstream_by_id = {item["id"]: item for item in product["upstream_assets"]}
        for upstream_id, actual in evidence["chart_upstream"].items():
            validate_chart_upstream(upstream_by_id[upstream_id], actual)
        bad = copy.deepcopy(evidence["chart_upstream"]["gha-runner-scale-set"])
        bad["digest"] = ""
        with self.assertRaisesRegex(FluxAssetError, "mutable_upstream"):
            validate_chart_upstream(upstream_by_id["gha-runner-scale-set"], bad)
        bad = copy.deepcopy(evidence["chart_upstream"]["gha-runner-scale-set"])
        bad["attribution_preserved"] = False
        with self.assertRaisesRegex(FluxAssetError, "attribution_missing"):
            validate_chart_upstream(upstream_by_id["gha-runner-scale-set"], bad)


class EvidenceAndHandoffTests(unittest.TestCase):
    def setUp(self) -> None:
        self.contract = load_contract(CONTRACT)
        self.evidence = json.loads(
            (FIXTURES / "dependency-evidence.json").read_text(encoding="utf-8")
        )

    def _request(self, product_id: str, policy_path: str) -> dict[str, str]:
        return {
            "admitted_sha": SHA,
            "product_id": product_id,
            "release_version": "1.2.3",
            "operation": "release",
            "policy_path": policy_path,
            "request_id": f"{product_id}-1.2.3",
            "source_ref_type": "tag",
            "source_ref_name": "v1.2.3",
        }

    def test_verified_image_release_emits_review_only_handoff(self) -> None:
        output = release(
            self.contract,
            request=self._request(
                "flux-runner-images",
                "scripts/github-actions-runner/verify_private_runner_image.py",
            ),
            dependency_outputs=self.evidence["image"],
        )
        self.assertEqual(output["result"], "verified")
        self.assertRegex(output["release_manifest_sha256"], r"^[0-9a-f]{64}$")
        immutable = json.loads(output["immutable_references_json"])
        handoff = immutable["flux_handoff"]
        self.assertTrue(handoff["review_required"])
        self.assertTrue(handoff["canary_required"])
        self.assertFalse(handoff["mutation_authorized"])
        self.assertFalse(handoff["desired_state_change_requested"])
        self.assertFalse(handoff["cluster_credentials_included"])
        self.assertFalse(handoff["sops_credentials_included"])
        self.assertEqual(
            handoff["previous_known_good_policy"],
            "flux-policy:runner-images/current-known-good",
        )

    def test_verified_chart_release_uses_helm_interfaces_only(self) -> None:
        request = self._request(
            "flux-runner-chart-assets", "apps/github-actions-runner/values.schema.json"
        )
        plan = build_release_plan(self.contract, **request)
        self.assertEqual(
            [dependency.api_name for dependency in plan.dependencies],
            ["helm.validate", "helm.publish"],
        )
        output = release(
            self.contract,
            request=request,
            dependency_outputs=self.evidence["chart"],
        )
        self.assertEqual(output["result"], "verified")

    def test_plan_never_needs_registry_evidence(self) -> None:
        output = release(
            self.contract,
            request={
                "admitted_sha": SHA,
                "product_id": "flux-runner-images",
                "release_version": "1.2.3",
                "operation": "plan",
                "policy_path": "images/github-actions-runner-buildah/Dockerfile",
                "request_id": "plan-only",
            },
            dependency_outputs={},
        )
        self.assertEqual(output["result"], "planned")
        immutable = json.loads(output["immutable_references_json"])
        self.assertFalse(immutable["handoff"]["mutation_authorized"])

    def test_missing_dependency_evidence_fails_closed(self) -> None:
        request = self._request(
            "flux-runner-images",
            "scripts/github-actions-runner/verify_private_runner_image.py",
        )
        with self.assertRaisesRegex(FluxAssetError, "missing_dependency_evidence"):
            release(self.contract, request=request, dependency_outputs={})

    def test_mutable_latest_reference_is_rejected(self) -> None:
        request = self._request(
            "flux-runner-images",
            "scripts/github-actions-runner/verify_private_runner_image.py",
        )
        plan = build_release_plan(self.contract, **request)
        bad = copy.deepcopy(self.evidence["image"])
        refs = json.loads(bad["oci.publish"]["immutable_references_json"])
        refs["runner-buildah"]["version"] = "registry.invalid/runner-buildah:latest"
        bad["oci.publish"]["immutable_references_json"] = json.dumps(refs)
        with self.assertRaisesRegex(FluxAssetError, "mutable_reference"):
            verify_dependency_outputs(plan, bad)

    def test_replay_requires_exact_identity_and_digest_parity(self) -> None:
        expected = {"version": DIGEST_A, "source": DIGEST_A}
        verify_replay(expected, dict(expected))
        with self.assertRaisesRegex(FluxAssetError, "immutable_conflict"):
            verify_replay(expected, {"version": DIGEST_A, "source": DIGEST_B})


class CleanupTests(unittest.TestCase):
    def test_symlink_cleanup_unlinks_only_issue_owned_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            target = base / "outside"
            target.mkdir()
            sentinel = target / "sentinel"
            sentinel.write_text("keep", encoding="utf-8")
            root = base / "flux-assets-test"
            os.symlink(target, root, target_is_directory=True)
            cleanup_state(root)
            verify_residue_absent(root)
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "keep")

    def test_residue_check_fails_while_state_exists(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "flux-assets-residue"
            root.mkdir()
            with self.assertRaisesRegex(FluxAssetError, "residue_detected"):
                verify_residue_absent(root)


if __name__ == "__main__":
    unittest.main()
