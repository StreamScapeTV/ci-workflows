from __future__ import annotations

import io
import json
import tempfile
import unittest
import urllib.error
from datetime import datetime, timezone
from email.message import Message
from pathlib import Path
from typing import Any, Mapping

from ci_workflows.maintenance import (
    GitHubApi,
    artifacts,
    branches,
    conformance,
    runner_retry,
)
from ci_workflows.maintenance_contract import MaintenanceError, load_contract

ROOT = Path(__file__).resolve().parents[1]
SHA = "a" * 40


def pull(*, merged: bool = True) -> dict[str, Any]:
    return {
        "number": 20,
        "state": "closed",
        "merged_at": "2026-08-12T10:00:00Z" if merged else None,
        "head": {
            "sha": SHA,
            "ref": "issue/20-maintenance-abcd",
            "repo": {"full_name": "StreamScapeTV/agent-state"},
        },
        "base": {
            "ref": "main",
            "repo": {"full_name": "StreamScapeTV/agent-state"},
        },
    }


class FakeApi:
    def __init__(self) -> None:
        self.artifact_rows: dict[str, list[Mapping[str, Any]]] = {}
        self.artifact_current: dict[
            tuple[str, int], Mapping[str, Any] | None
        ] = {}
        self.run: Mapping[str, Any] | None = None
        self.pull: Mapping[str, Any] | None = None
        self.branch: Mapping[str, Any] | None = None
        self.deleted_artifacts: list[tuple[str, int]] = []
        self.deleted_branches: list[tuple[str, str]] = []
        self.workflow_files: dict[str, list[str]] = {}
        self.file_text: dict[tuple[str, str], str] = {}
        self.open_issues: list[Mapping[str, Any]] = []
        self.created_issues: list[tuple[str, str]] = []
        self.updated_issues: list[int] = []
        self.jobs: list[Mapping[str, Any]] = []
        self.logs: dict[int, str] = {}
        self.reruns: list[tuple[str, int]] = []

    def list_artifacts(self, repository: str):
        return list(self.artifact_rows.get(repository, []))

    def get_artifact(self, repository: str, artifact_id: int):
        return self.artifact_current.get((repository, artifact_id))

    def delete_artifact(self, repository: str, artifact_id: int):
        self.deleted_artifacts.append((repository, artifact_id))
        self.artifact_current[(repository, artifact_id)] = None

    def get_run(self, repository: str, run_id: int):
        return self.run

    def get_pull(self, repository: str, number: int):
        return self.pull

    def list_closed_pulls(self, repository: str, base: str):
        return [] if self.pull is None else [self.pull]

    def get_branch(self, repository: str, branch: str):
        return self.branch

    def delete_branch(self, repository: str, branch: str):
        self.deleted_branches.append((repository, branch))
        self.branch = None

    def list_workflow_files(self, repository: str):
        return list(self.workflow_files.get(repository, []))

    def get_file_text(self, repository: str, path: str, ref: str):
        return self.file_text.get((repository, path))

    def list_open_issues(self, repository: str):
        return list(self.open_issues)

    def create_issue(self, repository: str, title: str, body: str):
        self.created_issues.append((title, body))
        issue = {
            "number": 77,
            "title": title,
            "html_url": "https://example.invalid/77",
        }
        self.open_issues = [issue]
        return issue

    def update_issue(
        self,
        repository: str,
        number: int,
        title: str,
        body: str,
    ):
        self.updated_issues.append(number)
        return {
            "number": number,
            "title": title,
            "html_url": f"https://example.invalid/{number}",
        }

    def list_attempt_jobs(self, repository: str, run_id: int, attempt: int):
        return list(self.jobs)

    def download_job_logs(
        self,
        repository: str,
        job_id: int,
        maximum_bytes: int,
    ):
        return self.logs[job_id]

    def rerun_failed_jobs(self, repository: str, run_id: int):
        self.reruns.append((repository, run_id))


class MaintenanceRuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.contract = load_contract(ROOT)

    def test_artifact_cleanup_preserves_exception_and_revalidates_before_delete(self) -> None:
        api = FakeApi()
        repo = "StreamScapeTV/ci-workflows"
        retained = {
            "id": 1,
            "name": "android-validation-diagnostics",
            "size_in_bytes": 12,
            "created_at": "2026-08-11T12:00:00Z",
            "workflow_run": {"id": 9},
        }
        expired = {
            "id": 2,
            "name": "ordinary",
            "size_in_bytes": 12,
            "created_at": "2026-08-10T12:00:00Z",
            "workflow_run": {"id": 9},
        }
        api.artifact_rows[repo] = [retained, expired]
        api.artifact_current[(repo, 2)] = dict(expired)
        api.run = {"status": "completed"}
        result = artifacts(
            self.contract,
            api,
            root=ROOT,
            repository_scope="ci-workflows",
            dry_run=False,
            request_id="artifact-test",
            now=datetime(2026, 8, 12, 12, tzinfo=timezone.utc),
        )
        self.assertEqual(result.mutation_count, 1)
        self.assertEqual(api.deleted_artifacts, [(repo, 2)])
        self.assertIn(
            "retained_artifact_exception",
            {item["reason"] for item in result.decisions},
        )

    def test_artifact_exception_count_budget_preserves_newest_only(self) -> None:
        api = FakeApi()
        repo = "StreamScapeTV/ci-workflows"
        newest = {
            "id": 10,
            "name": "android-validation-diagnostics",
            "size_in_bytes": 12,
            "created_at": "2026-08-12T08:00:00Z",
            "workflow_run": {"id": 9},
        }
        older = {
            "id": 11,
            "name": "android-validation-diagnostics",
            "size_in_bytes": 12,
            "created_at": "2026-08-11T08:00:00Z",
            "workflow_run": {"id": 9},
        }
        api.artifact_rows[repo] = [older, newest]
        api.artifact_current[(repo, 10)] = dict(newest)
        api.artifact_current[(repo, 11)] = dict(older)
        api.run = {"status": "completed"}
        result = artifacts(
            self.contract,
            api,
            root=ROOT,
            repository_scope="ci-workflows",
            dry_run=False,
            request_id="artifact-count-budget",
            now=datetime(2026, 8, 12, 12, tzinfo=timezone.utc),
        )
        self.assertEqual(api.deleted_artifacts, [(repo, 11)])
        decisions = {row["artifact_id"]: row for row in result.decisions}
        self.assertEqual(decisions[10]["reason"], "retained_artifact_exception")
        self.assertEqual(decisions[11]["reason"], "artifact_exception_limit_exceeded")

    def test_artifact_exception_byte_budget_is_independently_enforced(self) -> None:
        api = FakeApi()
        repo = "StreamScapeTV/ci-workflows"
        newest = {
            "id": 20,
            "name": "bounded-diagnostic",
            "size_in_bytes": 10,
            "created_at": "2026-08-12T08:00:00Z",
            "workflow_run": {"id": 9},
        }
        older = {
            "id": 21,
            "name": "bounded-diagnostic",
            "size_in_bytes": 10,
            "created_at": "2026-08-11T08:00:00Z",
            "workflow_run": {"id": 9},
        }
        api.artifact_rows[repo] = [newest, older]
        api.artifact_current[(repo, 20)] = dict(newest)
        api.artifact_current[(repo, 21)] = dict(older)
        api.run = {"status": "completed"}
        with tempfile.TemporaryDirectory() as directory:
            contracts = Path(directory) / "contracts"
            contracts.mkdir()
            (contracts / "artifact-exceptions.json").write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "default": "zero-artifacts",
                        "exceptions": [
                            {
                                "id": "bounded",
                                "issue": 20,
                                "allowed_names": ["bounded-diagnostic"],
                                "trust_modes": ["trusted-validation"],
                                "maximum_count": 3,
                                "maximum_total_bytes": 15,
                                "maximum_retention_days": 3,
                                "reason": "test bounded retention",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            result = artifacts(
                self.contract,
                api,
                root=Path(directory),
                repository_scope="ci-workflows",
                dry_run=False,
                request_id="artifact-byte-budget",
                now=datetime(2026, 8, 12, 12, tzinfo=timezone.utc),
            )
        self.assertEqual(api.deleted_artifacts, [(repo, 21)])
        decisions = {row["artifact_id"]: row for row in result.decisions}
        self.assertEqual(decisions[20]["reason"], "retained_artifact_exception")
        self.assertEqual(decisions[21]["reason"], "artifact_exception_limit_exceeded")

    def test_active_run_artifacts_are_preserved_outside_exception_budget(self) -> None:
        api = FakeApi()
        repo = "StreamScapeTV/ci-workflows"
        items = [
            {
                "id": 30 + index,
                "name": "android-validation-diagnostics",
                "size_in_bytes": 12,
                "created_at": f"2026-08-11T0{index}:00:00Z",
                "workflow_run": {"id": 9},
            }
            for index in (1, 2)
        ]
        api.artifact_rows[repo] = items
        api.run = {"status": "in_progress"}
        result = artifacts(
            self.contract,
            api,
            root=ROOT,
            repository_scope="ci-workflows",
            dry_run=False,
            request_id="artifact-active-run",
            now=datetime(2026, 8, 12, 12, tzinfo=timezone.utc),
        )
        self.assertEqual(result.mutation_count, 0)
        self.assertEqual(api.deleted_artifacts, [])
        self.assertEqual(
            {row["reason"] for row in result.decisions},
            {"workflow_run_not_completed"},
        )

    def test_artifact_cleanup_changed_snapshot_fails_closed(self) -> None:
        api = FakeApi()
        repo = "StreamScapeTV/ci-workflows"
        old = {
            "id": 3,
            "name": "ordinary",
            "size_in_bytes": 12,
            "created_at": "2026-08-10T12:00:00Z",
            "workflow_run": {"id": 9},
        }
        api.artifact_rows[repo] = [old]
        api.artifact_current[(repo, 3)] = {**old, "size_in_bytes": 13}
        api.run = {"status": "completed"}
        with self.assertRaisesRegex(
            MaintenanceError,
            "artifact_changed_before_delete",
        ):
            artifacts(
                self.contract,
                api,
                root=ROOT,
                repository_scope="ci-workflows",
                dry_run=False,
                request_id="artifact-drift",
                now=datetime(2026, 8, 12, 12, tzinfo=timezone.utc),
            )
        self.assertEqual(api.deleted_artifacts, [])

    def test_branch_hygiene_deletes_only_exact_merged_unprotected_tip_and_replays(self) -> None:
        api = FakeApi()
        api.pull = pull()
        api.branch = {"protected": False, "commit": {"sha": SHA}}
        result = branches(
            self.contract,
            api,
            project_id="agent-state",
            pr_number=20,
            expected_head_sha=SHA,
            dry_run=False,
            request_id="branch-delete",
        )
        self.assertEqual(result.mutation_count, 1)
        self.assertEqual(
            api.deleted_branches,
            [("StreamScapeTV/agent-state", "issue/20-maintenance-abcd")],
        )
        replay = branches(
            self.contract,
            api,
            project_id="agent-state",
            pr_number=20,
            expected_head_sha=SHA,
            dry_run=False,
            request_id="branch-replay",
        )
        self.assertEqual(replay.mutation_count, 0)
        self.assertEqual(
            replay.decisions[0]["reason"],
            "branch_already_absent",
        )

    def test_branch_hygiene_rejects_unmerged_protected_and_changed(self) -> None:
        for scenario in ("unmerged", "protected", "changed"):
            with self.subTest(scenario=scenario):
                api = FakeApi()
                api.pull = pull(merged=scenario != "unmerged")
                api.branch = {
                    "protected": scenario == "protected",
                    "commit": {
                        "sha": "b" * 40 if scenario == "changed" else SHA
                    },
                }
                with self.assertRaises(MaintenanceError):
                    branches(
                        self.contract,
                        api,
                        project_id="agent-state",
                        pr_number=20,
                        expected_head_sha=SHA,
                        dry_run=False,
                        request_id="branch-reject",
                    )
                self.assertEqual(api.deleted_branches, [])

    def test_conformance_report_is_inventory_driven_and_idempotent(self) -> None:
        api = FakeApi()
        api.workflow_files["StreamScapeTV/ci-workflows"] = [
            ".github/workflows/new-unregistered.yml"
        ]
        dry = conformance(
            self.contract,
            api,
            root=ROOT,
            repository_scope="ci-workflows",
            dry_run=True,
            request_id="conformance-dry",
        )
        kinds = {item["kind"] for item in dry.decisions}
        self.assertIn("unregistered_live_workflow", kinds)
        self.assertIn("missing_inventory_workflow", kinds)
        first = conformance(
            self.contract,
            api,
            root=ROOT,
            repository_scope="ci-workflows",
            dry_run=False,
            request_id="conformance-write",
        )
        second = conformance(
            self.contract,
            api,
            root=ROOT,
            repository_scope="ci-workflows",
            dry_run=False,
            request_id="conformance-update",
        )
        self.assertEqual(first.report_issue_url, "https://example.invalid/77")
        self.assertEqual(len(api.created_issues), 1)
        self.assertEqual(api.updated_issues, [77])

    def _retry_api(self, logs: str) -> FakeApi:
        api = FakeApi()
        api.run = {
            "id": 101,
            "name": "Central workflow self-check",
            "workflow_id": 328043909,
            "path": ".github/workflows/self-check.yml",
            "event": "push",
            "status": "completed",
            "conclusion": "failure",
            "run_attempt": 1,
            "head_branch": "main",
            "head_sha": SHA,
            "head_repository": {"full_name": "StreamScapeTV/ci-workflows"},
            "pull_requests": [],
        }
        api.branch = {"protected": True, "commit": {"sha": SHA}}
        api.jobs = [
            {
                "id": 201,
                "conclusion": "failure",
                "labels": ["self-hosted", "linux"],
                "steps": [{"name": "Set up job", "conclusion": "failure"}],
            }
        ]
        api.logs[201] = logs
        return api

    def test_runner_retry_accepts_only_proven_infrastructure_failure_and_revalidates(self) -> None:
        api = self._retry_api("The self-hosted runner was lost")
        result = runner_retry(
            self.contract,
            api,
            root=ROOT,
            project_id="ci-workflows",
            run_id=101,
            expected_head_sha=SHA,
            dry_run=False,
            request_id="retry-infra",
        )
        self.assertEqual(result.mutation_count, 1)
        self.assertEqual(api.reruns, [("StreamScapeTV/ci-workflows", 101)])

    def test_runner_retry_rejects_deterministic_product_failure(self) -> None:
        api = self._retry_api("Process completed with exit code 1. Tests failed")
        with self.assertRaisesRegex(
            MaintenanceError,
            "deterministic_or_unproven_failure",
        ):
            runner_retry(
                self.contract,
                api,
                root=ROOT,
                project_id="ci-workflows",
                run_id=101,
                expected_head_sha=SHA,
                dry_run=False,
                request_id="retry-product",
            )
        self.assertEqual(api.reruns, [])

    def test_http_client_honors_retry_after_and_link_pagination(self) -> None:
        sleeps: list[float] = []
        calls = 0

        class Response:
            status = 200

            def __init__(self, body: bytes, headers: Message):
                self.body = body
                self.headers = headers

            def read(self):
                return self.body

            def getcode(self):
                return self.status

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

        def opener(request, timeout=30):
            nonlocal calls
            calls += 1
            if calls == 1:
                headers = Message()
                headers["Retry-After"] = "1"
                raise urllib.error.HTTPError(
                    request.full_url,
                    429,
                    "rate",
                    headers,
                    io.BytesIO(b"{}"),
                )
            headers = Message()
            if calls == 2:
                headers["Link"] = '<https://api.github.com/page2>; rel="next"'
            return Response(json.dumps([{"id": calls}]).encode(), headers)

        api = GitHubApi("token", opener=opener, sleep=sleeps.append)
        rows = api.paginate("/page1")
        self.assertEqual(len(rows), 2)
        self.assertEqual(sleeps, [1.0])


if __name__ == "__main__":
    unittest.main()
