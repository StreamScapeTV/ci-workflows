from __future__ import annotations

import json
import unittest
from pathlib import Path

import yaml

from ci_workflows.validation_model import ActionsLoader

ROOT = Path(__file__).resolve().parents[1]
HELPER_SHA = "a464a023cf654d00172d62ebd1700c51ce2c75bb"
HELPERS = {
    "maintenance.artifacts": ("maintenance-artifacts", HELPER_SHA),
    "maintenance.branches": ("maintenance-branches", HELPER_SHA),
    "maintenance.conformance": ("maintenance-conformance", HELPER_SHA),
    "maintenance.runner-retry": ("maintenance-runner-retry", HELPER_SHA),
}


class MaintenanceWorkflowContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        operations = json.loads(
            (ROOT / "contracts/public-workflows/operations.json").read_text(
                encoding="utf-8"
            )
        )
        cls.records = {row["api_name"]: row for row in operations["workflows"]}
        permissions = json.loads(
            (ROOT / "contracts/permission-profiles.json").read_text(
                encoding="utf-8"
            )
        )
        cls.permissions = {row["id"]: row for row in permissions["profiles"]}

    def test_four_maintenance_workflows_match_reviewed_public_api_without_new_control_surface(self) -> None:
        for api, (helper, helper_sha) in HELPERS.items():
            with self.subTest(api=api):
                record = self.records[api]
                path = ROOT / record["file"]
                text = path.read_text(encoding="utf-8")
                workflow = yaml.load(text, Loader=ActionsLoader)
                self.assertEqual(set(workflow["on"]), {"workflow_call"})
                call = workflow["on"]["workflow_call"]
                self.assertEqual(
                    set(call["inputs"]),
                    {row["name"] for row in record["inputs"]},
                )
                self.assertEqual(
                    set(call.get("secrets", {})),
                    set(record["secrets"]),
                )
                self.assertEqual(set(call["outputs"]), set(record["outputs"]))
                self.assertEqual(
                    workflow["permissions"],
                    self.permissions[record["permission_profile"]][
                        "workflow_permissions"
                    ],
                )
                self.assertEqual(len(workflow["jobs"]), 1)
                job = next(iter(workflow["jobs"].values()))
                self.assertEqual(job["runs-on"], ["linux", "amd64", "general"])
                self.assertLessEqual(
                    job["timeout-minutes"],
                    record["timeout_minutes"],
                )
                self.assertIn(
                    "uses: StreamScapeTV/ci-workflows/actions/"
                    f"{helper}@{helper_sha}",
                    text,
                )
                self.assertNotIn("actions/checkout@", text)
                self.assertNotIn("job.workflow_", text)
                self.assertNotIn("path: .ciw", text)
                self.assertNotIn("./.ciw/actions/", text)
                self.assertNotIn("secrets: inherit", text)
                self.assertNotIn("self-hosted", text)
                for forbidden in (
                    "arbitrary_command",
                    "callback_url",
                    "runner_labels",
                    "force-push",
                ):
                    self.assertNotIn(forbidden, text)

    def test_conformance_repin_target_is_review_only_and_bounded_to_exact_sha_transport(self) -> None:
        record = self.records["maintenance.conformance"]
        inputs = {row["name"]: row for row in record["inputs"]}
        self.assertIn("shared_reference_target_sha", inputs)
        self.assertFalse(inputs["shared_reference_target_sha"]["required"])
        text = (ROOT / record["file"]).read_text(encoding="utf-8")
        self.assertIn("shared_reference_target_sha:", text)
        self.assertEqual(
            text.count(
                "shared_reference_target_sha: ${{ inputs.shared_reference_target_sha }}"
            ),
            2,
        )
        self.assertNotIn("consumer_repository", text)
        self.assertNotIn("consumer_path", text)
        self.assertNotIn("replacement_text", text)

    def test_optional_branch_pr_number_zero_is_treated_as_omitted(self) -> None:
        text = (
            ROOT / "actions/maintenance-branches/action.yml"
        ).read_text(encoding="utf-8")
        self.assertIn("CIW_PR_NUMBER: ${{ inputs.pr_number }}", text)
        self.assertIn('pr_number="${CIW_PR_NUMBER}"', text)
        self.assertIn('"${pr_number}" != "0"', text)
        self.assertIn('[[ "${pr_number}" =~ ^[1-9][0-9]*$ ]]', text)
        self.assertIn('args+=(--pr-number "${pr_number}")', text)

    def test_dry_run_remains_default_for_every_mutating_maintenance_api(self) -> None:
        for api in HELPERS:
            record = self.records[api]
            workflow = yaml.load(
                (ROOT / record["file"]).read_text(encoding="utf-8"),
                Loader=ActionsLoader,
            )
            self.assertIs(
                workflow["on"]["workflow_call"]["inputs"]["dry_run"][
                    "default"
                ],
                True,
            )

    def test_composite_actions_are_thin_and_delegate_only_through_ciw(self) -> None:
        for name, _ in HELPERS.values():
            path = ROOT / f"actions/{name}/action.yml"
            action = yaml.load(
                path.read_text(encoding="utf-8"),
                Loader=ActionsLoader,
            )
            self.assertEqual(action["runs"]["using"], "composite")
            self.assertEqual(len(action["runs"]["steps"]), 1)
            text = path.read_text(encoding="utf-8")
            self.assertIn("scripts/ci/ciw.py", text)
            self.assertNotIn("scripts/ci/maintenance.py", text)
            self.assertNotIn("runs-on", text)
            self.assertNotIn("repository_url", text)
            self.assertNotIn("shell_command", text)
            self.assertNotIn("${{ inputs.", action["runs"]["steps"][0]["run"])

    def test_maintenance_compatibility_cli_delegates_only_through_ciw(self) -> None:
        text = (ROOT / "scripts/ci/maintenance.py").read_text(encoding="utf-8")
        self.assertIn("from ci_workflows.ciw import main", text)
        self.assertIn('"maintenance", *sys.argv[1:]', text)
        self.assertNotIn("from ci_workflows.maintenance import", text)
        self.assertNotIn("MAINTENANCE_GITHUB_TOKEN", text)

    def test_focused_smoke_is_unprivileged_exact_head_and_zero_artifact(self) -> None:
        path = ROOT / ".github/workflows/maintenance-contract-smoke.yml"
        text = path.read_text(encoding="utf-8")
        workflow = yaml.load(text, Loader=ActionsLoader)
        self.assertEqual(set(workflow["on"]), {"pull_request"})
        self.assertEqual(
            workflow["permissions"],
            {"actions": "read", "contents": "read"},
        )
        job = workflow["jobs"]["focused"]
        self.assertEqual(job["runs-on"], ["linux", "amd64", "general"])
        self.assertIn("github.event.pull_request.head.sha", text)
        self.assertIn("zero Actions artifacts", text)
        self.assertNotIn("secrets.", text)


if __name__ == "__main__":
    unittest.main()
