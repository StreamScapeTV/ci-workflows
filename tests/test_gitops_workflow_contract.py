from __future__ import annotations

import json
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
FOUNDATION_SHA = "70e08d4ddf8930046632a7135950e924b82e22bf"
GITOPS_SHA = "8445e63dd9fa9468b60b6d0c61e543da9681b47b"
EXECUTION_BACKEND_SHA = "01d1d10bafcc4fc1e4c51663f72b08f694dc4e35"


class GitOpsWorkflowContractTests(unittest.TestCase):
    def test_reusable_workflow_exposes_bounded_backend_and_source_inputs(self) -> None:
        path = ROOT / ".github/workflows/reusable-gitops-validation.yml"
        source = path.read_text(encoding="utf-8")
        workflow = yaml.safe_load(source)
        inputs = workflow[True]["workflow_call"]["inputs"]
        self.assertEqual(
            {
                "execution_backend",
                "admitted_sha",
                "validation_profile",
                "consumer_contract",
                "change_base_sha",
                "policy_script_profile",
                "artifact_exception_id",
            },
            set(inputs),
        )
        self.assertEqual("organization", inputs["execution_backend"]["default"])
        for forbidden in (
            "runner",
            "runs_on",
            "tool_url",
            "command",
            "arguments",
            "registry",
            "cluster",
            "kubeconfig",
            "sops_key",
            "secret_name",
            "deployment",
        ):
            self.assertNotIn(forbidden, inputs)
        self.assertEqual({"actions": "read", "contents": "read"}, workflow["permissions"])
        self.assertNotIn("secrets:", source)
        self.assertNotIn("upload-artifact", source)
        self.assertNotIn("runs-on: macOS", source)
        self.assertNotIn("self-hosted", source)
        self.assertNotIn("runs-on: portable", source)
        self.assertNotIn("runs-on: [linux, amd64, general]", source)

        hosted = workflow["jobs"]["plan"]
        organization = workflow["jobs"]["plan_organization"]
        validate = workflow["jobs"]["validate"]
        self.assertEqual(["ubuntu-latest"], hosted["runs-on"])
        self.assertEqual(
            "${{ inputs.execution_backend == 'github-hosted' }}",
            hosted["if"],
        )
        self.assertEqual(
            ["linux", "amd64", "general", "small"],
            organization["runs-on"],
        )
        self.assertEqual(
            "${{ inputs.execution_backend != 'github-hosted' }}",
            organization["if"],
        )
        self.assertEqual(["plan", "plan_organization"], validate["needs"])
        self.assertEqual(
            "${{ always() && (needs.plan.result == 'success' || needs.plan_organization.result == 'success') }}",
            validate["if"],
        )
        self.assertEqual(
            "${{ fromJSON(needs.plan.outputs.runs_on_json || needs.plan_organization.outputs.runs_on_json) }}",
            validate["runs-on"],
        )

        for planner in (hosted, organization):
            backend = next(
                step for step in planner["steps"] if step.get("id") == "backend"
            )
            self.assertEqual(
                f"StreamScapeTV/ci-workflows/actions/resolve-execution-backend@{EXECUTION_BACKEND_SHA}",
                backend["uses"],
            )
            self.assertEqual("validation.gitops", backend["with"]["workflow_api"])
            self.assertEqual(
                "${{ inputs.execution_backend }}",
                backend["with"]["execution_backend"],
            )
            self.assertEqual(
                "${{ steps.plan.outputs.runner_profile }}",
                backend["with"]["runner_profile"],
            )
        self.assertIn("CI / GitOps validation", source)
        self.assertIn("if: always()", source)
        self.assertIn("Confirm zero Actions artifacts", source)

    def test_private_central_helpers_are_immutable_without_central_clone(self) -> None:
        source = (ROOT / ".github/workflows/reusable-gitops-validation.yml").read_text(
            encoding="utf-8"
        )
        workflow = yaml.safe_load(source)
        for forbidden in (
            "repository: StreamScapeTV/ci-workflows",
            "repository: ${{ job.workflow_repository }}",
            "ref: ${{ github.workflow_sha }}",
            "ref: ${{ job.workflow_sha }}",
            "path: .ciw",
            "./.ciw/actions/",
            "secrets: inherit",
        ):
            self.assertNotIn(forbidden, source)
        self.assertNotIn("actions/checkout@", source)

        remote = {
            str(step["uses"]).split("@", 1)[0]: str(step["uses"]).split("@", 1)[1]
            for job in workflow["jobs"].values()
            for step in job.get("steps", [])
            if str(step.get("uses", "")).startswith("StreamScapeTV/ci-workflows/actions/")
        }
        self.assertEqual(
            {
                "StreamScapeTV/ci-workflows/actions/validate-gitops": GITOPS_SHA,
                "StreamScapeTV/ci-workflows/actions/resolve-execution-backend": EXECUTION_BACKEND_SHA,
                "StreamScapeTV/ci-workflows/actions/exact-checkout": FOUNDATION_SHA,
                "StreamScapeTV/ci-workflows/actions/prepare-workspace": FOUNDATION_SHA,
                "StreamScapeTV/ci-workflows/actions/render-evidence": FOUNDATION_SHA,
                "StreamScapeTV/ci-workflows/actions/cleanup-workspace": FOUNDATION_SHA,
            },
            remote,
        )
        checkout = next(
            step
            for step in workflow["jobs"]["validate"]["steps"]
            if step.get("name")
            == "Check out exact admitted caller source with bounded GitOps history"
        )
        self.assertEqual("1000", checkout["with"]["fetch_depth"])

        locked = {
            item["uses"]: item
            for item in json.loads(
                (ROOT / "contracts/action-tool-lock.json").read_text(encoding="utf-8")
            )["third_party_actions"]
        }
        gitops = locked["StreamScapeTV/ci-workflows/actions/validate-gitops"]
        self.assertEqual(GITOPS_SHA, gitops["sha"])
        self.assertEqual("issue #125 immutable private-action checkpoint", gitops["release"])
        self.assertEqual("composite", gitops["runtime"])

    def test_smoke_is_exact_head_hosted_linux_and_zero_artifact(self) -> None:
        source = (ROOT / ".github/workflows/gitops-validation-smoke.yml").read_text()
        workflow = yaml.safe_load(source)
        self.assertEqual({"actions": "read", "contents": "read"}, workflow["permissions"])
        self.assertIn("github.event.pull_request.head.repo.full_name == github.repository", source)
        self.assertIn('test "$(git rev-parse HEAD)"', source)
        self.assertIn("full", source)
        self.assertIn("synthetic", source)
        self.assertIn("3.18.6", source)
        self.assertIn("5.8.1", source)
        self.assertIn("Verify GitOps smoke retained zero artifacts", source)
        self.assertNotIn("upload-artifact", source)
        self.assertNotIn("macOS", source)
        self.assertNotIn("runs-on: portable", source)
        self.assertNotIn("runs-on: [linux, amd64, general]", source)
        self.assertNotIn("runs-on: [linux, amd64, general, small]", source)
        self.assertEqual(["ubuntu-latest"], workflow["jobs"]["plan"]["runs-on"])
        self.assertEqual(["ubuntu-latest"], workflow["jobs"]["execute"]["runs-on"])
        self.assertEqual(["ubuntu-latest"], workflow["jobs"]["artifacts"]["runs-on"])
        self.assertIn("PLANNED_RUNNER_JSON", source)
        self.assertIn("needs.plan.outputs.runs_on_json", source)
        self.assertEqual(
            "${{ always() && !cancelled() && needs.plan.result != 'skipped' }}",
            workflow["jobs"]["artifacts"]["if"],
        )
        for job in workflow["jobs"].values():
            if "uses" not in job:
                self.assertGreater(job.get("timeout-minutes", 0), 0)

    def test_smoke_bootstraps_and_removes_locked_pyyaml_for_source_tests(self) -> None:
        workflow = yaml.safe_load(
            (ROOT / ".github/workflows/gitops-validation-smoke.yml").read_text()
        )
        steps = workflow["jobs"]["plan"]["steps"]
        by_name = {step["name"]: step for step in steps if "name" in step}
        bootstrap = by_name["Bootstrap locked PyYAML for focused source tests"]
        focused_tests = by_name["Run focused source package tests"]
        cleanup = by_name["Remove locked PyYAML test state"]
        self.assertIn("scripts/ci/bootstrap_validation_runtime.py", bootstrap["run"])
        self.assertIn("--lock contracts/action-tool-lock.json", bootstrap["run"])
        self.assertIn("gitops-smoke-pyyaml-${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}", bootstrap["run"])
        self.assertIn("GITOPS_SMOKE_PYTHONPATH", bootstrap["run"])
        self.assertIn("printf 'GITOPS_SMOKE_PYTHONPATH=%s\\n'", bootstrap["run"])
        self.assertNotIn("printf 'GITOPS_SMOKE_PYTHONPATH=%s\\\\n'", bootstrap["run"])
        self.assertIn('PYTHONPATH="${GITOPS_SMOKE_PYTHONPATH}:src"', focused_tests["run"])
        self.assertEqual("always()", cleanup["if"])
        self.assertIn("gitops-smoke-pyyaml-${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}", cleanup["run"])
        self.assertIn('test ! -e "${validation_root}"', cleanup["run"])

    def test_action_is_thin_and_rejects_authority_inputs(self) -> None:
        source = (ROOT / "actions/validate-gitops/action.yml").read_text()
        action = yaml.safe_load(source)
        self.assertEqual("composite", action["runs"]["using"])
        self.assertEqual(1, len(action["runs"]["steps"]))
        inputs = set(action["inputs"])
        self.assertEqual(
            {
                "phase",
                "admitted_sha",
                "validation_profile",
                "consumer_contract",
                "change_base_sha",
                "policy_script_profile",
                "artifact_exception_id",
            },
            inputs,
        )
        self.assertIn("scripts/ci/ciw.py", source)
        self.assertIn("gitops validate", source)
        self.assertNotIn("curl ", source)
        self.assertNotIn("helm ", source)
        self.assertNotIn("kubectl", source)
        self.assertNotIn("sops", source.lower())

    def test_fixtures_have_descriptive_positive_and_negative_matrix(self) -> None:
        payload = json.loads(
            (ROOT / "tests/fixtures/gitops-validation/cases.json").read_text()
        )
        self.assertGreaterEqual(len(payload["positive"]), 8)
        self.assertGreaterEqual(len(payload["negative"]), 10)
        self.assertTrue(
            (ROOT / "tests/fixtures/gitops-validation/negative/duplicate-key.yaml").is_file()
        )
        self.assertTrue(
            (ROOT / "tests/fixtures/gitops-validation/negative/plaintext-sops.yaml").is_file()
        )

    def test_docs_preserve_source_only_security_and_current_consumer_boundary(self) -> None:
        combined = (
            (ROOT / "docs/workflows/gitops-validation.md").read_text()
            + (ROOT / "docs/architecture/gitops-validation.md").read_text()
        ).lower()
        for required in (
            "validation.gitops",
            "source-only",
            "helm 3.18.6",
            "kustomize 5.8.1",
            "sops",
            "never decrypt",
            "zero routine artifacts",
            "portable",
            "flux",
            "iptv-backend",
            "agent-state",
            "immutable private",
            "1000",
        ):
            self.assertIn(required, combined)
        for forbidden in (
            "`sops decrypt`",
            "kubectl apply",
            "flux reconcile",
            "registry login",
            "runs-on: macos",
        ):
            self.assertNotIn(forbidden, combined)


if __name__ == "__main__":
    unittest.main()
