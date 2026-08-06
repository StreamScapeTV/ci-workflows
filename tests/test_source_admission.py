from __future__ import annotations

import json
import re
import sys
import unittest
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ci_workflows import source  # noqa: E402

A = "a" * 40
B = "b" * 40
C = "c" * 40
D = "d" * 40
E = "e" * 40
F = "f" * 40
ZERO = "0" * 40
ONE = "1" * 40
REPOSITORY = "StreamScapeTV/iptv-backend"
FORK = "fork-user/iptv-backend"


class FakeProvider:
    def __init__(self) -> None:
        self.default_branch = "main"
        self.permissions = {"maintainer": "maintain"}
        self.branches = {"main": A, "develop": D}
        self.commits = {
            (REPOSITORY, A),
            (REPOSITORY, C),
            (REPOSITORY, D),
            (REPOSITORY, E),
            (REPOSITORY, ZERO),
            (REPOSITORY, ONE),
            (FORK, B),
        }
        self.pull = {
            "number": 17,
            "head": {"sha": B, "repo": {"full_name": FORK}},
            "base": {
                "ref": "main",
                "sha": A,
                "repo": {"full_name": REPOSITORY},
            },
            "merge_commit_sha": C,
        }
        self.tag_ref_value: Mapping[str, Any] = {
            "object": {"type": "tag", "sha": F}
        }
        self.tag_objects: dict[str, Mapping[str, Any]] = {
            F: {"object": {"type": "commit", "sha": ZERO}}
        }

    def repository(self, repository: str) -> Mapping[str, Any]:
        assert repository == REPOSITORY
        return {"full_name": repository, "default_branch": self.default_branch}

    def collaborator_permission(self, repository: str, actor: str) -> str:
        assert repository == REPOSITORY
        return self.permissions.get(actor, "none")

    def pull_request(self, repository: str, number: int) -> Mapping[str, Any]:
        assert repository == REPOSITORY
        assert number == 17
        return self.pull

    def commit(self, repository: str, sha: str) -> Mapping[str, Any]:
        if (repository, sha) not in self.commits:
            raise source.SourceAdmissionError("github_metadata_unavailable")
        return {"sha": sha}

    def branch_sha(self, repository: str, branch: str) -> str:
        assert repository == REPOSITORY
        return self.branches[branch]

    def tag_ref(self, repository: str, tag_name: str) -> Mapping[str, Any]:
        assert repository == REPOSITORY
        assert tag_name == "v1.2.3"
        return self.tag_ref_value

    def tag_object(self, repository: str, sha: str) -> Mapping[str, Any]:
        assert repository == REPOSITORY
        return self.tag_objects[sha]


class SourceAdmissionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.contract = source.load_contract(ROOT)
        cls.fixture_root = ROOT / "tests/fixtures/source-admission"
        cls.cases = json.loads((cls.fixture_root / "cases.json").read_text(encoding="utf-8"))

    def setUp(self) -> None:
        self.provider = FakeProvider()

    def event(
        self,
        fixture: str,
        event_name: str,
        *,
        sha: str,
        ref: str = "",
        ref_name: str = "",
        ref_type: str = "",
    ) -> source.EventContext:
        events = json.loads(
            (self.fixture_root / "events.json").read_text(encoding="utf-8")
        )
        payload = events[fixture]
        return source.EventContext(
            event_name=event_name,
            repository=REPOSITORY,
            sha=sha,
            ref=ref,
            ref_name=ref_name,
            ref_type=ref_type,
            actor="maintainer",
            triggering_actor="maintainer",
            workflow_sha=A,
            payload=payload,
        )

    def inputs(self, **overrides: Any) -> source.SourceInputs:
        raw: dict[str, Any] = {"source_mode": "auto", "history_depth": 1}
        raw.update(overrides)
        return source.validate_inputs(raw, self.contract)

    def test_positive_event_modes_resolve_typed_exact_source(self) -> None:
        pr_head = source.admit_source(
            self.inputs(source_mode="pr-head"),
            self.event("pull_request_fork.json", "pull_request", sha=C),
            self.provider,
        )
        self.assertEqual(pr_head.trust_mode, source.TrustMode.UNTRUSTED_VALIDATION)
        self.assertEqual(pr_head.source_repository, FORK)
        self.assertEqual(pr_head.source_sha, B)
        self.assertEqual(pr_head.pr_base_sha, A)
        self.assertTrue(pr_head.requires_freshness)

        pr_merge = source.admit_source(
            self.inputs(source_mode="pr-merge", requested_sha=C),
            self.event("pull_request_fork.json", "pull_request", sha=C),
            self.provider,
        )
        self.assertEqual(pr_merge.source_repository, REPOSITORY)
        self.assertEqual(pr_merge.source_sha, C)

        push = source.admit_source(
            self.inputs(
                source_mode="push",
                expected_branch="develop",
                caller_integration_branch="develop",
            ),
            self.event(
                "push_develop.json",
                "push",
                sha=D,
                ref="refs/heads/develop",
                ref_name="develop",
                ref_type="branch",
            ),
            self.provider,
        )
        self.assertEqual(push.trust_mode, source.TrustMode.TRUSTED_VALIDATION)
        self.assertEqual(push.source_sha, D)

        manual = source.admit_source(
            self.inputs(source_mode="manual", requested_sha=E),
            self.event(
                "workflow_dispatch_exact.json",
                "workflow_dispatch",
                sha=A,
                ref="refs/heads/main",
                ref_name="main",
                ref_type="branch",
            ),
            self.provider,
        )
        self.assertEqual(manual.source_sha, E)
        self.assertFalse(manual.requires_freshness)

        workflow_call = source.admit_source(
            self.inputs(source_mode="workflow-call", requested_sha=ONE),
            self.event("workflow_call_exact.json", "workflow_call", sha=ONE),
            self.provider,
        )
        self.assertEqual(workflow_call.source_sha, ONE)

        release = source.admit_source(
            self.inputs(source_mode="tag", release_contract="backend"),
            self.event(
                "tag_annotated.json",
                "push",
                sha=F,
                ref="refs/tags/v1.2.3",
                ref_name="v1.2.3",
                ref_type="tag",
            ),
            self.provider,
        )
        self.assertEqual(release.trust_mode, source.TrustMode.TAG_RELEASE)
        self.assertEqual(release.tag_object_sha, F)
        self.assertEqual(release.tag_commit_sha, ZERO)
        self.assertEqual(release.source_sha, ZERO)

    def test_reusable_call_mode_preserves_original_event_trust(self) -> None:
        pr = source.admit_source(
            self.inputs(source_mode="workflow-call", requested_sha=B),
            self.event("pull_request_fork.json", "pull_request", sha=C),
            self.provider,
        )
        self.assertEqual(pr.trust_mode, source.TrustMode.UNTRUSTED_VALIDATION)
        self.assertEqual(pr.source_repository, FORK)
        self.assertEqual(pr.source_sha, B)

        push = source.admit_source(
            self.inputs(
                source_mode="workflow-call",
                requested_sha=D,
                expected_branch="develop",
                caller_integration_branch="develop",
            ),
            self.event(
                "push_develop.json",
                "push",
                sha=D,
                ref="refs/heads/develop",
                ref_name="develop",
                ref_type="branch",
            ),
            self.provider,
        )
        self.assertEqual(push.trust_mode, source.TrustMode.TRUSTED_VALIDATION)
        self.assertEqual(push.source_sha, D)

    def test_trusted_metadata_events_never_admit_pull_request_source(self) -> None:
        for fixture, event_name in (
            ("workflow_run.json", "workflow_run"),
            ("issue_comment.json", "issue_comment"),
            ("pull_request_target.json", "pull_request_target"),
        ):
            with self.subTest(event=event_name):
                result = source.admit_source(
                    self.inputs(
                        source_mode="trusted-maintenance",
                        pr_number=17,
                        expected_pr_head_sha=B,
                        expected_pr_base_sha=A,
                        expected_pr_merge_sha=C,
                    ),
                    self.event(fixture, event_name, sha=A),
                    self.provider,
                )
                self.assertEqual(result.trust_mode, source.TrustMode.TRUSTED_MAINTENANCE)
                self.assertEqual(result.source_repository, REPOSITORY)
                self.assertEqual(result.source_sha, A)
                self.assertNotEqual(result.source_sha, result.pr_head_sha)
                self.assertTrue(result.requires_freshness)

    def test_trust_mode_is_derived_and_consumer_escalation_fails_closed(self) -> None:
        for raw, expected in (
            (
                {"source_mode": "trusted-maintenance"},
                "source_mode_event_mismatch",
            ),
            (
                {"source_mode": "manual", "requested_sha": "main"},
                "requested_sha_must_be_full_sha",
            ),
            (
                {"source_mode": "manual", "requested_sha": "refs/heads/main"},
                "requested_sha_must_be_full_sha",
            ),
            (
                {"source_mode": "manual", "trust_mode": "tag-release"},
                "unsupported_source_input",
            ),
            (
                {"source_mode": "manual", "artifact_path": "result.zip"},
                "unsupported_source_input",
            ),
        ):
            with self.subTest(expected=expected, raw=raw):
                try:
                    inputs = source.validate_inputs(raw, self.contract)
                    source.admit_source(
                        inputs,
                        self.event("pull_request_fork.json", "pull_request", sha=C),
                        self.provider,
                    )
                except source.SourceAdmissionError as error:
                    self.assertEqual(error.instruction, expected)
                else:
                    self.fail("unsafe source request unexpectedly succeeded")

    def test_stale_pull_request_and_tag_mismatch_are_rejected(self) -> None:
        stale = json.loads(json.dumps(self.provider.pull))
        stale["head"]["sha"] = "9" * 40
        self.provider.pull = stale
        with self.assertRaises(source.SourceAdmissionError) as caught:
            source.admit_source(
                self.inputs(source_mode="pr-head"),
                self.event("pull_request_fork.json", "pull_request", sha=C),
                self.provider,
            )
        self.assertEqual(caught.exception.instruction, "stale_pr_head")

        self.provider = FakeProvider()
        with self.assertRaises(source.SourceAdmissionError) as caught:
            source.admit_source(
                self.inputs(source_mode="tag", release_contract="backend"),
                self.event(
                    "tag_annotated.json",
                    "push",
                    sha="9" * 40,
                    ref="refs/tags/v1.2.3",
                    ref_name="v1.2.3",
                    ref_type="tag",
                ),
                self.provider,
            )
        self.assertEqual(caught.exception.instruction, "tag_event_sha_mismatch")

    def test_current_evidence_can_be_revalidated_immediately_before_privilege(self) -> None:
        admitted = source.admit_source(
            self.inputs(source_mode="pr-head"),
            self.event("pull_request_fork.json", "pull_request", sha=C),
            self.provider,
        )
        source.revalidate_admission(admitted, self.provider)
        changed = json.loads(json.dumps(self.provider.pull))
        changed["base"]["sha"] = "8" * 40
        self.provider.pull = changed
        with self.assertRaises(source.SourceAdmissionError) as caught:
            source.revalidate_admission(admitted, self.provider)
        self.assertEqual(caught.exception.instruction, "stale_pr_base")

    def test_identifiers_are_stable_bounded_and_safe_for_status_publication(self) -> None:
        first = source.admit_source(
            self.inputs(source_mode="pr-head"),
            self.event("pull_request_fork.json", "pull_request", sha=C),
            self.provider,
        )
        second = source.admit_source(
            self.inputs(source_mode="pr-head"),
            self.event("pull_request_fork.json", "pull_request", sha=C),
            self.provider,
        )
        self.assertEqual(first.request_id, second.request_id)
        self.assertEqual(first.evidence_id, second.evidence_id)
        self.assertRegex(first.request_id, r"^source-[0-9a-f]{24}$")
        self.assertRegex(first.evidence_id, r"^evidence-[0-9a-f]{24}$")
        serialized = json.dumps(first.output_values(), sort_keys=True)
        self.assertNotIn("token", serialized.casefold())
        self.assertNotRegex(serialized, re.compile(r"https?://"))

    def test_fixture_manifest_covers_all_supported_and_negative_modes(self) -> None:
        valid = {row["id"] for row in self.cases["valid"]}
        self.assertEqual(
            valid,
            {
                "fork-pr-head",
                "fork-pr-merge",
                "protected-push",
                "manual-exact",
                "workflow-call-exact",
                "historical-annotated-tag",
                "trusted-workflow-run",
                "trusted-issue-comment",
                "trusted-pull-request-target",
            },
        )
        negative = {row["id"] for row in self.cases["negative"]}
        self.assertEqual(
            negative,
            {
                "fork-privilege-escalation",
                "malformed-sha",
                "mutable-ref",
                "unsafe-artifact",
                "trust-mode-input",
                "stale-pr",
                "tag-mismatch",
                "changed-checkout",
            },
        )


if __name__ == "__main__":
    unittest.main()
