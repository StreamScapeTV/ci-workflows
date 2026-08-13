from __future__ import annotations

import json
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
FOUNDATION_SHA = "70e08d4ddf8930046632a7135950e924b82e22bf"
OCI_HELPER_SHA = "be0ec9505800bb5678083fc7ce912be83a90f139"


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
        self.assertIn("product_id: ciw-oci-smoke", self.smoke)
        self.assertIn("platform_set: linux-amd64", self.smoke)
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
            {"iptv-backend-image", "agent-state-image", "flux-runner-images", "ciw-oci-smoke"},
            set(products),
        )
        flux = products["flux-runner-images"]
        self.assertTrue(flux["independent_bootstrap"])
        self.assertTrue(flux["flux_asset"])
        self.assertEqual("buildah-high", flux["runner_profile"])
        smoke = products["ciw-oci-smoke"]["targets"][0]
        self.assertIsNone(smoke["smoke_script"])
        self.assertNotIn("StreamScapeTV/StreamScapeWeb", json.dumps(products))
        self.assertNotIn("StreamScapeTV/iptv-android", json.dumps(products))
        self.assertNotIn("StreamScapeTV/iptv-apple", json.dumps(products))


if __name__ == "__main__":
    unittest.main()
