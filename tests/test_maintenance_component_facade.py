from __future__ import annotations

import inspect
import unittest
from pathlib import Path
from unittest import mock

from ci_workflows import maintenance


class MaintenanceComponentFacadeTests(unittest.TestCase):
    def test_reviewed_public_and_projection_component_names_are_real_functions(self) -> None:
        for name in (
            "artifacts",
            "branches",
            "conformance",
            "runner_retry",
            "project_status",
            "project_comment",
            "project_labels",
        ):
            value = getattr(maintenance, name)
            self.assertTrue(inspect.isfunction(value), name)
            self.assertEqual(value.__module__, "ci_workflows.maintenance")

    def test_artifact_facade_forwards_bounded_named_arguments(self) -> None:
        contract = object()
        api = object()
        sentinel = object()
        with mock.patch(
            "ci_workflows.maintenance._artifacts",
            return_value=sentinel,
        ) as delegated:
            result = maintenance.artifacts(
                contract,
                api,
                root=Path("root"),
                repository_scope="scope",
                dry_run=True,
                request_id="request",
            )
        self.assertIs(result, sentinel)
        delegated.assert_called_once_with(
            contract,
            api,
            root=Path("root"),
            repository_scope="scope",
            dry_run=True,
            request_id="request",
            now=None,
        )

    def test_remaining_facades_forward_without_arbitrary_callbacks(self) -> None:
        contract = object()
        api = object()
        with (
            mock.patch("ci_workflows.maintenance._branches") as branches,
            mock.patch("ci_workflows.maintenance._conformance") as conformance,
            mock.patch("ci_workflows.maintenance._runner_retry") as retry,
            mock.patch("ci_workflows.maintenance._project_status") as status,
            mock.patch("ci_workflows.maintenance._project_comment") as comment,
            mock.patch("ci_workflows.maintenance._project_labels") as labels,
        ):
            maintenance.branches(
                contract,
                api,
                project_id="project",
                pr_number=7,
                expected_head_sha="a" * 40,
                dry_run=True,
                request_id="branch",
            )
            maintenance.conformance(
                contract,
                api,
                root=Path("root"),
                repository_scope="project",
                shared_reference_target_sha="b" * 40,
                dry_run=True,
                request_id="scan",
            )
            maintenance.runner_retry(
                contract,
                api,
                root=Path("root"),
                project_id="project",
                run_id=9,
                expected_head_sha="b" * 40,
                dry_run=True,
                request_id="retry",
            )
            maintenance.project_status(
                contract,
                api,
                project_id="project",
                expected_sha="c" * 40,
                state="success",
                context="bounded",
                description="ok",
                request_id="status",
            )
            maintenance.project_comment(
                contract,
                api,
                project_id="project",
                issue_number=10,
                expected_updated_at="2026-08-13T09:00:00Z",
                marker="bounded",
                body="decision",
                request_id="comment",
            )
            maintenance.project_labels(
                contract,
                api,
                project_id="project",
                issue_number=10,
                expected_updated_at="2026-08-13T09:00:00Z",
                expected_labels=["old"],
                desired_labels=["new"],
                request_id="labels",
            )
        self.assertEqual(branches.call_count, 1)
        self.assertEqual(conformance.call_count, 1)
        self.assertEqual(retry.call_count, 1)
        self.assertEqual(status.call_count, 1)
        self.assertEqual(comment.call_count, 1)
        self.assertEqual(labels.call_count, 1)


if __name__ == "__main__":
    unittest.main()
