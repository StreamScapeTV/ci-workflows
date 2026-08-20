from __future__ import annotations

import unittest
from typing import Any, Mapping

from ci_workflows.release_tag_authority import (
    ACTIONS_AUTOMATION_ACTOR,
    ReleaseAuthority,
    ReleaseEvent,
    ReleaseInputs,
    ReleaseTagError,
    revalidate_release_authority,
    resolve_release_authority,
)

COMMIT_A = "a" * 40
COMMIT_B = "b" * 40
CALLER_SHA = "f" * 40
REPOSITORY = "StreamScapeTV/example-product"


def object_payload(object_type: str, sha: str) -> Mapping[str, Any]:
    return {"object": {"type": object_type, "sha": sha}}


class TrackingProvider:
    def __init__(self) -> None:
        self.default_branch = "main"
        self.default_sha = CALLER_SHA
        self.permission = "write"
        self.permission_calls: list[tuple[str, str]] = []
        self.tag_refs: dict[str, Mapping[str, Any]] = {
            "1.2.3": object_payload("commit", COMMIT_A)
        }
        self.commits = {COMMIT_A, COMMIT_B, CALLER_SHA}

    def repository_metadata(self, repository: str) -> Mapping[str, Any]:
        return {
            "full_name": repository,
            "fork": False,
            "default_branch": self.default_branch,
        }

    def collaborator_permission(
        self, repository: str, actor: str
    ) -> Mapping[str, Any]:
        self.permission_calls.append((repository, actor))
        return {"permission": self.permission}

    def branch_sha(self, repository: str, branch: str) -> str:
        if branch != self.default_branch:
            raise ReleaseTagError("default_branch_missing")
        return self.default_sha

    def tag_ref(self, repository: str, tag_name: str) -> Mapping[str, Any]:
        try:
            return self.tag_refs[tag_name]
        except KeyError:
            raise ReleaseTagError("release_tag_missing") from None

    def tag_object(self, repository: str, sha: str) -> Mapping[str, Any]:
        raise ReleaseTagError("annotated_tag_object_missing")

    def commit(self, repository: str, sha: str) -> Mapping[str, Any]:
        if sha not in self.commits:
            raise ReleaseTagError("tag_commit_missing")
        return {"sha": sha}


def dispatch_event(
    *,
    actor: str = ACTIONS_AUTOMATION_ACTOR,
    event_name: str = "workflow_dispatch",
    fork: bool = False,
    ref: str = "refs/heads/main",
    ref_name: str = "main",
    ref_type: str = "branch",
    sha: str = CALLER_SHA,
    workflow_ref: str | None = None,
) -> ReleaseEvent:
    return ReleaseEvent(
        event_name=event_name,
        repository=REPOSITORY,
        event_repository=REPOSITORY,
        event_repository_fork=fork,
        ref=ref,
        ref_type=ref_type,
        ref_name=ref_name,
        sha=sha,
        actor=actor,
        workflow_ref=(
            workflow_ref
            or f"{REPOSITORY}/.github/workflows/release.yml@refs/heads/main"
        ),
    )


class ActionsAutomationExistingTagAuthorityTest(unittest.TestCase):
    def setUp(self) -> None:
        self.provider = TrackingProvider()
        self.inputs = ReleaseInputs("existing-tag", "1.2.3", COMMIT_A)

    def test_exact_actions_bot_is_admitted_after_default_branch_checks(self) -> None:
        authority = resolve_release_authority(
            self.inputs,
            dispatch_event(),
            self.provider,
        )

        self.assertEqual("existing-tag", authority.release_mode)
        self.assertEqual(COMMIT_A, authority.release_source_sha)
        self.assertEqual([], self.provider.permission_calls)

    def test_human_existing_tag_callers_keep_collaborator_permission_gate(self) -> None:
        self.provider.permission = "read"

        with self.assertRaisesRegex(ReleaseTagError, "trusted_actor_write_required"):
            resolve_release_authority(
                self.inputs,
                dispatch_event(actor="release-owner"),
                self.provider,
            )

        self.assertEqual(
            [(REPOSITORY, "release-owner")],
            self.provider.permission_calls,
        )

    def test_bot_like_actor_does_not_receive_automation_exception(self) -> None:
        self.provider.permission = "read"

        with self.assertRaisesRegex(ReleaseTagError, "trusted_actor_write_required"):
            resolve_release_authority(
                self.inputs,
                dispatch_event(actor="github-actions[bot]-extra"),
                self.provider,
            )

        self.assertEqual(
            [(REPOSITORY, "github-actions[bot]-extra")],
            self.provider.permission_calls,
        )

    def test_actions_bot_still_fails_closed_for_untrusted_dispatch_context(self) -> None:
        cases = (
            (
                dispatch_event(event_name="pull_request"),
                "existing_tag_event_forbidden",
            ),
            (dispatch_event(fork=True), "fork_repository_forbidden"),
            (
                dispatch_event(ref="refs/heads/feature", ref_name="feature"),
                "trusted_caller_branch_mismatch",
            ),
            (
                dispatch_event(
                    workflow_ref=(
                        f"{REPOSITORY}/.github/workflows/release.yml"
                        "@refs/heads/feature"
                    )
                ),
                "caller_workflow_not_default_branch",
            ),
        )
        for event, expression in cases:
            with self.subTest(expression=expression):
                with self.assertRaisesRegex(ReleaseTagError, expression):
                    resolve_release_authority(self.inputs, event, self.provider)

        self.provider.default_sha = COMMIT_B
        with self.assertRaisesRegex(ReleaseTagError, "stale_trusted_caller_source"):
            resolve_release_authority(
                self.inputs,
                dispatch_event(),
                self.provider,
            )

        self.assertEqual([], self.provider.permission_calls)

    def test_actions_bot_revalidation_preserves_exact_tag_immutability(self) -> None:
        authority = resolve_release_authority(
            self.inputs,
            dispatch_event(),
            self.provider,
        )

        # Revalidation intentionally allows main to move after initial admission,
        # while still requiring the same default-branch workflow and exact tag tuple.
        self.provider.default_sha = COMMIT_B
        self.assertEqual(
            authority,
            revalidate_release_authority(
                authority,
                dispatch_event(),
                self.provider,
            ),
        )

        self.provider.tag_refs["1.2.3"] = object_payload("commit", COMMIT_B)
        with self.assertRaisesRegex(ReleaseTagError, "release_tag_moved"):
            revalidate_release_authority(
                authority,
                dispatch_event(),
                self.provider,
            )

    def test_non_default_ref_type_remains_forbidden_for_actions_bot(self) -> None:
        with self.assertRaisesRegex(
            ReleaseTagError,
            "trusted_caller_ref_type_mismatch",
        ):
            resolve_release_authority(
                self.inputs,
                dispatch_event(
                    ref="refs/tags/1.2.3",
                    ref_name="1.2.3",
                    ref_type="tag",
                ),
                self.provider,
            )


if __name__ == "__main__":
    unittest.main()
