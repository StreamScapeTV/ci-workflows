from __future__ import annotations

import unittest
from pathlib import Path

import yaml

from ci_workflows.validation_model import ActionsLoader

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_PATH = ROOT / ".github/workflows/reusable-static-web.yml"
ACTION_PATH = ROOT / "actions/validate-static-web/action.yml"
DOC_PATH = ROOT / "docs/workflows/static-web.md"


class StaticWebWorkflowContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.workflow_text = WORKFLOW_PATH.read_text(encoding="utf-8")
        cls.workflow = yaml.load(cls.workflow_text, Loader=ActionsLoader)
        cls.action_text = ACTION_PATH.read_text(encoding="utf-8")
        cls.action = yaml.load(cls.action_text, Loader=ActionsLoader)
        cls.docs = DOC_PATH.read_text(encoding="utf-8")

    def test_public_api_is_workflow_call_only_and_product_neutral(self) -> None:
        self.assertEqual(set(self.workflow["on"]), {"workflow_call"})
        call = self.workflow["on"]["workflow_call"]
        self.assertEqual(
            set(call["inputs"]),
            {"admitted_sha", "working_directory", "validation_plan_json"},
        )
        self.assertEqual(call.get("secrets", {}), {})
        self.assertEqual(
            set(call["outputs"]),
            {
                "result",
                "build_result",
                "output_verified",
                "output_digest",
                "output_file_count",
                "test_summary",
                "cleanup_result",
                "failure_code",
            },
        )
        for forbidden in (
            "runner",
            "runner_labels",
            "cache",
            "container_engine",
            "registry",
            "secret_name",
            "deployment_target",
            "namespace",
            "framework",
        ):
            self.assertNotIn(forbidden, call["inputs"])

    def test_one_general_job_reuses_one_checkout_and_workspace(self) -> None:
        jobs = self.workflow["jobs"]
        self.assertEqual(set(jobs), {"validate"})
        validate = jobs["validate"]
        self.assertEqual(validate["name"], "CI / Static-web validation")
        self.assertEqual(
            validate["runs-on"],
            ["linux", "amd64", "general", "small"],
        )
        self.assertEqual(validate["timeout-minutes"], 60)
        self.assertEqual(
            self.workflow_text.count(
                "StreamScapeTV/ci-workflows/actions/exact-checkout@main"
            ),
            1,
        )
        self.assertEqual(
            self.workflow_text.count(
                "StreamScapeTV/ci-workflows/actions/prepare-workspace@main"
            ),
            1,
        )
        self.assertNotIn("runs-on: portable", self.workflow_text)
        self.assertNotIn("self-hosted", self.workflow_text)

    def test_private_helpers_follow_main_and_sequence_is_cleanup_safe(self) -> None:
        source = self.workflow_text
        sequence = [
            "uses: StreamScapeTV/ci-workflows/actions/exact-checkout@main",
            "uses: StreamScapeTV/ci-workflows/actions/prepare-workspace@main",
            "uses: StreamScapeTV/ci-workflows/actions/validate-static-web@main",
            "uses: StreamScapeTV/ci-workflows/actions/cleanup-workspace@main",
            "name: Verify exact source remained clean after cleanup",
        ]
        positions = [source.index(item) for item in sequence]
        self.assertEqual(positions, sorted(positions))
        steps = self.workflow["jobs"]["validate"]["steps"]
        cleanup = next(step for step in steps if step.get("id") == "cleanup")
        clean = next(step for step in steps if step.get("id") == "clean")
        self.assertEqual(cleanup["if"], "always()")
        self.assertEqual(clean["if"], "always()")
        self.assertIn("git rev-parse HEAD", clean["run"])
        self.assertIn("git status --porcelain", clean["run"])
        result = self.workflow["jobs"]["validate"]["outputs"]["result"]
        self.assertIn("steps.static_web.outcome", result)
        self.assertIn("steps.cleanup.outcome", result)
        self.assertIn("steps.clean.outcome", result)

    def test_no_cache_or_security_expansion(self) -> None:
        source = self.workflow_text + "\n" + self.action_text
        for forbidden in (
            "actions/cache",
            "setup-buildx",
            "attestation",
            "provenance",
            "id-token:",
            "secrets: inherit",
            "workflow_dispatch",
            "schedule:",
        ):
            self.assertNotIn(forbidden, source)
        self.assertIn("cache_mode: disabled", self.workflow_text)
        self.assertEqual(self.workflow["permissions"], {"contents": "read"})

    def test_action_is_thin_and_dispatches_shared_ciw_registry(self) -> None:
        self.assertEqual(self.action["runs"]["using"], "composite")
        self.assertEqual(len(self.action["runs"]["steps"]), 1)
        step = self.action["runs"]["steps"][0]
        script = step["run"]
        self.assertEqual(
            set(self.action["inputs"]),
            {"admitted_sha", "working_directory", "validation_plan_json"},
        )
        self.assertIn("type -P python3.12", script)
        self.assertIn("type -P python3", script)
        self.assertIn("scripts/ci/ciw.py", script)
        self.assertIn("static-web validate", script)
        self.assertNotIn("-m ci_workflows.ciw_web", script)
        self.assertIn('GITHUB_WORKSPACE="${GITHUB_WORKSPACE}/source"', script)
        self.assertNotIn("eval ", script)
        self.assertNotIn("bash -c", script)
        self.assertNotIn("sh -c", script)
        self.assertNotIn("curl ", script)
        self.assertNotIn("npm ", script)
        self.assertNotIn("yarn ", script)
        self.assertNotIn("pnpm ", script)

    def test_docs_keep_build_and_framework_behavior_caller_owned(self) -> None:
        for expected in (
            "validation.static-web",
            "build_script_path",
            "static_output_directory",
            "expected_files",
            "verification_script_path",
            "CIW_STATIC_OUTPUT_DIRECTORY",
            "GitHub Actions cache",
            "[linux, amd64, general, small]",
        ):
            self.assertIn(expected, self.docs)
        self.assertIn("framework configuration", self.docs)
        self.assertIn("caller", self.docs.lower())


if __name__ == "__main__":
    unittest.main()
