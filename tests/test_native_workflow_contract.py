from __future__ import annotations

import unittest
from pathlib import Path

import yaml

from ci_workflows.validation_model import ActionsLoader

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_PATH = ROOT / ".github/workflows/reusable-native.yml"
ACTION_PATH = ROOT / "actions/validate-native/action.yml"
FOUNDATION_SHA = "70e08d4ddf8930046632a7135950e924b82e22bf"
NATIVE_ACTION_SHA = "7273bf28d5e4b9fc3bd6bcb8c6b685a5ff7c1f88"


class NativeWorkflowContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.workflow_text = WORKFLOW_PATH.read_text(encoding="utf-8")
        cls.workflow = yaml.load(cls.workflow_text, Loader=ActionsLoader)
        cls.action_text = ACTION_PATH.read_text(encoding="utf-8")
        cls.action = yaml.load(cls.action_text, Loader=ActionsLoader)

    def test_workflow_call_is_product_neutral_and_bounded(self) -> None:
        self.assertEqual(set(self.workflow["on"]), {"workflow_call"})
        call = self.workflow["on"]["workflow_call"]
        self.assertEqual(
            set(call["inputs"]),
            {"admitted_sha", "working_directory", "validation_plan_json"},
        )
        self.assertEqual(set(call["outputs"]), {"result", "test_summary", "cleanup_result"})
        self.assertEqual(call.get("secrets", {}), {})
        self.assertEqual(self.workflow["permissions"], {"contents": "read"})
        self.assertEqual(
            self.workflow["jobs"]["validate"]["name"],
            "CI / Native CMake validation",
        )
        forbidden = {
            "runner",
            "runs_on",
            "runner_labels",
            "container_engine",
            "cache_key",
            "registry",
            "registry_token",
            "product_id",
            "repository_script",
            "arbitrary_command",
        }
        self.assertTrue(set(call["inputs"]).isdisjoint(forbidden))

    def test_one_general_small_job_reuses_checkout_and_workspace(self) -> None:
        jobs = self.workflow["jobs"]
        self.assertEqual(set(jobs), {"validate"})
        validate = jobs["validate"]
        self.assertEqual(validate["runs-on"], ["linux", "amd64", "general", "small"])
        self.assertEqual(validate["timeout-minutes"], 120)
        self.assertEqual(validate["name"], "CI / Native CMake validation")
        self.assertEqual(
            self.workflow_text.count(
                f"uses: StreamScapeTV/ci-workflows/actions/exact-checkout@{FOUNDATION_SHA}"
            ),
            1,
        )
        self.assertEqual(
            self.workflow_text.count(
                f"uses: StreamScapeTV/ci-workflows/actions/prepare-workspace@{FOUNDATION_SHA}"
            ),
            1,
        )
        self.assertEqual(
            self.workflow_text.count(
                f"uses: StreamScapeTV/ci-workflows/actions/validate-native@{NATIVE_ACTION_SHA}"
            ),
            1,
        )
        self.assertIn("profile: native", self.workflow_text)
        self.assertIn("cache_mode: disabled", self.workflow_text)

    def test_runner_tool_probe_precedes_source_execution_without_installing_packages(self) -> None:
        validate = self.workflow["jobs"]["validate"]
        steps = validate["steps"]
        cmake_index = next(
            index for index, step in enumerate(steps) if step.get("name") == "Verify runner-provided CMake"
        )
        checkout_index = next(
            index for index, step in enumerate(steps) if step.get("name") == "Check out exact admitted caller source"
        )
        self.assertLess(cmake_index, checkout_index)
        probe = steps[cmake_index]["run"]
        self.assertIn("type -P cmake", probe)
        for token in ("apt-get", "apt ", "sudo ", "brew ", "dnf ", "yum "):
            self.assertNotIn(token, probe)

    def test_cleanup_and_clean_tree_are_terminal_even_after_failure(self) -> None:
        validate = self.workflow["jobs"]["validate"]
        cleanup = next(step for step in validate["steps"] if step.get("id") == "cleanup")
        clean = next(step for step in validate["steps"] if step.get("id") == "clean")
        self.assertEqual(cleanup["if"], "always()")
        self.assertEqual(clean["if"], "always()")
        self.assertIn("git rev-parse HEAD", clean["run"])
        self.assertIn("git status --porcelain --untracked-files=all", clean["run"])
        result = validate["outputs"]["result"]
        self.assertIn("steps.native.outcome", result)
        self.assertIn("steps.cleanup.outcome", result)
        self.assertIn("steps.clean.outcome", result)
        self.assertEqual(
            validate["outputs"]["cleanup_result"],
            "${{ steps.cleanup.outcome == 'success' && 'success' || 'failure' }}",
        )

    def test_no_actions_cache_artifacts_or_security_publication_surface(self) -> None:
        text = self.workflow_text + "\n" + self.action_text
        for token in (
            "actions/cache",
            "actions/upload-artifact",
            "actions/download-artifact",
            "id-token:",
            "packages: write",
            "attest",
            "cosign",
            "provenance",
            "docker ",
            "buildah ",
            "podman ",
        ):
            self.assertNotIn(token, text.lower())

    def test_action_is_thin_and_caller_cannot_supply_arbitrary_commands(self) -> None:
        self.assertEqual(self.action["runs"]["using"], "composite")
        self.assertEqual(len(self.action["runs"]["steps"]), 1)
        self.assertEqual(
            set(self.action["inputs"]),
            {"admitted_sha", "working_directory", "validation_plan_json"},
        )
        script = self.action["runs"]["steps"][0]["run"]
        self.assertIn("scripts/ci/ciw.py", script)
        self.assertIn("native validate", script)
        self.assertIn("type -P python3", script)
        self.assertNotIn("eval ", script)
        self.assertNotIn("bash -c", script)
        self.assertNotIn("sh -c", script)
        self.assertTrue(
            set(self.action["inputs"]).isdisjoint(
                {"command", "script", "callback", "runner", "runs_on", "shell"}
            )
        )


if __name__ == "__main__":
    unittest.main()
