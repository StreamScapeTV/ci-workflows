from __future__ import annotations

from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/reusable-package-publish.yml"


class PackagePublishWorkflowContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.workflow = WORKFLOW.read_text(encoding="utf-8")

    def test_workflow_is_call_only_least_privilege_and_zero_artifact(self) -> None:
        self.assertIn("workflow_call:", self.workflow)
        for forbidden in (
            "workflow_dispatch:",
            "pull_request_target",
            "workflow_run:",
            "secrets: inherit",
            "upload-artifact",
            "download-artifact",
            "actions/cache",
            "id-token: write",
            "packages: write",
            "runs-on: self-hosted",
        ):
            self.assertNotIn(forbidden, self.workflow)
        self.assertIn("permissions:\n  contents: read", self.workflow)

    def test_public_inputs_are_product_neutral_and_do_not_duplicate_tag_authority(self) -> None:
        public = self.workflow.split("secrets:", 1)[0]
        expected = {
            "execution_backend",
            "ecosystem",
            "working_directory",
            "package_name",
            "package_group",
            "publication_plan_json",
        }
        self.assertEqual(
            expected,
            set(re.findall(r"(?m)^      ([a-z_]+):$", public.split("outputs:", 1)[0])),
        )
        for forbidden in (
            "admitted_sha:",
            "package_version:",
            "registry_url:",
            "registry_host:",
            "runner:",
            "runner_labels:",
            "runs_on:",
            "container_engine:",
            "command:",
            "shell:",
            "secret_name:",
            "product_id:",
        ):
            self.assertNotIn(forbidden, public)

    def test_execution_backend_is_bounded_and_hosted_rejects_before_product_execution(self) -> None:
        public = self.workflow.split("secrets:", 1)[0]
        self.assertIn("execution_backend:\n        description: organization or github-hosted", public)
        self.assertIn("default: organization", public)
        self.assertIn("inputs.execution_backend == 'github-hosted'", self.workflow)
        self.assertIn("inputs.execution_backend == 'organization'", self.workflow)
        self.assertIn("inputs.execution_backend != 'organization' && inputs.execution_backend != 'github-hosted'", self.workflow)
        self.assertIn("name: Reject unsupported hosted package publication", self.workflow)
        self.assertIn("runs-on: [ubuntu-latest]", self.workflow)
        hosted = self.workflow.split("  reject_hosted:\n", 1)[1].split("\n  reject_invalid:\n", 1)[0]
        self.assertNotIn("secrets.", hosted)
        self.assertNotIn("Check out exact", hosted)
        self.assertLess(
            self.workflow.index("Reject unsupported hosted package publication"),
            self.workflow.index("Check out exact tagged package source"),
        )
        self.assertIn("if: ${{ inputs.execution_backend == 'organization' }}\n    runs-on: [linux, amd64, general, small]", self.workflow)
        self.assertIn("needs: plan\n    if: ${{ inputs.execution_backend == 'organization' }}", self.workflow)
        self.assertNotIn("execution_backend: ${{", self.workflow)

    def test_named_registry_secrets_are_explicit_and_not_inherited(self) -> None:
        secret_block = self.workflow.split("secrets:", 1)[1].split("outputs:", 1)[0]
        self.assertIn("registry_username:", secret_block)
        self.assertIn("registry_token:", secret_block)
        self.assertNotIn("password", secret_block.lower())
        self.assertNotIn("inherit", secret_block.lower())
        self.assertIn("registry_token:\n        description:", secret_block)
        self.assertIn("required: false", secret_block)

    def test_called_workflow_source_uses_job_identity_not_caller_sha(self) -> None:
        self.assertEqual(2, self.workflow.count("repository: ${{ job.workflow_repository }}"))
        self.assertEqual(2, self.workflow.count("ref: ${{ job.workflow_sha }}"))
        self.assertEqual(
            2,
            self.workflow.count('test "$(git rev-parse HEAD)" = "${{ job.workflow_sha }}"'),
        )
        self.assertNotIn("github.workflow_sha", self.workflow)
        self.assertNotIn("repository: StreamScapeTV/ci-workflows", self.workflow)

    def test_product_git_tag_is_resolved_and_revalidated_as_sole_release_authority(self) -> None:
        self.assertEqual(2, self.workflow.count("uses: ./.ciw/actions/resolve-release-tag"))
        self.assertEqual(2, self.workflow.count("release_mode: tag-push"))
        self.assertNotIn("release_mode: existing-tag", self.workflow)
        self.assertIn(
            "INPUT_ADMITTED_SHA: ${{ steps.release.outputs.release_source_sha }}",
            self.workflow,
        )
        self.assertIn(
            "INPUT_PACKAGE_VERSION: ${{ steps.release.outputs.release_version }}",
            self.workflow,
        )
        self.assertIn(
            "release_version: ${{ needs.plan.outputs.release_version }}",
            self.workflow,
        )
        self.assertIn(
            "release_source_sha: ${{ needs.plan.outputs.release_source_sha }}",
            self.workflow,
        )
        self.assertIn("phase: revalidate", self.workflow)
        self.assertLess(
            self.workflow.index("Revalidate exact product tag"),
            self.workflow.index("Build inspect and publish exact package"),
        )

    def test_semantic_runner_is_central_and_ecosystem_plan_drives_publish_job(self) -> None:
        self.assertIn("runs-on: [linux, amd64, general, small]", self.workflow)
        self.assertIn("runs-on: ${{ fromJSON(needs.plan.outputs.runs_on_json) }}", self.workflow)
        self.assertNotIn("runs-on: portable", self.workflow)
        self.assertNotRegex(self.workflow, r"runs-on:\s*\[.*buildah")
        self.assertEqual(2, self.workflow.count('python3 "${GITHUB_WORKSPACE}/.ciw/scripts/ci/ciw.py"'))
        self.assertEqual(2, self.workflow.count("package publish"))
        self.assertNotIn("ci_workflows.ciw_packages", self.workflow)
        self.assertIn("--phase plan", self.workflow)
        self.assertIn("--phase execute", self.workflow)

    def test_exact_tagged_source_workspace_and_terminal_cleanup_are_unconditional(self) -> None:
        self.assertIn("Check out exact tagged package source", self.workflow)
        self.assertIn("uses: ./.ciw/actions/exact-checkout", self.workflow)
        self.assertIn("repository: ${{ github.repository }}", self.workflow)
        self.assertIn(
            "admitted_sha: ${{ needs.plan.outputs.release_source_sha }}",
            self.workflow,
        )
        self.assertIn("Prepare isolated package publication state", self.workflow)
        self.assertIn("cache_mode: disabled", self.workflow)
        for step in (
            "Remove and verify all registered package state",
            "Verify exact tagged source remained clean after cleanup",
        ):
            self.assertIn(f"name: {step}\n        if: always()", self.workflow)
        self.assertIn("Project terminal package publication status", self.workflow)
        self.assertIn("if: ${{ always() && !cancelled() }}", self.workflow)
        self.assertLess(
            self.workflow.index("Build inspect and publish exact package"),
            self.workflow.index("Remove and verify all registered package state"),
        )

    def test_clear_package_identity_outputs_are_public(self) -> None:
        output_block = self.workflow.split("outputs:", 1)[1].split("permissions:", 1)[0]
        for name in ("result", "ecosystem", "package_name", "package_version", "cleanup_result"):
            self.assertRegex(output_block, rf"(?m)^      {name}:$")
        self.assertIn("value: ${{ jobs.publish.outputs.package_name }}", output_block)
        self.assertIn("value: ${{ jobs.publish.outputs.package_version }}", output_block)
        self.assertIn("derived from the exact product Git tag", output_block)

    def test_no_android_gradle_or_supply_chain_expansion(self) -> None:
        lowered = self.workflow.lower()
        for forbidden in (
            "android",
            "gradle",
            "provenance",
            "attestation",
            "cosign",
            "sigstore",
            "oidc",
            "remote read-back",
        ):
            self.assertNotIn(forbidden, lowered)


if __name__ == "__main__":
    unittest.main()
