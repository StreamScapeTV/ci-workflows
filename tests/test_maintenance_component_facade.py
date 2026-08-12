from __future__ import annotations

import inspect
import unittest
from pathlib import Path
from unittest import mock

from ci_workflows import maintenance


class MaintenanceComponentFacadeTests(unittest.TestCase):
    def test_reviewed_public_component_names_are_real_functions(self) -> None:
        for name in ("artifacts", "branches", "conformance", "runner_retry"):
            value = getattr(maintenance, name)
            self.assertTrue(inspect.isfunction(value), name)
            self.assertEqual(value.__module__, "ci_workflows.maintenance")

    def test_artifact_facade_forwards_bounded_named_arguments(self) -> None:
        contract = object()
        api = object()
        sentinel = object()
        with mock.patch("ci_workflows.maintenance._artifacts", return_value=sentinel) as delegated:
            result = maintenance.artifacts(contract, api, root=Path("root"), repository_scope="scope", dry_run=True, request_id="request")
        self.assertIs(result, sentinel)
        delegated.assert_called_once_with(contract, api, root=Path("root"), repository_scope="scope", dry_run=True, request_id="request", now=None)

    def test_remaining_facades_forward_without_arbitrary_callbacks(self) -> None:
        contract = object()
        api = object()
        with mock.patch("ci_workflows.maintenance._branches") as branches, mock.patch("ci_workflows.maintenance._conformance") as conformance, mock.patch("ci_workflows.maintenance._runner_retry") as retry:
            maintenance.branches(contract, api, project_id="project", pr_number=7, expected_head_sha="a" * 40, dry_run=True, request_id="branch")
            maintenance.conformance(contract, api, root=Path("root"), repository_scope="project", dry_run=True, request_id="scan")
            maintenance.runner_retry(contract, api, root=Path("root"), project_id="project", run_id=9, expected_head_sha="b" * 40, dry_run=True, request_id="retry")
        self.assertEqual(branches.call_count, 1)
        self.assertEqual(conformance.call_count, 1)
        self.assertEqual(retry.call_count, 1)


if __name__ == "__main__":
    unittest.main()
