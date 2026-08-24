from __future__ import annotations

import unittest
from pathlib import Path

import yaml

from ci_workflows.validation_model import ActionsLoader

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/device-validation-contract-smoke.yml"
OWNER_FRAGMENT = "github.event.pull_request.user.login == 'mimranfaruqi'"
REPOSITORY_FRAGMENT = (
    "github.event.pull_request.head.repo.full_name == github.repository"
)
PR_ONLY_PLAN_JOBS = {"authorized_pr_plan", "authorization_transport"}


class DevicePublicCiAdmissionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = WORKFLOW.read_text(encoding="utf-8")
        cls.workflow = yaml.load(cls.source, Loader=ActionsLoader)

    def test_reviewed_entry_events_remain_bounded(self) -> None:
        self.assertEqual(
            {"pull_request", "push", "workflow_dispatch"},
            set(self.workflow["on"]),
        )
        for forbidden in (
            "pull_request_target",
            "issue_comment",
            "repository_dispatch",
            "workflow_run",
        ):
            self.assertNotIn(forbidden, self.workflow["on"])
        self.assertEqual(
            ["main", "agent*/**", "agent/**", "issue/**"],
            self.workflow["on"]["push"]["branches"],
        )

    def test_every_pr_reachable_job_requires_exact_owner_and_same_repository(self) -> None:
        for job_id, job in self.workflow["jobs"].items():
            with self.subTest(job=job_id):
                condition = " ".join(str(job.get("if", "")).split())
                self.assertIn(OWNER_FRAGMENT, condition)
                self.assertIn(REPOSITORY_FRAGMENT, condition)
                if job_id in PR_ONLY_PLAN_JOBS:
                    self.assertIn("github.event_name == 'pull_request'", condition)
                else:
                    self.assertIn("github.event_name != 'pull_request'", condition)

    def test_public_pr_admission_never_uses_broad_trust_or_visibility(self) -> None:
        self.assertNotIn("author_association", self.source)
        self.assertNotIn("github.event.repository.private", self.source)
        self.assertNotIn("secrets: inherit", self.source)

    def test_pr_only_device_jobs_remain_plan_only(self) -> None:
        authorized = self.source.split("  authorized_pr_plan:\n", 1)[1].split(
            "\n  authorization_transport:\n", 1
        )[0]
        self.assertIn("phase: plan", authorized)
        self.assertIn("execution_authorized", authorized)
        self.assertIn("trusted-exact", authorized)
        self.assertNotIn("phase: execute", authorized)
        self.assertNotIn("phase: discover", authorized)

        transport = self.source.split("  authorization_transport:\n", 1)[1].split(
            "\n  synthetic:\n", 1
        )[0]
        self.assertIn(
            "StreamScapeTV/ci-workflows/.github/workflows/internal-device-authorization-transport.yml@",
            transport,
        )
        self.assertIn("device_authorization_receipt", transport)
        self.assertNotIn("runs-on:", transport)
        self.assertNotIn("phase: execute", transport)
        self.assertNotIn("phase: discover", transport)


if __name__ == "__main__":
    unittest.main()
