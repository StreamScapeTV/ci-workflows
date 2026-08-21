from __future__ import annotations

import json
from pathlib import Path
import re
import unittest

import yaml

from ci_workflows.validation_model import ActionsLoader


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/reusable-gradle-maven-publish.yml"
ACTION = ROOT / "actions/publish-gradle-maven/action.yml"
ACTION_LOCK = ROOT / "contracts/action-tool-lock.json"
PUBLISH_ACTION = "StreamScapeTV/ci-workflows/actions/publish-gradle-maven"
PUBLISH_SHA = "af95ac60ec2751897765178b7006caadc3903b88"
SHA = re.compile(r"^StreamScapeTV/ci-workflows/actions/[a-z0-9-]+@[0-9a-f]{40}$")


class GradleMavenPublishWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = WORKFLOW.read_text(encoding="utf-8")
        cls.workflow = yaml.load(cls.source, Loader=ActionsLoader)
        cls.action_source = ACTION.read_text(encoding="utf-8")
        cls.action = yaml.safe_load(cls.action_source)

    def test_public_surface_is_bounded_and_registry_host_is_not_caller_input(self) -> None:
        call = self.workflow["on"]["workflow_call"]
        self.assertEqual(
            set(call["inputs"]),
            {
                "admitted_sha",
                "expected_branch",
                "working_directory",
                "gradle_wrapper_path",
                "version_file",
                "arguments_json",
            },
        )
        self.assertEqual(set(call["secrets"]), {"registry_username", "registry_token"})
        self.assertEqual(set(call["outputs"]), {"result", "release_version"})
        for forbidden in (
            "registry_host",
            "registry_url",
            "publication_channel",
            "runner",
            "runs_on",
            "shell",
            "command",
        ):
            self.assertNotIn(forbidden, call["inputs"])

    def test_one_mobile_job_runs_one_gradle_publication_with_terminal_cleanup(self) -> None:
        self.assertEqual(set(self.workflow["jobs"]), {"publish"})
        job = self.workflow["jobs"]["publish"]
        self.assertEqual(job["runs-on"], ["linux", "amd64", "mobile"])
        self.assertEqual(job["timeout-minutes"], 120)
        self.assertNotIn("strategy", job)
        self.assertNotIn("concurrency", self.workflow)
        steps = {step.get("id"): step for step in job["steps"]}
        self.assertEqual(
            set(steps),
            {
                "checkout",
                "workspace",
                "maven_plan",
                "maven",
                "source_cleanup",
                "source_residue",
                "workspace_cleanup",
                "terminal",
            },
        )
        self.assertEqual(job["outputs"]["release_version"], "${{ steps.maven.outputs.release_version }}")
        self.assertTrue(SHA.fullmatch(steps["checkout"]["uses"]))
        self.assertTrue(SHA.fullmatch(steps["workspace"]["uses"]))
        self.assertTrue(SHA.fullmatch(steps["workspace_cleanup"]["uses"]))
        for step_id in ("maven_plan", "maven", "source_cleanup", "source_residue"):
            self.assertTrue(SHA.fullmatch(steps[step_id]["uses"]))
            self.assertIn("publish-gradle-maven", steps[step_id]["uses"])
        self.assertEqual(steps["maven"]["with"]["phase"], "execute")
        self.assertEqual(steps["maven_plan"]["with"]["phase"], "plan")
        self.assertEqual(steps["source_cleanup"]["with"]["phase"], "cleanup")
        self.assertEqual(steps["source_residue"]["with"]["phase"], "residue")
        self.assertIn("always()", steps["source_cleanup"]["if"])
        self.assertIn("always()", steps["workspace_cleanup"]["if"])
        self.assertIn("SOURCE_CLEANUP_OUTCOME", steps["terminal"]["env"])
        self.assertIn("SOURCE_RESIDUE_OUTCOME", steps["terminal"]["env"])

    def test_publication_has_no_artifacts_cache_oidc_or_central_source_checkout(self) -> None:
        lowered = self.source.casefold()
        for forbidden in (
            "actions/cache",
            "upload-artifact",
            "download-artifact",
            "id-token",
            "secrets: inherit",
            "actions/checkout",
            "job.workflow_repository",
            "job.workflow_sha",
            ".ciw/",
        ):
            self.assertNotIn(forbidden, lowered)
        self.assertEqual(self.workflow["permissions"], {"contents": "read"})

    def test_registry_secrets_reach_only_execute_phase(self) -> None:
        steps = self.workflow["jobs"]["publish"]["steps"]
        execute = next(step for step in steps if step.get("id") == "maven")
        self.assertEqual(execute["with"]["registry_username"], "${{ secrets.registry_username }}")
        self.assertEqual(execute["with"]["registry_token"], "${{ secrets.registry_token }}")
        for step in steps:
            if step is execute:
                continue
            serialized = json.dumps(step)
            self.assertNotIn("registry_username", serialized)
            self.assertNotIn("registry_token", serialized)

    def test_publication_action_checkpoint_is_locked(self) -> None:
        lock = json.loads(ACTION_LOCK.read_text(encoding="utf-8"))
        entry = next(
            item
            for item in lock["third_party_actions"]
            if item["uses"] == PUBLISH_ACTION
        )
        self.assertEqual(entry["sha"], PUBLISH_SHA)
        self.assertEqual(entry["release"], "issue #418 immutable action checkpoint")
        self.assertEqual(entry["runtime"], "composite")
        self.assertEqual(
            entry["source"],
            f"https://github.com/StreamScapeTV/ci-workflows/tree/{PUBLISH_SHA}/actions/publish-gradle-maven",
        )

    def test_action_is_a_phase_adapter_without_registry_endpoint_or_gradle_shell(self) -> None:
        self.assertEqual(self.action["runs"]["using"], "composite")
        self.assertEqual(self.action["inputs"]["phase"]["default"], "execute")
        self.assertIn("gradle_maven_publish.py", self.action_source)
        self.assertNotIn("./gradlew", self.action_source)
        self.assertNotIn("git.faruqi.dev", self.action_source)
        self.assertNotIn("registry_url", self.action_source)


if __name__ == "__main__":
    unittest.main()
