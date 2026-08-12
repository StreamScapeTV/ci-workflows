from __future__ import annotations

import json
import unittest
from pathlib import Path

import yaml

from ci_workflows.validation_model import ActionsLoader

ROOT = Path(__file__).resolve().parents[1]
CHECKOUT = "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1"


class MaintenanceWorkflowContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        operations = json.loads((ROOT / "contracts/public-workflows/operations.json").read_text(encoding="utf-8"))
        cls.records = {row["api_name"]: row for row in operations["workflows"]}
        permissions = json.loads((ROOT / "contracts/permission-profiles.json").read_text(encoding="utf-8"))
        cls.permissions = {row["id"]: row for row in permissions["profiles"]}

    def test_four_maintenance_workflows_match_reviewed_public_api_without_new_control_surface(self) -> None:
        apis = (
            "maintenance.artifacts",
            "maintenance.branches",
            "maintenance.conformance",
            "maintenance.runner-retry",
        )
        for api in apis:
            with self.subTest(api=api):
                record = self.records[api]
                path = ROOT / record["file"]
                text = path.read_text(encoding="utf-8")
                workflow = yaml.load(text, Loader=ActionsLoader)
                self.assertEqual(set(workflow["on"]), {"workflow_call"})
                call = workflow["on"]["workflow_call"]
                self.assertEqual(set(call["inputs"]), {row["name"] for row in record["inputs"]})
                self.assertEqual(set(call.get("secrets", {})), set(record["secrets"]))
                self.assertEqual(set(call["outputs"]), set(record["outputs"]))
                self.assertEqual(workflow["permissions"], self.permissions[record["permission_profile"]]["workflow_permissions"])
                self.assertEqual(len(workflow["jobs"]), 1)
                job = next(iter(workflow["jobs"].values()))
                self.assertEqual(job["runs-on"], ["linux", "amd64", "general"])
                self.assertLessEqual(job["timeout-minutes"], record["timeout_minutes"])
                self.assertEqual(text.count(CHECKOUT), 1)
                self.assertIn("persist-credentials: false", text)
                self.assertNotIn("secrets: inherit", text)
                self.assertNotIn("self-hosted", text)
                for forbidden in ("arbitrary_command", "callback_url", "runner_labels", "force-push"):
                    self.assertNotIn(forbidden, text)

    def test_dry_run_remains_default_for_every_mutating_maintenance_api(self) -> None:
        for api in (
            "maintenance.artifacts",
            "maintenance.branches",
            "maintenance.conformance",
            "maintenance.runner-retry",
        ):
            record = self.records[api]
            workflow = yaml.load((ROOT / record["file"]).read_text(encoding="utf-8"), Loader=ActionsLoader)
            self.assertIs(workflow["on"]["workflow_call"]["inputs"]["dry_run"]["default"], True)

    def test_composite_actions_are_thin_and_do_not_select_repositories_or_runners(self) -> None:
        for name in ("maintenance-artifacts", "maintenance-branches", "maintenance-conformance", "maintenance-runner-retry"):
            path = ROOT / f"actions/{name}/action.yml"
            action = yaml.load(path.read_text(encoding="utf-8"), Loader=ActionsLoader)
            self.assertEqual(action["runs"]["using"], "composite")
            self.assertEqual(len(action["runs"]["steps"]), 1)
            text = path.read_text(encoding="utf-8")
            self.assertIn("scripts/ci/maintenance.py", text)
            self.assertNotIn("runs-on", text)
            self.assertNotIn("repository_url", text)
            self.assertNotIn("shell_command", text)

    def test_focused_smoke_is_unprivileged_exact_head_and_zero_artifact(self) -> None:
        path = ROOT / ".github/workflows/maintenance-contract-smoke.yml"
        text = path.read_text(encoding="utf-8")
        workflow = yaml.load(text, Loader=ActionsLoader)
        self.assertEqual(set(workflow["on"]), {"pull_request"})
        self.assertEqual(workflow["permissions"], {"actions": "read", "contents": "read"})
        job = workflow["jobs"]["focused"]
        self.assertEqual(job["runs-on"], ["linux", "amd64", "general"])
        self.assertIn("github.event.pull_request.head.sha", text)
        self.assertIn("zero Actions artifacts", text)
        self.assertNotIn("secrets.", text)


if __name__ == "__main__":
    unittest.main()
