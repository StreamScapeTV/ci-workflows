from __future__ import annotations

from typing import Any, Mapping
import unittest

from ci_workflows.release_tag_authority import (
    ReleaseEvent,
    ReleaseInputs,
    revalidate_release_authority,
    resolve_release_authority,
)


REPOSITORY = "StreamScapeTV/iptv-backend"
HISTORICAL_SHA = "1" * 40
CURRENT_DEFAULT_SHA = "2" * 40
ANNOTATED_TAG_SHA = "3" * 40


def object_payload(object_type: str, sha: str) -> Mapping[str, Any]:
    return {"object": {"type": object_type, "sha": sha}}


class HistoricalProvider:
    def __init__(self, *, annotated: bool) -> None:
        self.annotated = annotated
        self.moved = False

    def repository_metadata(self, repository: str) -> Mapping[str, Any]:
        return {
            "full_name": repository,
            "fork": False,
            "default_branch": "main",
        }

    def collaborator_permission(self, repository: str, actor: str) -> Mapping[str, Any]:
        return {"permission": "write"}

    def branch_sha(self, repository: str, branch: str) -> str:
        return CURRENT_DEFAULT_SHA

    def tag_ref(self, repository: str, tag_name: str) -> Mapping[str, Any]:
        if self.moved:
            return object_payload("commit", CURRENT_DEFAULT_SHA)
        if self.annotated:
            return object_payload("tag", ANNOTATED_TAG_SHA)
        return object_payload("commit", HISTORICAL_SHA)

    def tag_object(self, repository: str, sha: str) -> Mapping[str, Any]:
        if sha != ANNOTATED_TAG_SHA:
            raise AssertionError("unexpected annotated tag object")
        return object_payload("commit", HISTORICAL_SHA)

    def commit(self, repository: str, sha: str) -> Mapping[str, Any]:
        if sha not in {HISTORICAL_SHA, CURRENT_DEFAULT_SHA}:
            raise AssertionError("unexpected commit")
        return {"sha": sha}


def trusted_replay_event() -> ReleaseEvent:
    return ReleaseEvent(
        event_name="workflow_dispatch",
        repository=REPOSITORY,
        event_repository=REPOSITORY,
        event_repository_fork=False,
        ref="refs/heads/main",
        ref_type="branch",
        ref_name="main",
        sha=CURRENT_DEFAULT_SHA,
        actor="release-owner",
        workflow_ref=(
            f"{REPOSITORY}/.github/workflows/release.yml@refs/heads/main"
        ),
    )


class HistoricalReleaseAuthorityIntegrationTest(unittest.TestCase):
    def resolve(self, provider: HistoricalProvider):
        return resolve_release_authority(
            ReleaseInputs(
                release_mode="existing-tag",
                release_version="1.4.2",
                release_source_sha=HISTORICAL_SHA,
            ),
            trusted_replay_event(),
            provider,
        )

    def test_historical_lightweight_tag_uses_tag_commit_not_default_branch_tip(self) -> None:
        provider = HistoricalProvider(annotated=False)
        authority = self.resolve(provider)
        self.assertEqual(HISTORICAL_SHA, authority.release_source_sha)
        self.assertEqual(HISTORICAL_SHA, authority.tag_object_sha)
        self.assertEqual(HISTORICAL_SHA, authority.tag_commit_sha)
        self.assertNotEqual(CURRENT_DEFAULT_SHA, authority.release_source_sha)
        self.assertEqual(
            authority,
            revalidate_release_authority(
                authority,
                trusted_replay_event(),
                provider,
            ),
        )

    def test_historical_annotated_tag_preserves_object_and_peeled_commit(self) -> None:
        provider = HistoricalProvider(annotated=True)
        authority = self.resolve(provider)
        self.assertEqual(ANNOTATED_TAG_SHA, authority.tag_object_sha)
        self.assertEqual(HISTORICAL_SHA, authority.tag_commit_sha)
        self.assertEqual(HISTORICAL_SHA, authority.release_source_sha)
        self.assertNotEqual(CURRENT_DEFAULT_SHA, authority.release_source_sha)

    def test_historical_replay_fails_if_tag_moves_after_admission(self) -> None:
        provider = HistoricalProvider(annotated=True)
        authority = self.resolve(provider)
        provider.moved = True
        with self.assertRaisesRegex(Exception, r"release_tag_moved"):
            revalidate_release_authority(
                authority,
                trusted_replay_event(),
                provider,
            )


if __name__ == "__main__":
    unittest.main()
