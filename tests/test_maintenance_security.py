from __future__ import annotations

import json
import unittest
import urllib.request
from datetime import datetime, timezone
from email.message import Message
from pathlib import Path

from ci_workflows.maintenance import GitHubApi, artifacts, conformance
from ci_workflows.maintenance_contract import MaintenanceError, load_contract
from ci_workflows.maintenance_http_transport import _SafeRedirectHandler
from tests.test_maintenance_runtime import FakeApi

ROOT = Path(__file__).resolve().parents[1]


class RunSequenceApi(FakeApi):
    def __init__(self, runs):
        super().__init__()
        self.runs = list(runs)

    def get_run(self, repository: str, run_id: int):
        return self.runs.pop(0) if self.runs else None


class StableReportApi(FakeApi):
    def create_issue(self, repository: str, title: str, body: str):
        issue = {"number": 77, "title": title, "body": body, "html_url": "https://example.invalid/77"}
        self.created_issues.append((title, body))
        self.open_issues = [issue]
        return issue


class MaintenanceSecurityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.contract = load_contract(ROOT)

    def test_artifact_run_change_before_delete_fails_closed(self) -> None:
        repo = "StreamScapeTV/ci-workflows"
        old = {"id": 9, "name": "ordinary", "size_in_bytes": 12, "created_at": "2026-08-10T12:00:00Z", "workflow_run": {"id": 55}}
        api = RunSequenceApi([
            {"id": 55, "status": "completed", "conclusion": "success", "run_attempt": 1, "head_sha": "a" * 40, "updated_at": "2026-08-10T13:00:00Z"},
            {"id": 55, "status": "completed", "conclusion": "success", "run_attempt": 2, "head_sha": "a" * 40, "updated_at": "2026-08-10T14:00:00Z"},
        ])
        api.artifact_rows[repo] = [old]
        api.artifact_current[(repo, 9)] = dict(old)
        with self.assertRaisesRegex(MaintenanceError, "artifact_run_changed_before_delete"):
            artifacts(self.contract, api, root=ROOT, repository_scope="ci-workflows", dry_run=False, request_id="artifact-run-drift", now=datetime(2026, 8, 12, 12, tzinfo=timezone.utc))
        self.assertEqual(api.deleted_artifacts, [])

    def test_conformance_exact_replay_is_noop(self) -> None:
        api = StableReportApi()
        api.workflow_files["StreamScapeTV/ci-workflows"] = [".github/workflows/new-unregistered.yml"]
        first = conformance(self.contract, api, root=ROOT, repository_scope="ci-workflows", dry_run=False, request_id="same-request")
        second = conformance(self.contract, api, root=ROOT, repository_scope="ci-workflows", dry_run=False, request_id="same-request")
        self.assertEqual(first.mutation_count, 1)
        self.assertEqual(second.mutation_count, 0)
        self.assertEqual(api.updated_issues, [])
        self.assertIn("conformance_report_unchanged", {row.get("reason") for row in second.decisions})

    def test_cross_origin_pagination_is_rejected(self) -> None:
        api = GitHubApi("token")
        with self.assertRaisesRegex(MaintenanceError, "unsafe_api_url"):
            api._url("https://evil.example.invalid/repos/x?page=2")

    def test_cross_origin_https_redirect_strips_authorization(self) -> None:
        request = urllib.request.Request("https://api.github.com/repos/x/actions/jobs/1/logs")
        request.add_header("Authorization", "Bearer secret")
        redirected = _SafeRedirectHandler().redirect_request(
            request,
            None,
            302,
            "Found",
            Message(),
            "https://objects.githubusercontent.com/signed-log",
        )
        self.assertIsNotNone(redirected)
        self.assertIsNone(redirected.get_header("Authorization"))


if __name__ == "__main__":
    unittest.main()
