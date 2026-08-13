from __future__ import annotations

import unittest
from pathlib import Path
from typing import Any, Mapping

from ci_workflows.maintenance_conformance import conformance
from ci_workflows.maintenance_contract import MaintenanceError, load_contract

ROOT = Path(__file__).resolve().parents[1]
TITLE = "Organization conformance: ci-workflows"


class RacingIssueApi:
    def __init__(self, snapshots: list[list[Mapping[str, Any]]]) -> None:
        self.snapshots = snapshots
        self.list_calls = 0
        self.created: list[tuple[str, str]] = []
        self.updated: list[tuple[int, str, str]] = []

    def list_workflow_files(self, repository: str) -> list[str]:
        return []

    def get_file_text(self, repository: str, path: str, ref: str) -> str | None:
        return None

    def list_open_issues(self, repository: str) -> list[Mapping[str, Any]]:
        index = min(self.list_calls, len(self.snapshots) - 1)
        self.list_calls += 1
        return list(self.snapshots[index])

    def create_issue(self, repository: str, title: str, body: str) -> Mapping[str, Any]:
        self.created.append((title, body))
        return {"number": 99, "title": title, "body": body, "html_url": "https://example.invalid/99"}

    def update_issue(self, repository: str, number: int, title: str, body: str) -> Mapping[str, Any]:
        self.updated.append((number, title, body))
        return {"number": number, "title": title, "body": body, "html_url": f"https://example.invalid/{number}"}


class MaintenanceConformanceRaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.contract = load_contract(ROOT)

    def issue(self, *, body: str, updated_at: str) -> dict[str, Any]:
        return {
            "number": 77,
            "title": TITLE,
            "body": body,
            "state": "open",
            "updated_at": updated_at,
            "html_url": "https://example.invalid/77",
        }

    def test_concurrent_report_edit_fails_closed_before_update(self) -> None:
        api = RacingIssueApi(
            [
                [self.issue(body="old", updated_at="2026-08-13T00:00:00Z")],
                [self.issue(body="human edit", updated_at="2026-08-13T00:00:01Z")],
            ]
        )
        with self.assertRaisesRegex(MaintenanceError, "conformance_report_changed_before_update"):
            conformance(
                self.contract,
                api,
                root=ROOT,
                repository_scope="ci-workflows",
                dry_run=False,
                request_id="conformance-race-update",
            )
        self.assertEqual(api.updated, [])

    def test_concurrent_report_creation_fails_closed_before_duplicate_create(self) -> None:
        api = RacingIssueApi(
            [
                [],
                [self.issue(body="concurrent", updated_at="2026-08-13T00:00:00Z")],
            ]
        )
        with self.assertRaisesRegex(MaintenanceError, "conformance_report_changed_before_create"):
            conformance(
                self.contract,
                api,
                root=ROOT,
                repository_scope="ci-workflows",
                dry_run=False,
                request_id="conformance-race-create",
            )
        self.assertEqual(api.created, [])


if __name__ == "__main__":
    unittest.main()
