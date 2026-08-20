from __future__ import annotations

import json
import unittest
from pathlib import Path

import yaml

from ci_workflows.validation_model import ActionsLoader


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/reusable-gradle-maven-publish.yml"
ACTION = ROOT / "actions/publish-gradle-maven/action.yml"


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

    def test_one_mobile_job_has_one_gradle_publication_step_and_terminal_cleanup(self) -> None:
        self.assertEqual(set(self.workflow["jobs"]), {"publish"})
        job = self.workflow["jobs"]["publish"]
        self.assertEqual(job["runs-on"], ["linux", "amd64", "mobile"])
        self.assertEqual(job["timeout-minutes"], 120)
        steps = job["steps"]
        ids = [step.get("id") for step in steps]
        self.assertEqual(ids.count("maven"), 1)
        self.assertEqual(ids.count("workspace"), 1)
        self.assertEqual(ids.count("workspace_cleanup"), 1)
        publish = next(step for step in steps if step.get("id") == "maven")
        self.assertEqual(publish["uses"], "./.ciw/actions/publish-gradle-maven")
        self.assertEqual(publish["with"]["arguments_json"], "${{ inputs.arguments_json }}")
        self.assertEqual(publish["with"]["expected_branch"], "${{ inputs.expected_branch }}")
        cleanup = next(step for step in steps if step.get("id") == "workspace_cleanup")
        self.assertEqual(cleanup["if"], "always()")
        self.assertNotIn("strategy", job)
        self.assertNotIn("concurrency", self.workflow)

    def test_publication_has_no_github_artifact_cache_or_oidc_transport(self) -> None:
        lowered = self.source.casefold()
        for forbidden in (
            "actions/cache",
            "upload-artifact",
            "download-artifact",
            "id-token",
            "secrets: inherit",
            "docker ",
            "helm ",
        ):
            self.assertNotIn(forbidden, lowered)
        self.assertEqual(self.workflow["permissions"], {"contents": "read"})

    def test_registry_secrets_reach_only_publication_action(self) -> None:
        steps = self.workflow["jobs"]["publish"]["steps"]
        publish = next(step for step in steps if step.get("id") == "maven")
        self.assertEqual(publish["with"]["registry_username"], "${{ secrets.registry_username }}")
        self.assertEqual(publish["with"]["registry_token"], "${{ secrets.registry_token }}")
        for step in steps:
            if step is publish:
                continue
            serialized = json.dumps(step)
            self.assertNotIn("registry_username", serialized)
            self.assertNotIn("registry_token", serialized)

    def test_action_is_thin_python_adapter(self) -> None:
        self.assertEqual(self.action["runs"]["using"], "composite")
        self.assertIn("gradle_maven_publish.py", self.action_source)
        self.assertNotIn("./gradlew", self.action_source)
        self.assertNotIn("git.faruqi.dev", self.action_source)


if __name__ == "__main__":
    unittest.main()
