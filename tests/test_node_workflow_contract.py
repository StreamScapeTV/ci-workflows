from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

import yaml

from ci_workflows.validation_model import ActionsLoader

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_PATH = ROOT / ".github/workflows/reusable-node.yml"
ACTION_PATH = ROOT / "actions/validate-node/action.yml"


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
        self.assertEqual(
            set(call["outputs"]),
            {
                "result",
                "node_version",
                "npm_version",
                "install_result",
                "test_summary",
                "build_result",
                "output_verified",
                "output_digest",
                "clean_tree",
                "cleanup_result",
                "artifact_exception_used",
                "evidence_id",
            },
        )
        self.assertEqual(call.get("secrets", {}), {})
        self.assertEqual(self.public_record["status"], "implemented")
        self.assertEqual(self.public_record["api_version"], "1.0.0")
        self.assertEqual(self.public_record["stable_check_name"], "CI / Node validation")
        self.assertEqual(
            {item["name"] for item in self.public_record["inputs"]},
            set(call["inputs"]),
        )
        self.assertEqual(set(self.public_record["outputs"]), set(call["outputs"]))

    def test_jobs_use_only_general_linux_capabilities_and_no_caller_matrix(self) -> None:
        jobs = self.workflow["jobs"]
        self.assertEqual(set(jobs), {"plan", "validate"})
        self.assertEqual(
            jobs["plan"]["runs-on"], ["linux", "amd64", "general"]
        )
        self.assertEqual(
            jobs["validate"]["runs-on"], ["linux", "amd64", "general"]
        )
        self.assertEqual(jobs["validate"]["name"], "CI / Node validation")
        self.assertEqual(jobs["validate"]["timeout-minutes"], 90)
        self.assertNotIn("strategy", jobs["plan"])
        self.assertNotIn("strategy", jobs["validate"])
        self.assertNotIn("self-hosted", self.workflow_text)
        self.assertNotIn("runs-on: portable", self.workflow_text)
        self.assertNotIn("fromJSON(needs.plan.outputs.runs_on", self.workflow_text)

    def test_setup_node_is_exact_locked_and_cache_disabled(self) -> None:
        setup = "actions/setup-node@249970729cb0ef3589644e2896645e5dc5ba9c38"
        self.assertEqual(self.workflow_text.count(setup), 1)
        validate_steps = self.workflow["jobs"]["validate"]["steps"]
        step = next(item for item in validate_steps if item.get("uses", "").startswith("actions/setup-node@"))
        self.assertEqual(step["uses"], setup)
        self.assertEqual(step["with"]["node-version"], "${{ needs.plan.outputs.node_version }}")
        self.assertFalse(step["with"]["package-manager-cache"])
        lock = json.loads(
            (ROOT / "contracts/action-tool-lock.json").read_text(encoding="utf-8")
        )
        entry = next(
            item
            for item in lock["third_party_actions"]
            if item["uses"] == "actions/setup-node"
        )
        self.assertEqual(entry["sha"], "249970729cb0ef3589644e2896645e5dc5ba9c38")
        self.assertEqual(entry["release"], "v6.5.0")
        self.assertEqual(entry["runtime"], "node24")

    def test_plan_and_execute_phases_are_separate_and_ordered(self) -> None:
        plan_steps = self.workflow["jobs"]["plan"]["steps"]
        plan = next(item for item in plan_steps if item.get("uses") == "./.ciw/actions/validate-node")
        self.assertEqual(plan["with"]["phase"], "plan")
        validate_steps = self.workflow["jobs"]["validate"]["steps"]
        uses = [item.get("uses") for item in validate_steps if item.get("uses")]
        expected = [
            "./.ciw/actions/exact-checkout",
            "./.ciw/actions/prepare-workspace",
            "actions/setup-node@249970729cb0ef3589644e2896645e5dc5ba9c38",
            "./.ciw/actions/validate-node",
            "./.ciw/actions/render-evidence",
            "./.ciw/actions/cleanup-workspace",
        ]
        positions = [uses.index(value) for value in expected]
        self.assertEqual(positions, sorted(positions))
        execute = next(item for item in validate_steps if item.get("uses") == "./.ciw/actions/validate-node")
        self.assertEqual(execute["with"]["phase"], "execute")
        cleanup = next(item for item in validate_steps if item.get("uses") == "./.ciw/actions/cleanup-workspace")
        self.assertEqual(cleanup["if"], "always()")

    def test_exact_central_and_caller_source_are_verified(self) -> None:
        self.assertEqual(self.workflow_text.count("repository: ${{ job.workflow_repository }}"), 2)
        self.assertEqual(self.workflow_text.count("ref: ${{ job.workflow_sha }}"), 2)
        self.assertEqual(self.workflow_text.count("EXPECTED_REPOSITORY: ${{ job.workflow_repository }}"), 2)
        self.assertEqual(self.workflow_text.count("EXPECTED_SHA: ${{ job.workflow_sha }}"), 2)
        self.assertEqual(self.workflow_text.count("persist-credentials: false"), 2)
        self.assertEqual(self.workflow_text.count("set-safe-directory: false"), 2)
        self.assertNotIn("github.workflow_sha", self.workflow_text)
        self.assertNotIn("GITHUB_WORKFLOW_SHA", self.workflow_text)
        self.assertIn("uses: ./.ciw/actions/exact-checkout", self.workflow_text)
        self.assertIn("admitted_sha: ${{ inputs.admitted_sha }}", self.workflow_text)
        self.assertIn('test "$(git rev-parse HEAD)" = "${{ inputs.admitted_sha }}"', self.workflow_text)
        self.assertIn("git status --porcelain --untracked-files=all", self.workflow_text)

    def test_action_is_thin_and_has_no_generic_control_surface(self) -> None:
        self.assertEqual(self.action["runs"]["using"], "composite")
        self.assertEqual(len(self.action["runs"]["steps"]), 1)
        step = self.action["runs"]["steps"][0]
        self.assertIn("scripts/ci/ciw.py", step["run"])
        self.assertIn("node validate", step["run"])
        self.assertIn("--phase", step["run"])
        inputs = set(self.action["inputs"])
        forbidden = set(self.node_contract["forbidden_inputs"])
        self.assertTrue(inputs.isdisjoint(forbidden))
        for token in ("eval ", "source ", "curl ", "npm install", "wrangler", "docker "):
            self.assertNotIn(token, step["run"].casefold())

    def test_workflow_has_no_artifact_deployment_or_secret_surface(self) -> None:
        lowered = self.workflow_text.casefold()
        for token in (
            "upload-artifact",
            "download-artifact",
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

    def test_docs_record_api_runtime_environment_output_and_cleanup(self) -> None:
        workflow_doc = (ROOT / "docs/workflows/node.md").read_text(encoding="utf-8")
        architecture = (
            ROOT / "docs/architecture/node-validation.md"
        ).read_text(encoding="utf-8")
        for token in (
            "validation.node",
            ".github/workflows/reusable-node.yml",
            "CI / Node validation",
            "next-static-export",
            "node-source-audit",
            "npm ci --no-audit --no-fund",
            "NEXT_PUBLIC_API_BASE_URL",
            "zero",
        ):
            self.assertIn(token, workflow_doc)
        for token in (
            "actions/setup-node@249970729cb0ef3589644e2896645e5dc5ba9c38",
            "ciw node validate",
            "lockfile version 3",
            "symlink",
            "Worker",
            "descriptor-anchored",
        ):
            self.assertIn(token, architecture)
        self.assertRegex(architecture, re.compile(r"Cloudflare Pages Git deployment", re.I))


if __name__ == "__main__":
    unittest.main()
