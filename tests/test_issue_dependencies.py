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
    ConvergenceError,
    IssueRecord,
    IssueRef,
    ManifestValidationError,
    NativeDependency,
    RepositoryRecord,
    load_manifest,
    parse_protected_integration_branch,
    sync_organization,
)


SCHEMA = json.loads(
    (ROOT / "contracts" / "issue-dependencies.schema.json").read_text()
)


def manifest(repo: str, body: str) -> str:
    return f"version: 1\nrepository: {repo}\nissues:\n{body}"


class FakeGateway:
    def __init__(self) -> None:
        self.repositories: list[RepositoryRecord] = []
        self.files: dict[tuple[str, str, str], str] = {}
        self.issues: dict[IssueRef, IssueRecord] = {}
        self.native: dict[IssueRef, list[NativeDependency]] = {}
        self.add_calls: list[tuple[IssueRef, IssueRecord]] = []
        self.remove_calls: list[tuple[IssueRef, NativeDependency]] = []
        self.mutation_order: list[str] = []
        self.force_readback: dict[IssueRef, list[NativeDependency]] = {}
        self._after_mutation = False

    def list_repositories(self):
        return tuple(self.repositories)

    def read_file(self, repository, path, ref):
        return self.files.get((repository, path, ref))

    def get_issue(self, ref):
        return self.issues.get(ref)

    def list_blocked_by(self, ref):
        if self._after_mutation and ref in self.force_readback:
            return tuple(self.force_readback[ref])
        return tuple(self.native.get(ref, []))

    def add_blocked_by(self, dependent, blocker):
        self.mutation_order.append("add")
        self.add_calls.append((dependent, blocker))
        self.native.setdefault(dependent, []).append(
            NativeDependency(blocker.ref.url, blocker.issue_id)
        )
        self._after_mutation = True

    def remove_blocked_by(self, dependent, blocker):
        self.mutation_order.append("remove")
        self.remove_calls.append((dependent, blocker))
        self.native[dependent] = [
            item
            for item in self.native.get(dependent, [])
            if item.issue_id != blocker.issue_id
        ]
        self._after_mutation = True


def issue(
    repository: str,
    number: int,
    issue_id: int,
    *,
    state: str = "open",
    state_reason: str | None = None,
    pr: bool = False,
) -> IssueRecord:
    ref = IssueRef(repository, number)
    return IssueRecord(
        ref=ref,
        issue_id=issue_id,
        state=state,
        state_reason=state_reason,
        is_pull_request=pr,
    )


def managed_gateway(
    *,
    repo: str = "StreamScapeTV/demo",
    branch: str = "main",
    manifest_text: str,
) -> FakeGateway:
    gateway = FakeGateway()
    gateway.repositories.append(RepositoryRecord(repo, branch))
    gateway.files[(repo, "AGENTS.md", branch)] = (
        f"# AGENTS\n- Protected integration branch: `{branch}`\n"
    )
    gateway.files[(repo, "ISSUE_DEPENDENCIES.yml", branch)] = manifest_text
    return gateway


class ProtectedBranchTests(unittest.TestCase):
    def test_exact_standard_declaration(self):
        self.assertEqual(
            parse_protected_integration_branch(
                "# x\n- Protected integration branch: `develop`\n"
            ),
            "develop",
        )

    def test_missing_or_duplicate_declaration_is_unmanaged(self):
        self.assertIsNone(parse_protected_integration_branch("# no declaration\n"))
        text = (
            "- Protected integration branch: `main`\n"
            "- Protected integration branch: `develop`\n"
        )
        self.assertIsNone(parse_protected_integration_branch(text))

    def test_unsafe_branch_is_unmanaged(self):
        self.assertIsNone(
            parse_protected_integration_branch(
                "- Protected integration branch: `../main`\n"
            )
        )


class ManifestContractTests(unittest.TestCase):
    def test_accepts_same_repo_cross_repo_and_explicit_empty(self):
        text = manifest(
            "StreamScapeTV/demo",
            "  10:\n"
            "    blocked_by:\n"
            "      - 2\n"
            "      - https://github.com/StreamScapeTV/other/issues/3\n"
            "  11:\n"
            "    blocked_by: []\n",
        )
        loaded = load_manifest(
            text,
            SCHEMA,
            expected_repository="StreamScapeTV/demo",
            integration_branch="main",
        )
        self.assertEqual([item.dependent.number for item in loaded.issues], [10, 11])
        self.assertEqual(
            [ref.url for ref in loaded.issues[0].blockers],
            [
                "https://github.com/StreamScapeTV/demo/issues/2",
                "https://github.com/StreamScapeTV/other/issues/3",
            ],
        )

    def test_unknown_root_field_rejected_by_schema(self):
        text = (
            "version: 1\nrepository: StreamScapeTV/demo\nowner: Agent 1\nissues: {}\n"
        )
        with self.assertRaisesRegex(ManifestValidationError, "unsupported key"):
            load_manifest(
                text,
                SCHEMA,
                expected_repository="StreamScapeTV/demo",
                integration_branch="main",
            )

    def test_unknown_issue_field_rejected_by_schema(self):
        text = manifest(
            "StreamScapeTV/demo",
            "  10:\n    blocked_by: []\n    status: blocked\n",
        )
        with self.assertRaisesRegex(ManifestValidationError, "unsupported key"):
            load_manifest(
                text,
                SCHEMA,
                expected_repository="StreamScapeTV/demo",
                integration_branch="main",
            )

    def test_repository_mismatch_rejected(self):
        text = manifest("StreamScapeTV/other", "  10:\n    blocked_by: []\n")
        with self.assertRaisesRegex(ManifestValidationError, "does not match"):
            load_manifest(
                text,
                SCHEMA,
                expected_repository="StreamScapeTV/demo",
                integration_branch="main",
            )

    def test_duplicate_yaml_mapping_key_rejected(self):
        text = (
            "version: 1\n"
            "repository: StreamScapeTV/demo\n"
            "issues:\n"
            "  10:\n"
            "    blocked_by: []\n"
            "  10:\n"
            "    blocked_by: []\n"
        )
        with self.assertRaisesRegex(ManifestValidationError, "duplicate YAML"):
            load_manifest(
                text,
                SCHEMA,
                expected_repository="StreamScapeTV/demo",
                integration_branch="main",
            )

    def test_semantic_duplicate_number_and_url_rejected(self):
        text = manifest(
            "StreamScapeTV/demo",
            "  10:\n"
            "    blocked_by:\n"
            "      - 2\n"
            "      - https://github.com/StreamScapeTV/demo/issues/2\n",
        )
        with self.assertRaisesRegex(ManifestValidationError, "duplicate blocker edge"):
            load_manifest(
                text,
                SCHEMA,
                expected_repository="StreamScapeTV/demo",
                integration_branch="main",
            )

    def test_self_edge_rejected(self):
        text = manifest(
            "StreamScapeTV/demo",
            "  10:\n    blocked_by:\n      - 10\n",
        )
        with self.assertRaisesRegex(ManifestValidationError, "self-dependency"):
            load_manifest(
                text,
                SCHEMA,
                expected_repository="StreamScapeTV/demo",
                integration_branch="main",
            )

    def test_pr_style_url_and_foreign_org_rejected(self):
        for blocker in (
            "https://github.com/StreamScapeTV/demo/pull/2",
            "https://github.com/OtherOrg/demo/issues/2",
        ):
            text = manifest(
                "StreamScapeTV/demo",
                f"  10:\n    blocked_by:\n      - {blocker}\n",
            )
            with self.subTest(blocker=blocker):
                with self.assertRaises(ManifestValidationError):
                    load_manifest(
                        text,
                        SCHEMA,
                        expected_repository="StreamScapeTV/demo",
                        integration_branch="main",
                    )

    def test_multiple_yaml_documents_rejected(self):
        text = (
            "version: 1\nrepository: StreamScapeTV/demo\nissues: {}\n"
            "---\n"
            "version: 1\nrepository: StreamScapeTV/demo\nissues: {}\n"
        )
        with self.assertRaisesRegex(ManifestValidationError, "exactly one YAML"):
            load_manifest(
                text,
                SCHEMA,
                expected_repository="StreamScapeTV/demo",
                integration_branch="main",
            )


class ReconciliationTests(unittest.TestCase):
    def test_unmanaged_repositories_are_silent_successful_skips(self):
        gateway = FakeGateway()
        gateway.repositories = [
            RepositoryRecord("StreamScapeTV/legacy", "main"),
            RepositoryRecord("StreamScapeTV/no-manifest", "main"),
            RepositoryRecord("StreamScapeTV/bad-agents", "main"),
        ]
        gateway.files[("StreamScapeTV/no-manifest", "AGENTS.md", "main")] = (
            "- Protected integration branch: `main`\n"
        )
        gateway.files[("StreamScapeTV/bad-agents", "AGENTS.md", "main")] = (
            "# legacy without standard declaration\n"
        )
        summary = sync_organization(gateway, SCHEMA)
        self.assertEqual(summary.repositories_scanned, 3)
        self.assertEqual(summary.managed_repositories, 0)
        self.assertEqual(summary.mutations, 0)
        self.assertEqual(gateway.add_calls, [])
        self.assertEqual(gateway.remove_calls, [])

    def test_noop_performs_zero_mutations_but_reads_back(self):
        text = manifest(
            "StreamScapeTV/demo",
            "  10:\n    blocked_by:\n      - 2\n",
        )
        gateway = managed_gateway(manifest_text=text)
        dep = issue("StreamScapeTV/demo", 10, 1010)
        blocker = issue("StreamScapeTV/demo", 2, 1002)
        gateway.issues = {dep.ref: dep, blocker.ref: blocker}
        gateway.native[dep.ref] = [NativeDependency(blocker.ref.url, blocker.issue_id)]
        summary = sync_organization(gateway, SCHEMA)
        self.assertEqual(summary.mutations, 0)
        self.assertEqual(gateway.add_calls, [])
        self.assertEqual(gateway.remove_calls, [])

    def test_add_and_remove_same_repo_converge(self):
        text = manifest(
            "StreamScapeTV/demo",
            "  10:\n    blocked_by:\n      - 2\n",
        )
        gateway = managed_gateway(manifest_text=text)
        dep = issue("StreamScapeTV/demo", 10, 1010)
        desired = issue("StreamScapeTV/demo", 2, 1002)
        extra = issue("StreamScapeTV/demo", 3, 1003)
        gateway.issues = {dep.ref: dep, desired.ref: desired}
        gateway.native[dep.ref] = [NativeDependency(extra.ref.url, extra.issue_id)]
        summary = sync_organization(gateway, SCHEMA)
        self.assertEqual(summary.additions, 1)
        self.assertEqual(summary.removals, 1)
        self.assertEqual(summary.mutations, 2)
        self.assertEqual(
            [item.url for item in gateway.native[dep.ref]],
            [desired.ref.url],
        )

    def test_replacement_adds_desired_before_removing_stale_blocker(self):
        text = manifest(
            "StreamScapeTV/demo",
            "  10:\n    blocked_by:\n      - 2\n",
        )
        gateway = managed_gateway(manifest_text=text)
        dep = issue("StreamScapeTV/demo", 10, 1010)
        desired = issue("StreamScapeTV/demo", 2, 1002)
        extra = issue("StreamScapeTV/demo", 3, 1003)
        gateway.issues = {dep.ref: dep, desired.ref: desired}
        gateway.native[dep.ref] = [NativeDependency(extra.ref.url, extra.issue_id)]
        sync_organization(gateway, SCHEMA)
        self.assertEqual(gateway.mutation_order, ["add", "remove"])

    def test_cross_repo_blocker_is_added(self):
        text = manifest(
            "StreamScapeTV/demo",
            "  10:\n"
            "    blocked_by:\n"
            "      - https://github.com/StreamScapeTV/other/issues/3\n",
        )
        gateway = managed_gateway(manifest_text=text)
        dep = issue("StreamScapeTV/demo", 10, 1010)
        blocker = issue("StreamScapeTV/other", 3, 2003)
        gateway.issues = {dep.ref: dep, blocker.ref: blocker}
        summary = sync_organization(gateway, SCHEMA)
        self.assertEqual(summary.additions, 1)
        self.assertEqual(gateway.add_calls[0][1].ref, blocker.ref)

    def test_completed_blocker_is_permitted(self):
        text = manifest(
            "StreamScapeTV/demo",
            "  10:\n    blocked_by:\n      - 2\n",
        )
        gateway = managed_gateway(manifest_text=text)
        dep = issue("StreamScapeTV/demo", 10, 1010)
        blocker = issue(
            "StreamScapeTV/demo",
            2,
            1002,
            state="closed",
            state_reason="completed",
        )
        gateway.issues = {dep.ref: dep, blocker.ref: blocker}
        summary = sync_organization(gateway, SCHEMA)
        self.assertEqual(summary.additions, 1)

    def test_duplicate_or_not_planned_blocker_fails_before_writes(self):
        for reason in ("duplicate", "not_planned"):
            text = manifest(
                "StreamScapeTV/demo",
                "  10:\n    blocked_by:\n      - 2\n",
            )
            gateway = managed_gateway(manifest_text=text)
            dep = issue("StreamScapeTV/demo", 10, 1010)
            blocker = issue(
                "StreamScapeTV/demo",
                2,
                1002,
                state="closed",
                state_reason=reason,
            )
            gateway.issues = {dep.ref: dep, blocker.ref: blocker}
            with self.subTest(reason=reason):
                with self.assertRaisesRegex(ManifestValidationError, "update the manifest"):
                    sync_organization(gateway, SCHEMA)
                self.assertEqual(gateway.add_calls, [])
                self.assertEqual(gateway.remove_calls, [])

    def test_missing_issue_fails_before_native_reads_or_writes(self):
        text = manifest(
            "StreamScapeTV/demo",
            "  10:\n    blocked_by:\n      - 2\n",
        )
        gateway = managed_gateway(manifest_text=text)
        dep = issue("StreamScapeTV/demo", 10, 1010)
        gateway.issues = {dep.ref: dep}
        with self.assertRaisesRegex(ManifestValidationError, "missing issue"):
            sync_organization(gateway, SCHEMA)
        self.assertEqual(gateway.add_calls, [])
        self.assertEqual(gateway.remove_calls, [])

    def test_pull_request_reference_fails_before_writes(self):
        text = manifest(
            "StreamScapeTV/demo",
            "  10:\n    blocked_by:\n      - 2\n",
        )
        gateway = managed_gateway(manifest_text=text)
        dep = issue("StreamScapeTV/demo", 10, 1010)
        blocker = issue("StreamScapeTV/demo", 2, 1002, pr=True)
        gateway.issues = {dep.ref: dep, blocker.ref: blocker}
        with self.assertRaisesRegex(ManifestValidationError, "pull request"):
            sync_organization(gateway, SCHEMA)
        self.assertEqual(gateway.add_calls, [])

    def test_cross_manifest_cycle_fails_before_writes(self):
        gateway = FakeGateway()
        gateway.repositories = [
            RepositoryRecord("StreamScapeTV/a", "main"),
            RepositoryRecord("StreamScapeTV/b", "develop"),
        ]
        gateway.files[("StreamScapeTV/a", "AGENTS.md", "main")] = (
            "- Protected integration branch: `main`\n"
        )
        gateway.files[("StreamScapeTV/b", "AGENTS.md", "develop")] = (
            "- Protected integration branch: `develop`\n"
        )
        gateway.files[("StreamScapeTV/a", "ISSUE_DEPENDENCIES.yml", "main")] = manifest(
            "StreamScapeTV/a",
            "  1:\n"
            "    blocked_by:\n"
            "      - https://github.com/StreamScapeTV/b/issues/2\n",
        )
        gateway.files[
            ("StreamScapeTV/b", "ISSUE_DEPENDENCIES.yml", "develop")
        ] = manifest(
            "StreamScapeTV/b",
            "  2:\n"
            "    blocked_by:\n"
            "      - https://github.com/StreamScapeTV/a/issues/1\n",
        )
        with self.assertRaisesRegex(ManifestValidationError, "cycle"):
            sync_organization(gateway, SCHEMA)
        self.assertEqual(gateway.add_calls, [])

    def test_all_manifests_validate_before_any_write(self):
        gateway = FakeGateway()
        gateway.repositories = [
            RepositoryRecord("StreamScapeTV/a", "main"),
            RepositoryRecord("StreamScapeTV/z", "main"),
        ]
        for repo in ("StreamScapeTV/a", "StreamScapeTV/z"):
            gateway.files[(repo, "AGENTS.md", "main")] = (
                "- Protected integration branch: `main`\n"
            )
        gateway.files[("StreamScapeTV/a", "ISSUE_DEPENDENCIES.yml", "main")] = manifest(
            "StreamScapeTV/a", "  1:\n    blocked_by: []\n"
        )
        gateway.files[("StreamScapeTV/z", "ISSUE_DEPENDENCIES.yml", "main")] = (
            "version: 1\nrepository: StreamScapeTV/z\nissues:\n"
            "  3:\n    blocked_by: []\n    priority: 1\n"
        )
        dep = issue("StreamScapeTV/a", 1, 1)
        gateway.issues[dep.ref] = dep
        with self.assertRaises(ManifestValidationError):
            sync_organization(gateway, SCHEMA)
        self.assertEqual(gateway.add_calls, [])
        self.assertEqual(gateway.remove_calls, [])

    def test_readback_mismatch_fails(self):
        text = manifest(
            "StreamScapeTV/demo",
            "  10:\n    blocked_by:\n      - 2\n",
        )
        gateway = managed_gateway(manifest_text=text)
        dep = issue("StreamScapeTV/demo", 10, 1010)
        blocker = issue("StreamScapeTV/demo", 2, 1002)
        gateway.issues = {dep.ref: dep, blocker.ref: blocker}
        gateway.force_readback[dep.ref] = []
        with self.assertRaisesRegex(ConvergenceError, "did not converge"):
            sync_organization(gateway, SCHEMA)


if __name__ == "__main__":
    unittest.main()
