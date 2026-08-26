from __future__ import annotations

import json
import unittest
from pathlib import Path

import yaml

from ci_workflows.validation_model import ActionsLoader

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_PATH = ROOT / ".github/workflows/reusable-node.yml"
ACTION_PATH = ROOT / "actions/validate-node/action.yml"
PRIVATE_HELPERS = (
    "validate-node",
    "exact-checkout",
    "prepare-workspace",
    "render-evidence",
    "cleanup-workspace",
)


class NodeWorkflowContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.workflow_text = WORKFLOW_PATH.read_text(encoding="utf-8")
        cls.workflow = yaml.load(cls.workflow_text, Loader=ActionsLoader)
        cls.action_text = ACTION_PATH.read_text(encoding="utf-8")
        cls.action = yaml.load(cls.action_text, Loader=ActionsLoader)
        cls.node_contract = json.loads(
            (ROOT / "contracts/node-validation.json").read_text(encoding="utf-8")
        )
        public = json.loads(
            (ROOT / "contracts/public-workflows/validation.json").read_text(
                encoding="utf-8"
            )
        )
        cls.public_record = next(
            item
            for item in public["workflows"]
            if item["api_name"] == "validation.node"
        )

    def test_workflow_call_only_api_matches_the_implemented_record(self) -> None:
        self.assertEqual(set(self.workflow["on"]), {"workflow_call"})
        call = self.workflow["on"]["workflow_call"]
        self.assertEqual(
            set(call["inputs"]),
            {
                "execution_backend",
                "admitted_sha",
                "validation_profile",
                "version_file",
                "node_version",
                "working_directory",
                "install_profile",
                "command_profile",
                "script_path",
                "static_output_directory",
                "output_verifier_path",
                "public_environment",
                "artifact_exception_id",
            },
        )
        backend = call["inputs"]["execution_backend"]
        self.assertFalse(backend["required"])
        self.assertEqual(backend["default"], "organization")
        self.assertEqual(backend["type"], "string")
        self.assertEqual(call.get("secrets", {}), {})
        self.assertEqual(self.public_record["status"], "implemented")
        self.assertEqual(
            {item["name"] for item in self.public_record["inputs"]},
            set(call["inputs"]),
        )
        self.assertEqual(set(self.public_record["outputs"]), set(call["outputs"]))

    def test_backend_planners_are_mutually_exclusive_and_validation_uses_successful_output(self) -> None:
        jobs = self.workflow["jobs"]
        self.assertEqual(set(jobs), {"plan", "plan_organization", "validate"})
        hosted = jobs["plan"]
        organization = jobs["plan_organization"]
        validate = jobs["validate"]
        self.assertEqual(hosted["runs-on"], ["ubuntu-latest"])
        self.assertEqual(
            hosted["if"], "${{ inputs.execution_backend == 'github-hosted' }}"
        )
        self.assertEqual(
            organization["runs-on"], ["linux", "amd64", "general", "small"]
        )
        self.assertEqual(
            organization["if"], "${{ inputs.execution_backend != 'github-hosted' }}"
        )
        self.assertEqual(validate["needs"], ["plan", "plan_organization"])
        self.assertEqual(
            validate["runs-on"],
            "${{ fromJSON(needs.plan.outputs.runs_on_json || needs.plan_organization.outputs.runs_on_json) }}",
        )
        for planner in (hosted, organization):
            step = planner["steps"][0]
            self.assertEqual(
                step["uses"],
                "StreamScapeTV/ci-workflows/actions/validate-node@main",
            )
            self.assertEqual(step["with"]["phase"], "plan")
            self.assertEqual(
                step["with"]["execution_backend"],
                "${{ inputs.execution_backend }}",
            )

    def test_setup_node_uses_normal_upstream_release_and_disables_cache(self) -> None:
        validate_steps = self.workflow["jobs"]["validate"]["steps"]
        step = next(
            item
            for item in validate_steps
            if item.get("uses", "").startswith("actions/setup-node@")
        )
        self.assertEqual(step["uses"], "actions/setup-node@v6.5.0")
        self.assertEqual(
            step["with"]["node-version"],
            "${{ needs.plan.outputs.node_version || needs.plan_organization.outputs.node_version }}",
        )
        self.assertFalse(step["with"]["package-manager-cache"])

    def test_first_party_helpers_follow_main_and_preserve_order(self) -> None:
        validate_steps = self.workflow["jobs"]["validate"]["steps"]
        uses = [item.get("uses") for item in validate_steps if item.get("uses")]
        expected = [
            "StreamScapeTV/ci-workflows/actions/exact-checkout@main",
            "StreamScapeTV/ci-workflows/actions/prepare-workspace@main",
            "actions/setup-node@v6.5.0",
            "StreamScapeTV/ci-workflows/actions/validate-node@main",
            "StreamScapeTV/ci-workflows/actions/render-evidence@main",
            "StreamScapeTV/ci-workflows/actions/cleanup-workspace@main",
        ]
        positions = [uses.index(value) for value in expected]
        self.assertEqual(positions, sorted(positions))
        for helper in PRIVATE_HELPERS:
            self.assertIn(
                f"StreamScapeTV/ci-workflows/actions/{helper}@main",
                self.workflow_text,
            )
        self.assertNotRegex(
            self.workflow_text,
            r"StreamScapeTV/ci-workflows/actions/[^\s@]+@[0-9a-f]{40}",
        )
        execute = next(
            item
            for item in validate_steps
            if item.get("uses")
            == "StreamScapeTV/ci-workflows/actions/validate-node@main"
        )
        self.assertEqual(execute["with"]["phase"], "execute")
        cleanup = next(
            item
            for item in validate_steps
            if item.get("uses")
            == "StreamScapeTV/ci-workflows/actions/cleanup-workspace@main"
        )
        self.assertEqual(cleanup["if"], "always()")

    def test_private_central_repository_is_never_cloned(self) -> None:
        self.assertNotIn("actions/checkout@", self.workflow_text)
        self.assertNotIn("path: .ciw", self.workflow_text)
        self.assertNotIn("./.ciw/actions/", self.workflow_text)
        self.assertEqual(self.workflow.get("permissions"), {"contents": "read"})
        self.assertNotIn("private_dependency_token", self.workflow_text)
        self.assertNotIn("checkout_token", self.workflow_text)

    def test_exact_caller_source_is_still_verified_and_clean(self) -> None:
        self.assertIn(
            "uses: StreamScapeTV/ci-workflows/actions/exact-checkout@main",
            self.workflow_text,
        )
        self.assertIn("admitted_sha: ${{ inputs.admitted_sha }}", self.workflow_text)
        self.assertIn(
            'test "$(git rev-parse HEAD)" = "${{ inputs.admitted_sha }}"',
            self.workflow_text,
        )
        self.assertIn("git status --porcelain --untracked-files=all", self.workflow_text)

    def test_action_is_thin_and_has_no_generic_control_surface(self) -> None:
        self.assertEqual(self.action["runs"]["using"], "composite")
        self.assertEqual(len(self.action["runs"]["steps"]), 1)
        step = self.action["runs"]["steps"][0]
        self.assertIn("scripts/ci/ciw.py", step["run"])
        self.assertIn("node validate", step["run"])
        inputs = set(self.action["inputs"])
        forbidden = set(self.node_contract["forbidden_inputs"])
        self.assertTrue(inputs.isdisjoint(forbidden))

    def test_workflow_has_no_deployment_or_secret_surface(self) -> None:
        lowered = self.workflow_text.casefold()
        for token in (
            "secrets: inherit",
            "wrangler",
            "cloudflare/pages-action",
            "npm publish",
            "docker login",
            "kubectl",
            "kubeconfig",
        ):
            self.assertNotIn(token, lowered)
        self.assertEqual(self.workflow["permissions"], {"contents": "read"})


if __name__ == "__main__":
    unittest.main()
