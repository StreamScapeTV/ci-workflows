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
FOUNDATION_SHA = "70e08d4ddf8930046632a7135950e924b82e22bf"
FOUNDATION_RELEASE = "issue #116 immutable private-action checkpoint"
VALIDATE_NODE_SHA = "7d5d839c6e90491e165f1358ecb5e80129805764"
VALIDATE_NODE_RELEASE = "issue #405 simplified execution-backend checkpoint"
PRIVATE_HELPERS = {
    "validate-node": (VALIDATE_NODE_SHA, VALIDATE_NODE_RELEASE),
    "exact-checkout": (FOUNDATION_SHA, FOUNDATION_RELEASE),
    "prepare-workspace": (FOUNDATION_SHA, FOUNDATION_RELEASE),
    "render-evidence": (FOUNDATION_SHA, FOUNDATION_RELEASE),
    "cleanup-workspace": (FOUNDATION_SHA, FOUNDATION_RELEASE),
}


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
        cls.action_lock = json.loads(
            (ROOT / "contracts/action-tool-lock.json").read_text(encoding="utf-8")
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

    def test_planner_preserves_organization_capacity_and_execution_uses_bounded_output(self) -> None:
        jobs = self.workflow["jobs"]
        self.assertEqual(set(jobs), {"plan", "validate"})
        self.assertEqual(
            jobs["plan"]["runs-on"], ["linux", "amd64", "general", "small"]
        )
        self.assertEqual(
            jobs["validate"]["runs-on"],
            "${{ fromJSON(needs.plan.outputs.runs_on_json) }}",
        )
        self.assertEqual(
            jobs["plan"]["outputs"]["runs_on_json"],
            "${{ steps.plan.outputs.runs_on_json }}",
        )
        planner = jobs["plan"]["steps"][0]
        self.assertEqual(
            planner["with"]["execution_backend"],
            "${{ inputs.execution_backend }}",
        )
        self.assertEqual(jobs["validate"]["name"], "CI / Node validation")
        self.assertEqual(jobs["validate"]["timeout-minutes"], 90)
        self.assertNotIn("strategy", jobs["plan"])
        self.assertNotIn("strategy", jobs["validate"])
        self.assertNotIn("self-hosted", self.workflow_text)
        self.assertNotIn("runs-on: portable", self.workflow_text)
        self.assertNotIn("runs-on: [linux, amd64, general]", self.workflow_text)

    def test_setup_node_is_exact_locked_and_cache_disabled(self) -> None:
        setup = "actions/setup-node@249970729cb0ef3589644e2896645e5dc5ba9c38"
        self.assertEqual(self.workflow_text.count(setup), 1)
        validate_steps = self.workflow["jobs"]["validate"]["steps"]
        step = next(
            item
            for item in validate_steps
            if item.get("uses", "").startswith("actions/setup-node@")
        )
        self.assertEqual(step["uses"], setup)
        self.assertEqual(
            step["with"]["node-version"], "${{ needs.plan.outputs.node_version }}"
        )
        self.assertFalse(step["with"]["package-manager-cache"])
        entry = next(
            item
            for item in self.action_lock["third_party_actions"]
            if item["uses"] == "actions/setup-node"
        )
        self.assertEqual(
            entry["sha"], "249970729cb0ef3589644e2896645e5dc5ba9c38"
        )
        self.assertEqual(entry["release"], "v6.5.0")
        self.assertEqual(entry["runtime"], "node24")

    def test_private_helpers_are_exact_locked_and_ordered(self) -> None:
        locked = {
            item["uses"]: item
            for item in self.action_lock["third_party_actions"]
        }
        for helper, (sha, release) in PRIVATE_HELPERS.items():
            uses = f"StreamScapeTV/ci-workflows/actions/{helper}"
            self.assertIn(uses, locked)
            self.assertEqual(sha, locked[uses]["sha"])
            self.assertEqual(release, locked[uses]["release"])
            self.assertEqual("composite", locked[uses]["runtime"])

        plan_steps = self.workflow["jobs"]["plan"]["steps"]
        self.assertEqual(len(plan_steps), 1)
        plan = plan_steps[0]
        self.assertEqual(
            plan["uses"],
            f"StreamScapeTV/ci-workflows/actions/validate-node@{VALIDATE_NODE_SHA}",
        )
        self.assertEqual(plan["with"]["phase"], "plan")
        self.assertEqual(
            plan["with"]["execution_backend"],
            "${{ inputs.execution_backend }}",
        )

        validate_steps = self.workflow["jobs"]["validate"]["steps"]
        uses = [item.get("uses") for item in validate_steps if item.get("uses")]
        expected = [
            f"StreamScapeTV/ci-workflows/actions/exact-checkout@{FOUNDATION_SHA}",
            f"StreamScapeTV/ci-workflows/actions/prepare-workspace@{FOUNDATION_SHA}",
            "actions/setup-node@249970729cb0ef3589644e2896645e5dc5ba9c38",
            f"StreamScapeTV/ci-workflows/actions/validate-node@{VALIDATE_NODE_SHA}",
            f"StreamScapeTV/ci-workflows/actions/render-evidence@{FOUNDATION_SHA}",
            f"StreamScapeTV/ci-workflows/actions/cleanup-workspace@{FOUNDATION_SHA}",
        ]
        positions = [uses.index(value) for value in expected]
        self.assertEqual(positions, sorted(positions))
        execute = next(
            item
            for item in validate_steps
            if item.get("uses")
            == f"StreamScapeTV/ci-workflows/actions/validate-node@{VALIDATE_NODE_SHA}"
        )
        self.assertEqual(execute["with"]["phase"], "execute")
        self.assertEqual(
            execute["with"]["execution_backend"],
            "${{ inputs.execution_backend }}",
        )
        cleanup = next(
            item
            for item in validate_steps
            if item.get("uses")
            == f"StreamScapeTV/ci-workflows/actions/cleanup-workspace@{FOUNDATION_SHA}"
        )
        self.assertEqual(cleanup["if"], "always()")

    def test_private_central_repository_is_never_cloned(self) -> None:
        self.assertNotIn("actions/checkout@", self.workflow_text)
        self.assertNotIn("repository: ${{ job.workflow_repository }}", self.workflow_text)
        self.assertNotIn("ref: ${{ job.workflow_sha }}", self.workflow_text)
        self.assertNotIn("path: .ciw", self.workflow_text)
        self.assertNotIn("./.ciw/actions/", self.workflow_text)
        for helper, (sha, _) in PRIVATE_HELPERS.items():
            self.assertIn(
                f"StreamScapeTV/ci-workflows/actions/{helper}@{sha}",
                self.workflow_text,
            )
        self.assertEqual(self.workflow.get("permissions"), {"contents": "read"})
        self.assertNotIn("private_dependency_token", self.workflow_text)
        self.assertNotIn("checkout_token", self.workflow_text)

    def test_exact_caller_source_is_still_verified_and_clean(self) -> None:
        self.assertIn(
            f"uses: StreamScapeTV/ci-workflows/actions/exact-checkout@{FOUNDATION_SHA}",
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
        self.assertIn("--phase", step["run"])
        self.assertEqual(self.action["inputs"]["execution_backend"]["default"], "organization")
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

    def test_docs_record_api_runtime_backend_output_and_cleanup(self) -> None:
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
            "immutable private",
            "execution_backend",
            "github-hosted",
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
            FOUNDATION_SHA,
        ):
            self.assertIn(token, architecture)
        self.assertRegex(
            architecture, re.compile(r"Cloudflare Pages Git deployment", re.I)
        )


if __name__ == "__main__":
    unittest.main()
