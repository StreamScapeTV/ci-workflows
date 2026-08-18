from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ci_workflows import source  # noqa: E402

BASE = "a" * 40
HEAD = "b" * 40
EVENT_MERGE = "c" * 40
CURRENT_MERGE = "d" * 40
STALE_HEAD = "e" * 40
STALE_BASE = "f" * 40
REPOSITORY = "StreamScapeTV/streamscape-media"


class MergeDriftProvider:
    def __init__(self) -> None:
        self.pull: dict[str, Any] = self.pull_metadata(merge_sha=EVENT_MERGE)
        self.pull_reads = 0
        self.commits = {
            BASE,
            HEAD,
            EVENT_MERGE,
            CURRENT_MERGE,
            STALE_HEAD,
            STALE_BASE,
        }

    @staticmethod
    def pull_metadata(
        *,
        head_sha: str = HEAD,
        base_sha: str = BASE,
        merge_sha: str | None = EVENT_MERGE,
    ) -> dict[str, Any]:
        return {
            "number": 524,
            "head": {"sha": head_sha, "repo": {"full_name": REPOSITORY}},
            "base": {
                "ref": "develop",
                "sha": base_sha,
                "repo": {"full_name": REPOSITORY},
            },
            "merge_commit_sha": merge_sha,
        }

    def repository(self, repository: str) -> Mapping[str, Any]:
        self._require_repository(repository)
        return {"full_name": repository, "default_branch": "develop"}

    def collaborator_permission(self, repository: str, actor: str) -> str:
        self._require_repository(repository)
        return "maintain"

    def pull_request(self, repository: str, number: int) -> Mapping[str, Any]:
        self._require_repository(repository)
        if number != 524:
            raise AssertionError(f"unexpected pull request {number}")
        self.pull_reads += 1
        return self.pull

    def commit(self, repository: str, sha: str) -> Mapping[str, Any]:
        self._require_repository(repository)
        if sha not in self.commits:
            raise source.SourceAdmissionError("github_metadata_unavailable")
        return {"sha": sha}

    def branch_sha(self, repository: str, branch: str) -> str:
        self._require_repository(repository)
        if branch != "develop":
            raise AssertionError(f"unexpected branch {branch}")
        return BASE

    def tag_ref(self, repository: str, tag_name: str) -> Mapping[str, Any]:
        raise AssertionError("tag lookup is not expected")

    def tag_object(self, repository: str, sha: str) -> Mapping[str, Any]:
        raise AssertionError("tag lookup is not expected")

    @staticmethod
    def _require_repository(repository: str) -> None:
        if repository != REPOSITORY:
            raise AssertionError(f"unexpected repository {repository}")


class SourceAdmissionMergeDriftTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.contract = source.load_contract(ROOT)

    def setUp(self) -> None:
        self.provider = MergeDriftProvider()

    def event(self) -> source.EventContext:
        return source.EventContext(
            event_name="pull_request",
            repository=REPOSITORY,
            sha=EVENT_MERGE,
            ref="refs/pull/524/merge",
            ref_name="524/merge",
            ref_type="branch",
            actor="maintainer",
            triggering_actor="maintainer",
            workflow_sha=BASE,
            payload={
                "pull_request": MergeDriftProvider.pull_metadata(
                    merge_sha=EVENT_MERGE
                )
            },
        )

    def inputs(self, **overrides: Any) -> source.SourceInputs:
        raw: dict[str, Any] = {
            "source_mode": "pr-head",
            "requested_sha": HEAD,
            "expected_branch": "develop",
            "history_depth": 1,
        }
        raw.update(overrides)
        return source.validate_inputs(raw, self.contract)

    def test_pr_head_ignores_unselected_synthetic_merge_regeneration(self) -> None:
        self.provider.pull = MergeDriftProvider.pull_metadata(
            merge_sha=CURRENT_MERGE
        )

        result = source.admit_source(self.inputs(), self.event(), self.provider)

        self.assertEqual(result.source_sha, HEAD)
        self.assertEqual(result.pr_head_sha, HEAD)
        self.assertEqual(result.pr_base_sha, BASE)
        self.assertEqual(result.pr_merge_sha, CURRENT_MERGE)

    def test_stale_pr_head_still_fails_closed_when_merge_also_changed(self) -> None:
        self.provider.pull = MergeDriftProvider.pull_metadata(
            head_sha=STALE_HEAD,
            merge_sha=CURRENT_MERGE,
        )

        with self.assertRaises(source.SourceAdmissionError) as caught:
            source.admit_source(self.inputs(), self.event(), self.provider)

        self.assertEqual(caught.exception.instruction, "stale_pr_head")

    def test_stale_pr_base_still_fails_closed_when_merge_also_changed(self) -> None:
        self.provider.pull = MergeDriftProvider.pull_metadata(
            base_sha=STALE_BASE,
            merge_sha=CURRENT_MERGE,
        )

        with self.assertRaises(source.SourceAdmissionError) as caught:
            source.admit_source(
                self.inputs(source_mode="pr-merge", requested_sha=None),
                self.event(),
                self.provider,
            )

        self.assertEqual(caught.exception.instruction, "stale_pr_base")

    def test_pr_merge_selects_one_current_snapshot_after_regeneration(self) -> None:
        self.provider.pull = MergeDriftProvider.pull_metadata(
            merge_sha=CURRENT_MERGE
        )

        result = source.admit_source(
            self.inputs(source_mode="pr-merge", requested_sha=None),
            self.event(),
            self.provider,
        )

        self.assertEqual(result.source_sha, CURRENT_MERGE)
        self.assertEqual(result.pr_head_sha, HEAD)
        self.assertEqual(result.pr_base_sha, BASE)
        self.assertEqual(result.pr_merge_sha, CURRENT_MERGE)
        self.assertEqual(self.provider.pull_reads, 1)

    def test_pr_merge_keeps_explicit_requested_merge_exact(self) -> None:
        self.provider.pull = MergeDriftProvider.pull_metadata(
            merge_sha=CURRENT_MERGE
        )

        current = source.admit_source(
            self.inputs(
                source_mode="pr-merge",
                requested_sha=CURRENT_MERGE,
            ),
            self.event(),
            self.provider,
        )
        self.assertEqual(current.source_sha, CURRENT_MERGE)

        with self.assertRaises(source.SourceAdmissionError) as caught:
            source.admit_source(
                self.inputs(
                    source_mode="pr-merge",
                    requested_sha=EVENT_MERGE,
                ),
                self.event(),
                self.provider,
            )

        self.assertEqual(caught.exception.instruction, "requested_sha_mismatch")

    def test_explicit_expected_merge_remains_fail_closed_for_pr_head(self) -> None:
        self.provider.pull = MergeDriftProvider.pull_metadata(
            merge_sha=CURRENT_MERGE
        )

        with self.assertRaises(source.SourceAdmissionError) as caught:
            source.admit_source(
                self.inputs(
                    pr_number=524,
                    expected_pr_head_sha=HEAD,
                    expected_pr_base_sha=BASE,
                    expected_pr_merge_sha=EVENT_MERGE,
                ),
                self.event(),
                self.provider,
            )

        self.assertEqual(caught.exception.instruction, "stale_pr_merge")

    def test_pr_head_revalidation_ignores_merge_only_regeneration(self) -> None:
        admitted = source.admit_source(self.inputs(), self.event(), self.provider)
        self.provider.pull = MergeDriftProvider.pull_metadata(
            merge_sha=CURRENT_MERGE
        )

        source.revalidate_admission(admitted, self.provider)

    def test_pr_merge_revalidation_keeps_merge_identity_strict(self) -> None:
        admitted = source.admit_source(
            self.inputs(source_mode="pr-merge", requested_sha=EVENT_MERGE),
            self.event(),
            self.provider,
        )
        self.provider.pull = MergeDriftProvider.pull_metadata(
            merge_sha=CURRENT_MERGE
        )

        with self.assertRaises(source.SourceAdmissionError) as caught:
            source.revalidate_admission(admitted, self.provider)

        self.assertEqual(caught.exception.instruction, "stale_pr_merge")


if __name__ == "__main__":
    unittest.main()
