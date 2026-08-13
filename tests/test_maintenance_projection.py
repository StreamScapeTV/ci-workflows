from __future__ import annotations

import unittest
from pathlib import Path
from typing import Any, Mapping

from ci_workflows.maintenance_contract import MaintenanceError, load_contract
from ci_workflows.maintenance_projection import (
    project_comment,
    project_labels,
    project_status,
)

ROOT = Path(__file__).resolve().parents[1]
SHA = "a" * 40


class ProjectionApi:
    def __init__(self) -> None:
        self.commit: Mapping[str, Any] | None = {"sha": SHA}
        self.statuses: list[Mapping[str, Any]] = []
        self.issue: Mapping[str, Any] | None = {
            "number": 7,
            "state": "open",
            "updated_at": "2026-08-13T09:00:00Z",
            "labels": [{"name": "triage"}],
        }
        self.comments: list[Mapping[str, Any]] = []
        self.created_statuses: list[dict[str, str]] = []
        self.created_comments: list[str] = []
        self.updated_comments: list[tuple[int, str]] = []
        self.label_updates: list[list[str]] = []

    def get_commit(self, repository: str, sha: str):
        return self.commit

    def list_statuses(self, repository: str, sha: str):
        return list(self.statuses)

    def create_status(
        self,
        repository: str,
        sha: str,
        *,
        state: str,
        context: str,
        description: str,
    ):
        value = {
            "id": len(self.created_statuses) + 100,
            "state": state,
            "context": context,
            "description": description,
            "target_url": None,
            "updated_at": "2026-08-13T09:01:00Z",
        }
        self.created_statuses.append(
            {
                "state": state,
                "context": context,
                "description": description,
            }
        )
        self.statuses.insert(0, value)
        return value

    def get_issue(self, repository: str, number: int):
        return None if self.issue is None else dict(self.issue)

    def list_issue_comments(self, repository: str, number: int):
        return [dict(comment) for comment in self.comments]

    def create_issue_comment(self, repository: str, number: int, body: str):
        value = {
            "id": len(self.comments) + 10,
            "body": body,
            "updated_at": "2026-08-13T09:01:00Z",
        }
        self.created_comments.append(body)
        self.comments.append(value)
        return value

    def update_issue_comment(self, repository: str, comment_id: int, body: str):
        self.updated_comments.append((comment_id, body))
        for index, comment in enumerate(self.comments):
            if comment.get("id") == comment_id:
                self.comments[index] = {
                    **comment,
                    "body": body,
                    "updated_at": "2026-08-13T09:02:00Z",
                }
                return self.comments[index]
        raise AssertionError("missing comment")

    def set_issue_labels(self, repository: str, number: int, labels):
        self.label_updates.append(list(labels))
        assert self.issue is not None
        self.issue = {
            **self.issue,
            "labels": [{"name": label} for label in labels],
            "updated_at": "2026-08-13T09:03:00Z",
        }
        return self.issue


class RacingCommentApi(ProjectionApi):
    def __init__(self) -> None:
        super().__init__()
        self.issue_reads = 0

    def get_issue(self, repository: str, number: int):
        self.issue_reads += 1
        value = super().get_issue(repository, number)
        if value is not None and self.issue_reads > 1:
            return {**value, "updated_at": "2026-08-13T09:00:01Z"}
        return value


class MaintenanceProjectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.contract = load_contract(ROOT)

    def test_status_projection_is_exact_source_and_replay_safe(self) -> None:
        api = ProjectionApi()
        first = project_status(
            self.contract,
            api,
            project_id="ci-workflows",
            expected_sha=SHA,
            state="success",
            context="Central / Sanitized decision",
            description="bounded decision accepted",
            request_id="status-first",
        )
        replay = project_status(
            self.contract,
            api,
            project_id="ci-workflows",
            expected_sha=SHA,
            state="success",
            context="Central / Sanitized decision",
            description="bounded decision accepted",
            request_id="status-replay",
        )
        self.assertEqual(first.mutation_count, 1)
        self.assertEqual(replay.mutation_count, 0)
        self.assertEqual(len(api.created_statuses), 1)

    def test_status_projection_selects_latest_matching_status_independent_of_api_order(self) -> None:
        api = ProjectionApi()
        context = "Central / Ordered status"
        api.statuses = [
            {
                "id": 10,
                "state": "failure",
                "context": context,
                "description": "old state",
                "target_url": None,
                "updated_at": "2026-08-13T08:59:00Z",
            },
            {
                "id": 11,
                "state": "success",
                "context": context,
                "description": "current state",
                "target_url": None,
                "updated_at": "2026-08-13T09:05:00Z",
            },
        ]
        replay = project_status(
            self.contract,
            api,
            project_id="ci-workflows",
            expected_sha=SHA,
            state="success",
            context=context,
            description="current state",
            request_id="status-order",
        )
        self.assertEqual(replay.mutation_count, 0)
        self.assertEqual(api.created_statuses, [])

    def test_status_projection_rejects_source_project_and_context_injection(self) -> None:
        api = ProjectionApi()
        api.commit = {"sha": "b" * 40}
        with self.assertRaisesRegex(MaintenanceError, "projection_source_changed"):
            project_status(
                self.contract,
                api,
                project_id="ci-workflows",
                expected_sha=SHA,
                state="success",
                context="Central / Status",
                description="ok",
                request_id="status-drift",
            )
        api.commit = {"sha": SHA}
        with self.assertRaisesRegex(MaintenanceError, "project_not_allowlisted"):
            project_status(
                self.contract,
                api,
                project_id="attacker/repository",
                expected_sha=SHA,
                state="success",
                context="Central / Status",
                description="ok",
                request_id="status-project",
            )
        with self.assertRaisesRegex(MaintenanceError, "projection_status_invalid"):
            project_status(
                self.contract,
                api,
                project_id="ci-workflows",
                expected_sha=SHA,
                state="success",
                context="x\nforged",
                description="ok",
                request_id="status-forged",
            )

    def test_comment_projection_upserts_one_deterministic_marker(self) -> None:
        api = ProjectionApi()
        first = project_comment(
            self.contract,
            api,
            project_id="ci-workflows",
            issue_number=7,
            expected_updated_at="2026-08-13T09:00:00Z",
            marker="review-decision",
            body="Sanitized bounded decision.",
            request_id="comment-first",
        )
        replay = project_comment(
            self.contract,
            api,
            project_id="ci-workflows",
            issue_number=7,
            expected_updated_at="2026-08-13T08:00:00Z",
            marker="review-decision",
            body="Sanitized bounded decision.",
            request_id="comment-replay",
        )
        self.assertEqual(first.mutation_count, 1)
        self.assertEqual(replay.mutation_count, 0)
        self.assertEqual(len(api.created_comments), 1)
        self.assertEqual(api.updated_comments, [])

    def test_comment_projection_revalidates_before_mutation_and_rejects_duplicate_marker(self) -> None:
        api = RacingCommentApi()
        with self.assertRaisesRegex(
            MaintenanceError,
            "projection_comment_changed_before_update",
        ):
            project_comment(
                self.contract,
                api,
                project_id="ci-workflows",
                issue_number=7,
                expected_updated_at="2026-08-13T09:00:00Z",
                marker="race",
                body="Sanitized bounded decision.",
                request_id="comment-race",
            )
        self.assertEqual(api.created_comments, [])

        duplicate = ProjectionApi()
        duplicate.comments = [
            {"id": 1, "body": "<!-- ci-workflows-projection:dup -->\na"},
            {"id": 2, "body": "<!-- ci-workflows-projection:dup -->\nb"},
        ]
        with self.assertRaisesRegex(MaintenanceError, "projection_comment_ambiguous"):
            project_comment(
                self.contract,
                duplicate,
                project_id="ci-workflows",
                issue_number=7,
                expected_updated_at="2026-08-13T09:00:00Z",
                marker="dup",
                body="Sanitized bounded decision.",
                request_id="comment-duplicate",
            )

        malformed = ProjectionApi()
        malformed.comments = [
            {
                "id": 0,
                "body": "<!-- ci-workflows-projection:bad -->\n"
                "Sanitized bounded decision.",
            }
        ]
        with self.assertRaisesRegex(MaintenanceError, "projection_comment_invalid"):
            project_comment(
                self.contract,
                malformed,
                project_id="ci-workflows",
                issue_number=7,
                expected_updated_at="2026-08-13T08:00:00Z",
                marker="bad",
                body="Sanitized bounded decision.",
                request_id="comment-malformed",
            )

    def test_projection_rejects_non_timestamp_expected_issue_state(self) -> None:
        api = ProjectionApi()
        with self.assertRaisesRegex(MaintenanceError, "projection_issue_invalid"):
            project_comment(
                self.contract,
                api,
                project_id="ci-workflows",
                issue_number=7,
                expected_updated_at="not-a-timestamp",
                marker="timestamp",
                body="Sanitized bounded decision.",
                request_id="comment-timestamp",
            )
        with self.assertRaisesRegex(MaintenanceError, "projection_issue_invalid"):
            project_labels(
                self.contract,
                api,
                project_id="ci-workflows",
                issue_number=7,
                expected_updated_at="not-a-timestamp",
                expected_labels=["triage"],
                desired_labels=["ready"],
                request_id="labels-timestamp",
            )

    def test_labels_projection_requires_exact_expected_state_and_replays(self) -> None:
        api = ProjectionApi()
        first = project_labels(
            self.contract,
            api,
            project_id="ci-workflows",
            issue_number=7,
            expected_updated_at="2026-08-13T09:00:00Z",
            expected_labels=["triage"],
            desired_labels=["ready", "triage"],
            request_id="labels-first",
        )
        replay = project_labels(
            self.contract,
            api,
            project_id="ci-workflows",
            issue_number=7,
            expected_updated_at="2026-08-13T08:00:00Z",
            expected_labels=["triage"],
            desired_labels=["ready", "triage"],
            request_id="labels-replay",
        )
        self.assertEqual(first.mutation_count, 1)
        self.assertEqual(replay.mutation_count, 0)
        self.assertEqual(api.label_updates, [["ready", "triage"]])

        stale = ProjectionApi()
        with self.assertRaisesRegex(MaintenanceError, "projection_issue_changed"):
            project_labels(
                self.contract,
                stale,
                project_id="ci-workflows",
                issue_number=7,
                expected_updated_at="2026-08-13T09:00:00Z",
                expected_labels=["other"],
                desired_labels=["ready"],
                request_id="labels-stale",
            )
        self.assertEqual(stale.label_updates, [])


if __name__ == "__main__":
    unittest.main()
