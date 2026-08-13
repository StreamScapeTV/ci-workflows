from __future__ import annotations

import unittest

from ci_workflows.maintenance_contract import MaintenanceError, load_contract
from ci_workflows.maintenance_projection import (
    project_comment,
    project_labels,
    project_status,
)
from tests.test_maintenance_projection import ProjectionApi, ROOT, SHA


class BadStatusApi(ProjectionApi):
    def create_status(self, repository, sha, *, state, context, description):
        return {
            "id": 101,
            "state": "failure",
            "context": context,
            "description": description,
            "target_url": None,
            "updated_at": "2026-08-13T09:01:00Z",
        }


class BadCommentApi(ProjectionApi):
    def create_issue_comment(self, repository, number, body):
        return {"id": 10, "body": "different body"}


class BadLabelsApi(ProjectionApi):
    def set_issue_labels(self, repository, number, labels):
        assert self.issue is not None
        return {
            **self.issue,
            "labels": [{"name": "unexpected"}],
            "updated_at": "2026-08-13T09:03:00Z",
        }


class WrongIssueApi(ProjectionApi):
    def get_issue(self, repository, number):
        value = super().get_issue(repository, number)
        return None if value is None else {**value, "number": number + 1}


class MaintenanceProjectionVerificationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.contract = load_contract(ROOT)

    def test_status_mutation_response_must_match_requested_state(self) -> None:
        with self.assertRaisesRegex(
            MaintenanceError,
            "projection_status_verification_failed",
        ):
            project_status(
                self.contract,
                BadStatusApi(),
                project_id="ci-workflows",
                expected_sha=SHA,
                state="success",
                context="Central / Verified response",
                description="bounded decision",
                request_id="verify-status",
            )

    def test_comment_mutation_response_must_match_marker_body(self) -> None:
        with self.assertRaisesRegex(
            MaintenanceError,
            "projection_comment_verification_failed",
        ):
            project_comment(
                self.contract,
                BadCommentApi(),
                project_id="ci-workflows",
                issue_number=7,
                expected_updated_at="2026-08-13T09:00:00Z",
                marker="verified-comment",
                body="bounded decision",
                request_id="verify-comment",
            )

    def test_label_mutation_response_must_match_exact_desired_set(self) -> None:
        with self.assertRaisesRegex(
            MaintenanceError,
            "projection_labels_verification_failed",
        ):
            project_labels(
                self.contract,
                BadLabelsApi(),
                project_id="ci-workflows",
                issue_number=7,
                expected_updated_at="2026-08-13T09:00:00Z",
                expected_labels=["triage"],
                desired_labels=["ready", "triage"],
                request_id="verify-labels",
            )

    def test_issue_response_number_must_match_requested_issue(self) -> None:
        api = WrongIssueApi()
        with self.assertRaisesRegex(MaintenanceError, "projection_issue_invalid"):
            project_comment(
                self.contract,
                api,
                project_id="ci-workflows",
                issue_number=7,
                expected_updated_at="2026-08-13T09:00:00Z",
                marker="wrong-issue",
                body="bounded decision",
                request_id="wrong-issue-comment",
            )
        with self.assertRaisesRegex(MaintenanceError, "projection_issue_invalid"):
            project_labels(
                self.contract,
                api,
                project_id="ci-workflows",
                issue_number=7,
                expected_updated_at="2026-08-13T09:00:00Z",
                expected_labels=["triage"],
                desired_labels=["ready"],
                request_id="wrong-issue-labels",
            )


if __name__ == "__main__":
    unittest.main()
