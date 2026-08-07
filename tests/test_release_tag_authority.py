from __future__ import annotations

import unittest
from typing import Any, Mapping

from ci_workflows.release_tag_authority import (
    MAX_TAG_DEREFERENCE_DEPTH,
    ReleaseAuthority,
    ReleaseEvent,
    ReleaseInputs,
    ReleaseTagError,
    revalidate_release_authority,
    resolve_release_authority,
    resolve_tag,
)

COMMIT_A = "a" * 40
COMMIT_B = "b" * 40
TAG_A = "c" * 40
TAG_B = "d" * 40
TAG_C = "e" * 40
CALLER_SHA = "f" * 40
REPOSITORY = "StreamScapeTV/iptv-backend"


class FakeProvider:
    def __init__(self) -> None:
        self.default_branch = "main"
        self.default_sha = CALLER_SHA
        self.permission = "write"
        self.repository_fork = False
        self.tag_refs: dict[str, Mapping[str, Any]] = {}
        self.tag_objects: dict[str, Mapping[str, Any]] = {}
        self.commits = {COMMIT_A, COMMIT_B, CALLER_SHA}

    def repository_metadata(self, repository: str) -> Mapping[str, Any]:
        return {
            "full_name": repository,
            "fork": self.repository_fork,
            "default_branch": self.default_branch,
        }

    def collaborator_permission(
        self, repository: str, actor: str
    ) -> Mapping[str, Any]:
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
        try:
            return self.tag_objects[sha]
        except KeyError:
            raise ReleaseTagError("annotated_tag_object_missing") from None

    def commit(self, repository: str, sha: str) -> Mapping[str, Any]:
        if sha not in self.commits:
            raise ReleaseTagError("tag_commit_missing")
        return {"sha": sha}


def object_payload(object_type: str, sha: str) -> Mapping[str, Any]:
    return {"object": {"type": object_type, "sha": sha}}


def push_event(*, version: str = "1.0.4", sha: str = COMMIT_A) -> ReleaseEvent:
    return ReleaseEvent(
        event_name="push",
        repository=REPOSITORY,
        event_repository=REPOSITORY,
        event_repository_fork=False,
        ref=f"refs/tags/{version}",
        ref_type="tag",
        ref_name=version,
        sha=sha,
        actor="release-owner",
        workflow_ref=(
            f"{REPOSITORY}/.github/workflows/release.yml"
            f"@refs/tags/{version}"
        ),
    )


def dispatch_event(
    *,
    ref: str = "refs/heads/main",
    ref_name: str = "main",
    ref_type: str = "branch",
    workflow_ref: str | None = None,
    fork: bool = False,
    event_name: str = "workflow_dispatch",
) -> ReleaseEvent:
    return ReleaseEvent(
        event_name=event_name,
        repository=REPOSITORY,
        event_repository=REPOSITORY,
        event_repository_fork=fork,
        ref=ref,
        ref_type=ref_type,
        ref_name=ref_name,
        sha=CALLER_SHA,
        actor="release-owner",
        workflow_ref=(
            workflow_ref
            or f"{REPOSITORY}/.github/workflows/release.yml@refs/heads/main"
        ),
    )


class ReleaseTagAuthorityTest(unittest.TestCase):
    def setUp(self) -> None:
        self.provider = FakeProvider()

    def lightweight(self, version: str = "1.0.4", sha: str = COMMIT_A) -> None:
        self.provider.tag_refs[version] = object_payload("commit", sha)

    def annotated(self, version: str = "1.0.4") -> None:
        self.provider.tag_refs[version] = object_payload("tag", TAG_A)
        self.provider.tag_objects[TAG_A] = object_payload("commit", COMMIT_A)

    def test_legacy_tag_push_defaults_and_resolves_lightweight_tag(self) -> None:
        self.lightweight()
        result = resolve_release_authority(
            ReleaseInputs(),
            push_event(),
            self.provider,
        )
        self.assertEqual("tag-push", result.release_mode)
        self.assertEqual("1.0.4", result.release_version)
        self.assertEqual(COMMIT_A, result.release_source_sha)
        self.assertEqual(COMMIT_A, result.tag_object_sha)

    def test_tag_push_accepts_annotated_event_object_sha(self) -> None:
        self.annotated()
        result = resolve_release_authority(
            ReleaseInputs(),
            push_event(sha=TAG_A),
            self.provider,
        )
        self.assertEqual(TAG_A, result.tag_object_sha)
        self.assertEqual(COMMIT_A, result.tag_commit_sha)

    def test_explicit_lightweight_existing_tag(self) -> None:
        self.lightweight()
        result = resolve_release_authority(
            ReleaseInputs(
                release_mode="existing-tag",
                release_version="1.0.4",
                release_source_sha=COMMIT_A,
            ),
            dispatch_event(),
            self.provider,
        )
        self.assertEqual("existing-tag", result.release_mode)
        self.assertEqual(COMMIT_A, result.release_source_sha)

    def test_explicit_annotated_and_nested_annotated_tags(self) -> None:
        self.annotated()
        result = resolve_release_authority(
            ReleaseInputs("existing-tag", "1.0.4", COMMIT_A),
            dispatch_event(),
            self.provider,
        )
        self.assertEqual(
            1,
            resolve_tag(self.provider, REPOSITORY, "1.0.4").dereference_depth,
        )
        self.assertEqual(COMMIT_A, result.release_source_sha)

        self.provider.tag_refs["1.0.5"] = object_payload("tag", TAG_A)
        self.provider.tag_objects[TAG_A] = object_payload("tag", TAG_B)
        self.provider.tag_objects[TAG_B] = object_payload("tag", TAG_C)
        self.provider.tag_objects[TAG_C] = object_payload("commit", COMMIT_B)
        nested = resolve_release_authority(
            ReleaseInputs("existing-tag", "1.0.5", COMMIT_B),
            dispatch_event(),
            self.provider,
        )
        self.assertEqual(
            3,
            resolve_tag(self.provider, REPOSITORY, "1.0.5").dereference_depth,
        )
        self.assertEqual(COMMIT_B, nested.release_source_sha)

    def test_missing_deleted_and_moved_tags_fail_closed(self) -> None:
        with self.assertRaisesRegex(ReleaseTagError, "release_tag_missing"):
            resolve_release_authority(
                ReleaseInputs("existing-tag", "1.0.4", COMMIT_A),
                dispatch_event(),
                self.provider,
            )

        self.lightweight()
        authority = resolve_release_authority(
            ReleaseInputs("existing-tag", "1.0.4", COMMIT_A),
            dispatch_event(),
            self.provider,
        )
        del self.provider.tag_refs["1.0.4"]
        with self.assertRaisesRegex(ReleaseTagError, "release_tag_missing"):
            revalidate_release_authority(
                authority,
                dispatch_event(),
                self.provider,
            )

        self.provider.tag_refs["1.0.4"] = object_payload("commit", COMMIT_B)
        with self.assertRaisesRegex(ReleaseTagError, "release_tag_moved"):
            revalidate_release_authority(
                authority,
                dispatch_event(),
                self.provider,
            )

    def test_tag_source_mismatch_is_rejected(self) -> None:
        self.lightweight(sha=COMMIT_B)
        with self.assertRaisesRegex(ReleaseTagError, "tag_source_mismatch"):
            resolve_release_authority(
                ReleaseInputs("existing-tag", "1.0.4", COMMIT_A),
                dispatch_event(),
                self.provider,
            )

    def test_incomplete_and_mixed_authority_are_rejected(self) -> None:
        for inputs, expression in (
            (
                ReleaseInputs("existing-tag", "1.0.4", None),
                "partial_explicit_release_tuple",
            ),
            (
                ReleaseInputs("existing-tag", None, COMMIT_A),
                "partial_explicit_release_tuple",
            ),
            (
                ReleaseInputs("tag-push", "1.0.4", COMMIT_A),
                "mixed_release_authority",
            ),
        ):
            with self.subTest(expression=expression):
                with self.assertRaisesRegex(ReleaseTagError, expression):
                    resolve_release_authority(
                        inputs,
                        dispatch_event()
                        if inputs.release_mode == "existing-tag"
                        else push_event(),
                        self.provider,
                    )

    def test_unknown_mode_malformed_version_and_sha_are_rejected(self) -> None:
        cases = (
            (ReleaseInputs("manual", "1.0.4", COMMIT_A), "unknown_release_mode"),
            (ReleaseInputs("existing-tag", "v1.0.4", COMMIT_A), "invalid_release_version"),
            (ReleaseInputs("existing-tag", "01.0.4", COMMIT_A), "invalid_release_version"),
            (ReleaseInputs("existing-tag", "1.0.4+build", COMMIT_A), "invalid_release_version"),
            (ReleaseInputs("existing-tag", "refs/tags/1.0.4", COMMIT_A), "invalid_release_version"),
            (ReleaseInputs("existing-tag", "1.0.4", COMMIT_A.upper()), "invalid_release_source_sha"),
            (ReleaseInputs("existing-tag", "1.0.4", "a" * 39), "invalid_release_source_sha"),
        )
        for inputs, expression in cases:
            with self.subTest(expression=expression, inputs=inputs):
                with self.assertRaisesRegex(ReleaseTagError, expression):
                    resolve_release_authority(
                        inputs,
                        dispatch_event(),
                        self.provider,
                    )

    def test_unsupported_object_cycle_and_depth_are_rejected(self) -> None:
        self.provider.tag_refs["1.0.4"] = object_payload("tree", TAG_A)
        with self.assertRaisesRegex(ReleaseTagError, "unsupported_tag_object_type"):
            resolve_tag(self.provider, REPOSITORY, "1.0.4")

        self.provider.tag_refs["1.0.4"] = object_payload("tag", TAG_A)
        self.provider.tag_objects[TAG_A] = object_payload("tag", TAG_B)
        self.provider.tag_objects[TAG_B] = object_payload("tag", TAG_A)
        with self.assertRaisesRegex(ReleaseTagError, "tag_object_cycle"):
            resolve_tag(self.provider, REPOSITORY, "1.0.4")

        tag_shas = [
            f"{index:040x}"
            for index in range(1, MAX_TAG_DEREFERENCE_DEPTH + 2)
        ]
        self.provider.tag_refs["1.0.4"] = object_payload("tag", tag_shas[0])
        for current, following in zip(tag_shas, tag_shas[1:]):
            self.provider.tag_objects[current] = object_payload("tag", following)
        self.provider.tag_objects[tag_shas[-1]] = object_payload("commit", COMMIT_A)
        with self.assertRaisesRegex(ReleaseTagError, "tag_dereference_too_deep"):
            resolve_tag(self.provider, REPOSITORY, "1.0.4")

    def test_untrusted_event_fork_branch_and_workflow_ref_are_rejected(self) -> None:
        self.lightweight()
        inputs = ReleaseInputs("existing-tag", "1.0.4", COMMIT_A)
        for event, expression in (
            (dispatch_event(event_name="pull_request"), "existing_tag_event_forbidden"),
            (dispatch_event(event_name="issue_comment"), "existing_tag_event_forbidden"),
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
        ):
            with self.subTest(expression=expression):
                with self.assertRaisesRegex(ReleaseTagError, expression):
                    resolve_release_authority(inputs, event, self.provider)

    def test_non_writer_and_stale_default_branch_are_rejected(self) -> None:
        self.lightweight()
        inputs = ReleaseInputs("existing-tag", "1.0.4", COMMIT_A)
        self.provider.permission = "read"
        with self.assertRaisesRegex(ReleaseTagError, "trusted_actor_write_required"):
            resolve_release_authority(inputs, dispatch_event(), self.provider)
        self.provider.permission = "write"
        self.provider.default_sha = COMMIT_B
        with self.assertRaisesRegex(ReleaseTagError, "stale_trusted_caller_source"):
            resolve_release_authority(inputs, dispatch_event(), self.provider)

    def test_revalidation_preserves_exact_unchanged_tuple(self) -> None:
        self.annotated()
        authority = resolve_release_authority(
            ReleaseInputs("existing-tag", "1.0.4", COMMIT_A),
            dispatch_event(),
            self.provider,
        )
        self.assertEqual(
            authority,
            revalidate_release_authority(
                authority,
                dispatch_event(),
                self.provider,
            ),
        )

    def test_revalidation_rejects_changed_commit_even_with_expected_object(self) -> None:
        authority = ReleaseAuthority(
            release_mode="existing-tag",
            release_version="1.0.4",
            release_source_sha=COMMIT_A,
            tag_object_sha=TAG_A,
            tag_commit_sha=COMMIT_A,
        )
        self.provider.tag_refs["1.0.4"] = object_payload("tag", TAG_A)
        self.provider.tag_objects[TAG_A] = object_payload("commit", COMMIT_B)
        with self.assertRaisesRegex(ReleaseTagError, "release_tag_source_changed"):
            revalidate_release_authority(
                authority,
                dispatch_event(),
                self.provider,
            )


if __name__ == "__main__":
    unittest.main()
