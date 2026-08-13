from __future__ import annotations

import json
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
FOUNDATION_SHA = "70e08d4ddf8930046632a7135950e924b82e22bf"
OCI_HELPER_SHA = "29cb88e406a0490834bd556bb825d0e227c862ac"


class OciWorkflowContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.workflow = (ROOT / ".github/workflows/reusable-oci-build.yml").read_text()
        cls.smoke = (ROOT / ".github/workflows/oci-build-smoke.yml").read_text()
        cls.action = (ROOT / "actions/validate-oci/action.yml").read_text()
        cls.contract = json.loads((ROOT / "contracts/oci-products.json").read_text())
        cls.schema = json.loads((ROOT / "contracts/oci-build.schema.json").read_text())

    def test_public_workflow_is_call_only_least_privilege_and_zero_artifact(self) -> None:
        self.assertIn("workflow_call:", self.workflow)
        for forbidden in (
            "pull_request_target", "workflow_run:", "secrets: inherit",
            "upload-artifact", "download-artifact", "packages: write",
            "id-token: write", "latest", "kubectl", "flux reconcile",
            "helm upgrade", "docker.sock", "runs-on: self-hosted",
        ):
            self.assertNotIn(forbidden, self.workflow)
        self.assertIn("permissions:\n  contents: read", self.workflow)
        self.assertNotRegex(self.workflow, r"(?m)^\s+secrets:\s*$")
        self.assertIn("publication\":\"false\"", self.workflow)
        self.assertIn("registry_credentials\":\"false\"", self.workflow)
        self.assertIn("buildah\":\"1.33.7\"", self.workflow)

    def test_dynamic_build_job_consumes_exact_trusted_planner_output(self) -> None:
        self.assertIn("runs-on: [linux, amd64, general]", self.workflow)
        self.assertIn("runs-on: ${{ fromJSON(needs.plan.outputs.runs_on_json) }}", self.workflow)
        self.assertNotIn("runs-on: portable", self.workflow)
        self.assertNotRegex(self.workflow, r"runs-on:\s*\[.*buildah")
        self.assertNotIn("runs-on: buildah", self.workflow)
        self.assertNotIn("runs-on: self-hosted", self.workflow)
        for deprecated_label in (
            "buildah-tiny",
            "buildah-small",
            "buildah-medium",
            "buildah-high",
            "arc-runner-set",
        ):
            self.assertNotIn(f"runs-on: {deprecated_label}", self.workflow)
        self.assertIn("Resolve contract-owned OCI product and runner", self.workflow)

    def test_private_central_helpers_are_immutable_without_central_clone(self) -> None:
        self.assertNotIn("actions/checkout@", self.workflow)
        self.assertNotIn("repository: ${{ job.workflow_repository }}", self.workflow)
        self.assertNotIn("ref: ${{ job.workflow_sha }}", self.workflow)
        self.assertNotIn("path: .ciw", self.workflow)
        self.assertNotIn("./.ciw/actions/", self.workflow)
        self.assertNotIn("secrets: inherit", self.workflow)
        self.assertNotIn("private_dependency_token", self.workflow)
        self.assertEqual(
            4,
            self.workflow.count(
                f"uses: StreamScapeTV/ci-workflows/actions/validate-oci@{OCI_HELPER_SHA}"
            ),
        )
        for helper in ("exact-checkout", "prepare-workspace", "render-evidence", "cleanup-workspace"):
            self.assertIn(
                f"StreamScapeTV/ci-workflows/actions/{helper}@{FOUNDATION_SHA}",
                self.workflow,
            )

    def test_exact_source_cleanup_residue_and_terminal_projection_are_unconditional(self) -> None:
        required = (
            "Check out exact admitted caller source",
            f"uses: StreamScapeTV/ci-workflows/actions/exact-checkout@{FOUNDATION_SHA}",
            "Build and inspect exact source without publication",
            "continue-on-error: true",
            "if: always()",
            "Remove images, manifests, containers, layouts, caches, and temporary state",
            "Verify zero OCI-specific residue",
            "Remove and verify registered workspace state",
            "Verify exact caller source remained clean",
            "Project terminal OCI build status",
        )
        for value in required:
            self.assertIn(value, self.workflow)
        self.assertLess(self.workflow.index("Build and inspect exact source"), self.workflow.index("Remove images, manifests"))
        self.assertLess(self.workflow.index("Remove images, manifests"), self.workflow.index("Verify zero OCI-specific residue"))
        for cleanup in (
            "Remove images, manifests, containers, layouts, caches, and temporary state",
            "Verify zero OCI-specific residue",
            "Remove and verify registered workspace state",
        ):
            self.assertIn(f"name: {cleanup}\n        if: always()", self.workflow)
        for terminal in (
            "Render deterministic redacted OCI evidence",
            "Verify exact caller source remained clean",
            "Project terminal OCI build status",
        ):
            self.assertIn(
                f"name: {terminal}\n        if: ${{{{ always() && !cancelled() }}}}",
                self.workflow,
            )

    def test_public_inputs_do_not_expose_engine_runner_command_registry_or_secret(self) -> None:
        on_block = self.workflow.split("outputs:", 1)[0]
        for forbidden in (
            "builder:", "engine:", "docker:", "buildah:", "buildkit:",
            "podman:", "runner:", "runner_labels:", "runs_on:", "command:",
            "arguments:", "callback:", "registry:", "secret_name:",
        ):
            self.assertNotIn(forbidden, on_block)
        self.assertEqual(False, self.contract["publication"])
        self.assertEqual(False, self.contract["registry_credentials"])
        self.assertEqual("zero-default", self.contract["artifact_policy"])
        self.assertFalse(self.schema["additionalProperties"])

    def test_public_outputs_are_limited_to_the_checked_in_non_publishing_contract(self) -> None:
        public_block = self.workflow.split("permissions:", 1)[0].split("outputs:", 1)[1]
        self.assertEqual(
            {
                "result",
                "image_digest",
                "platform_digests_json",
                "resolved_inputs_json",
                "artifact_exception_used",
            },
            set(re.findall(r"(?m)^      ([a-z_]+):$", public_block)),
        )
        for forbidden in ("evidence_id", "canary_id", "previous_known_good", "rollback_id"):
            self.assertNotIn(forbidden, public_block)

    def test_action_is_thin_and_uses_stable_ciw_registration(self) -> None:
        self.assertIn("scripts/ci/ciw.py", self.action)
        self.assertIn("oci validate", self.action)
        self.assertIn("--phase", self.action)
        self.assertNotIn("scripts/ci/oci.py", self.action)
        self.assertNotIn("shell callback", self.action.lower())
        self.assertLess(len(self.action.splitlines()), 120)

    def test_smoke_is_real_non_publishing_buildah_contract_caller(self) -> None:
        self.assertNotIn("uses: ./.github/workflows/reusable-oci-build.yml", self.smoke)
        self.assertIn("plan:\n    name: Resolve bounded OCI smoke plan", self.smoke)
        self.assertIn("smoke:\n    name: Non-publishing Buildah smoke", self.smoke)
        self.assertIn("runs-on: [linux, amd64, general]", self.smoke)
        self.assertIn("runs-on: ${{ fromJSON(needs.plan.outputs.runs_on_json) }}", self.smoke)
        self.assertIn("timeout-minutes: 180", self.smoke)
        self.assertIn("uses: ./.ciw/actions/validate-oci", self.smoke)
        self.assertIn("phase: execute", self.smoke)
        self.assertIn("product_id: ciw-oci-input-smoke", self.smoke)
        self.assertIn("platform_set: linux-amd64", self.smoke)
        self.assertIn("steps.execute.outputs.resolved_inputs_json", self.smoke)
        self.assertIn(
            "Verify exact resolved OCI input evidence is nonempty and redacted",
            self.smoke,
        )
        self.assertIn("INPUT_EVIDENCE_OUTCOME", self.smoke)
        self.assertIn("test \"${INPUT_EVIDENCE_OUTCOME}\" = \"success\"", self.smoke)
        self.assertIn(
            "Require no runner-global containers/image cache at entry", self.smoke
        )
        self.assertIn(
            "Verify engine isolation left no runner-global cache", self.smoke
        )
        self.assertEqual(
            4,
            self.smoke.count("test ! -e /var/lib/containers/cache")
            + self.smoke.count("test ! -L /var/lib/containers/cache"),
        )
        self.assertIn("IMPLICIT_CACHE_BASELINE_OUTCOME", self.smoke)
        self.assertIn("IMPLICIT_CACHE_RESIDUE_OUTCOME", self.smoke)
        fixture = (
            ROOT / "tests/fixtures/oci-build/input-smoke/Containerfile"
        ).read_text(encoding="utf-8")
        self.assertIn(
            "FROM docker.io/library/busybox@sha256:"
            "73aaf090f3d85aa34ee199857f03fa3a95c8ede2ffd4cc2cdb5b94e566b11662",
            fixture,
        )
        self.assertIn(
            "COPY --chmod=0444 .ciw-build-inputs/README.md /ciw-input/README.md",
            fixture,
        )
        self.assertNotRegex(fixture, r"(?m)^RUN\s")
        self.assertIn("Check out exact admitted smoke source", self.smoke)
        self.assertIn("Verify exact smoke source remained clean", self.smoke)
        self.assertIn("Run focused OCI contract, security, and media tests", self.smoke)
        self.assertIn("python3 -m unittest discover -s tests -p 'test_oci_*.py' -v", self.smoke)
        self.assertIn("Verify focused OCI tests left central source clean", self.smoke)
        self.assertIn(
            "concurrency:\n"
            "  group: oci-build-smoke-${{ github.event.pull_request.number }}\n"
            "  cancel-in-progress: true",
            self.smoke,
        )
        self.assertIn("permissions:\n  actions: read\n  contents: read", self.smoke)
        self.assertIn("zero_artifacts:", self.smoke)
        self.assertIn("if: ${{ always() && !cancelled() }}", self.smoke)
        self.assertIn("timeout-minutes: 10", self.smoke)
        self.assertIn("/actions/runs/", self.smoke)
        self.assertIn("/artifacts?per_page=100", self.smoke)
        self.assertNotIn("workflow_dispatch:", self.smoke)
        self.assertNotIn("secrets:", self.smoke)
        self.assertNotIn("upload-artifact", self.smoke)
        for cleanup in (
            "Remove bounded zero-artifact audit token scope",
            "Verify zero-artifact audit token scope residue is absent",
        ):
            self.assertIn(f"name: {cleanup}\n        if: always()", self.smoke)
        self.assertEqual(2, self.smoke.count('if test -z "${TOKEN_SCOPE}"; then'))
        self.assertIn("unset GITHUB_TOKEN", self.smoke)
        self.assertIn("Project OCI smoke artifact audit status", self.smoke)

    def test_product_contract_covers_backend_agent_state_flux_and_rejects_application_mobile(self) -> None:
        products = self.contract["products"]
        self.assertEqual(
            {
                "iptv-backend-image",
                "agent-state-image",
                "flux-runner-images",
                "ciw-oci-smoke",
                "ciw-oci-input-smoke",
            },
            set(products),
        )
        flux = products["flux-runner-images"]
        self.assertTrue(flux["independent_bootstrap"])
        self.assertTrue(flux["flux_asset"])
        self.assertEqual("buildah-high", flux["runner_profile"])
        smoke = products["ciw-oci-smoke"]["targets"][0]
        self.assertIsNone(smoke["smoke_script"])
        self.assertEqual(
            "tests/fixtures/oci-build/smoke/inputs.lock.json",
            smoke["build_input_lock_path"],
        )
        self.assertEqual("scratch-only-v1", smoke["input_policy_id"])
        input_smoke = products["ciw-oci-input-smoke"]
        self.assertTrue(input_smoke["adoption_ready"])
        input_target = input_smoke["targets"][0]
        self.assertEqual(
            "tests/fixtures/oci-build/input-smoke/inputs.lock.json",
            input_target["build_input_lock_path"],
        )
        self.assertEqual("oci-inputs-public-v1", input_target["input_policy_id"])
        for product_id in (
            "iptv-backend-image",
            "agent-state-image",
            "flux-runner-images",
        ):
            self.assertFalse(products[product_id]["adoption_ready"])
        self.assertNotIn("StreamScapeTV/StreamScapeWeb", json.dumps(products))
        self.assertNotIn("StreamScapeTV/iptv-android", json.dumps(products))
        self.assertNotIn("StreamScapeTV/iptv-apple", json.dumps(products))

    def test_input_policy_is_central_closed_and_never_caller_selected(self) -> None:
        self.assertEqual("1.1.0", self.contract["contract_version"])
        self.assertEqual(
            {"oci-inputs-public-v1", "scratch-only-v1"},
            set(self.contract["input_policies"]),
        )
        policy = self.contract["input_policies"]["oci-inputs-public-v1"]
        self.assertEqual(["docker.io"], policy["allowed_registry_hosts"])
        self.assertEqual(
            ["registry-1.docker.io"], policy["allowed_registry_api_hosts"]
        )
        self.assertEqual(
            ["auth.docker.io"], policy["allowed_registry_token_hosts"]
        )
        self.assertEqual(
            ["production.cloudfront.docker.com"],
            policy["allowed_registry_blob_hosts"],
        )
        self.assertEqual(
            ["raw.githubusercontent.com"], policy["allowed_download_hosts"]
        )
        self.assertTrue(policy["https_only"])
        self.assertFalse(policy["ambient_auth"])
        self.assertEqual("same-profile-hosts", policy["redirect_policy"])
        self.assertEqual(5, policy["maximum_redirects"])
        self.assertGreaterEqual(policy["maximum_input_bytes"], 4096)
        public_schema = json.dumps(self.schema, sort_keys=True)
        for forbidden in (
            "input_policy_id",
            "build_input_lock_path",
            "allowed_registry_hosts",
            "allowed_registry_api_hosts",
            "allowed_registry_token_hosts",
            "allowed_registry_blob_hosts",
            "allowed_download_hosts",
            "source_url",
        ):
            self.assertNotIn(forbidden, public_schema)


if __name__ == "__main__":
    unittest.main()
