from __future__ import annotations

import unittest
from datetime import datetime, timezone
from pathlib import Path

from ci_workflows.maintenance import artifacts, branches, conformance
from ci_workflows.maintenance_contract import MaintenanceError, load_contract
from tests.test_maintenance_runtime import FakeApi, SHA, pull

ROOT = Path(__file__).resolve().parents[1]


class StickyArtifactApi(FakeApi):
    def delete_artifact(self, repository: str, artifact_id: int):
        self.deleted_artifacts.append((repository, artifact_id))


class StickyBranchApi(FakeApi):
    def delete_branch(self, repository: str, branch: str):
        self.deleted_branches.append((repository, branch))


class BadCreateReportApi(FakeApi):
    def create_issue(self, repository: str, title: str, body: str):
        return {
            "number": 77,
            "title": title,
            "body": "mismatched body",
            "html_url": "https://example.invalid/77",
        }


class BadUpdateReportApi(FakeApi):
    def update_issue(self, repository: str, number: int, title: str, body: str):
        return {
            "number": number + 1,
            "title": title,
            "body": body,
            "html_url": f"https://example.invalid/{number + 1}",
        }


class MaintenanceMutationVerificationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.contract = load_contract(ROOT)

    def test_artifact_delete_must_be_observed_absent(self) -> None:
        api = StickyArtifactApi()
        repo = "StreamScapeTV/ci-workflows"
        item = {
            "id": 9,
            "name": "ordinary",
            "size_in_bytes": 12,
            "created_at": "2026-08-10T12:00:00Z",
            "workflow_run": {"id": 55},
        }
        api.artifact_rows[repo] = [item]
        api.artifact_current[(repo, 9)] = dict(item)
        api.run = {"status": "completed"}
        with self.assertRaisesRegex(
            MaintenanceError,
            "artifact_delete_verification_failed",
        ):
            artifacts(
                self.contract,
                api,
                root=ROOT,
                repository_scope="ci-workflows",
                dry_run=False,
                request_id="verify-artifact-delete",
                now=datetime(2026, 8, 12, 12, tzinfo=timezone.utc),
            )
        self.assertEqual(api.deleted_artifacts, [(repo, 9)])

    def test_branch_delete_must_be_observed_absent(self) -> None:
        api = StickyBranchApi()
        api.pull = pull()
        api.branch = {"protected": False, "commit": {"sha": SHA}}
        with self.assertRaisesRegex(
            MaintenanceError,
            "branch_delete_verification_failed",
        ):
            branches(
                self.contract,
                api,
                project_id="agent-state",
                pr_number=20,
                expected_head_sha=SHA,
                dry_run=False,
                request_id="verify-branch-delete",
            )
        self.assertEqual(
            api.deleted_branches,
            [("StreamScapeTV/agent-state", "issue/20-maintenance-abcd")],
        )

    def test_conformance_create_response_must_match_exact_report(self) -> None:
        api = BadCreateReportApi()
        with self.assertRaisesRegex(
            MaintenanceError,
            "conformance_report_verification_failed",
        ):
            conformance(
                self.contract,
                api,
                root=ROOT,
                repository_scope="ci-workflows",
                dry_run=False,
                request_id="verify-report-create",
            )

    def test_conformance_update_response_must_match_exact_issue(self) -> None:
        api = BadUpdateReportApi()
        api.open_issues = [
            {
                "number": 77,
                "title": "Organization conformance: ci-workflows",
                "body": "old body",
                "state": "open",
                "updated_at": "2026-08-13T09:00:00Z",
                "html_url": "https://example.invalid/77",
            }
        ]
        with self.assertRaisesRegex(
            MaintenanceError,
            "conformance_report_verification_failed",
        ):
            conformance(
                self.contract,
                api,
                root=ROOT,
                repository_scope="ci-workflows",
                dry_run=False,
                request_id="verify-report-update",
            )


if __name__ == "__main__":
    unittest.main()
