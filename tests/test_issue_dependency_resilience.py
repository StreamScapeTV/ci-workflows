from __future__ import annotations

import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ci_workflows.issue_dependencies import (
    IssueRecord,
    IssueRef,
    ManifestValidationError,
    NativeDependency,
    RepositoryRecord,
    sync_organization_resilient,
)

SCHEMA = json.loads(
    (ROOT / "contracts" / "issue-dependencies.schema.json").read_text()
)


def manifest(repository: str, dependent: int, blocker: int) -> str:
    return (
        "version: 1\n"
        f"repository: {repository}\n"
        "issues:\n"
        f"  {dependent}:\n"
        "    blocked_by:\n"
        f"      - {blocker}\n"
    )


class FakeGateway:
    def __init__(self) -> None:
        self.repositories: list[RepositoryRecord] = []
        self.files: dict[tuple[str, str, str], str] = {}
        self.issues: dict[IssueRef, IssueRecord] = {}
        self.native: dict[IssueRef, list[NativeDependency]] = {}
        self.add_calls: list[tuple[IssueRef, IssueRecord]] = []
        self.remove_calls: list[tuple[IssueRef, NativeDependency]] = []

    def list_repositories(self):
        return tuple(self.repositories)

    def read_file(self, repository, path, ref):
        return self.files.get((repository, path, ref))

    def get_issue(self, ref):
        return self.issues.get(ref)

    def list_blocked_by(self, ref):
        return tuple(self.native.get(ref, []))

    def add_blocked_by(self, dependent, blocker):
        self.add_calls.append((dependent, blocker))
        self.native.setdefault(dependent, []).append(
            NativeDependency(blocker.ref.url, blocker.issue_id)
        )

    def remove_blocked_by(self, dependent, blocker):
        self.remove_calls.append((dependent, blocker))
        self.native[dependent] = [
            item
            for item in self.native.get(dependent, [])
            if item.issue_id != blocker.issue_id
        ]


def record(
    repository: str,
    number: int,
    issue_id: int,
    *,
    state: str = "open",
    state_reason: str | None = None,
) -> IssueRecord:
    ref = IssueRef(repository, number)
    return IssueRecord(ref, issue_id, state, state_reason, False)


def two_repository_gateway(stale_reason: str) -> tuple[FakeGateway, IssueRef, IssueRef]:
    gateway = FakeGateway()
    stale_repo = "StreamScapeTV/stale"
    healthy_repo = "StreamScapeTV/healthy"
    gateway.repositories = [
        RepositoryRecord(healthy_repo, "main"),
        RepositoryRecord(stale_repo, "main"),
    ]
    for repository in (healthy_repo, stale_repo):
        gateway.files[(repository, "AGENTS.md", "main")] = (
            "- Protected integration branch: `main`\n"
        )
    gateway.files[(stale_repo, "ISSUE_DEPENDENCIES.yml", "main")] = manifest(
        stale_repo, 10, 2
    )
    gateway.files[(healthy_repo, "ISSUE_DEPENDENCIES.yml", "main")] = manifest(
        healthy_repo, 20, 3
    )

    stale_dependent = record(stale_repo, 10, 1010)
    stale_blocker = record(
        stale_repo,
        2,
        1002,
        state="closed",
        state_reason=stale_reason,
    )
    healthy_dependent = record(healthy_repo, 20, 2020)
    healthy_blocker = record(healthy_repo, 3, 2003)
    gateway.issues = {
        stale_dependent.ref: stale_dependent,
        stale_blocker.ref: stale_blocker,
        healthy_dependent.ref: healthy_dependent,
        healthy_blocker.ref: healthy_blocker,
    }
    return gateway, stale_dependent.ref, healthy_dependent.ref


class ResilientOrganizationSyncTests(unittest.TestCase):
    def test_stale_closed_blocker_quarantines_only_affected_repository(self):
        for reason in ("duplicate", "not_planned"):
            with self.subTest(reason=reason):
                gateway, stale_dependent, healthy_dependent = two_repository_gateway(reason)
                warnings: list[str] = []

                summary = sync_organization_resilient(
                    gateway,
                    SCHEMA,
                    warn=warnings.append,
                )

                self.assertEqual(summary.repositories_scanned, 2)
                self.assertEqual(summary.managed_repositories, 2)
                self.assertEqual(summary.mutations, 1)
                self.assertEqual(
                    [call[0] for call in gateway.add_calls],
                    [healthy_dependent],
                )
                self.assertNotIn(stale_dependent, gateway.native)
                self.assertEqual(
                    [item.url for item in gateway.native[healthy_dependent]],
                    [f"https://github.com/{healthy_dependent.repository}/issues/3"],
                )
                self.assertEqual(len(warnings), 1)
                self.assertIn("StreamScapeTV/stale", warnings[0])
                self.assertIn(
                    "https://github.com/StreamScapeTV/stale/issues/2",
                    warnings[0],
                )
                self.assertIn(reason, warnings[0])
                self.assertIn("update ISSUE_DEPENDENCIES.yml", warnings[0])

    def test_unsupported_closed_reason_remains_fatal_before_writes(self):
        gateway, _, _ = two_repository_gateway("unexpected")
        with self.assertRaisesRegex(
            ManifestValidationError,
            "unsupported closed state reason",
        ):
            sync_organization_resilient(gateway, SCHEMA)
        self.assertEqual(gateway.add_calls, [])
        self.assertEqual(gateway.remove_calls, [])


if __name__ == "__main__":
    unittest.main()
