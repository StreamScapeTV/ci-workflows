from __future__ import annotations

import json
from pathlib import Path
import unittest

from ci_workflows.release_contract import resolve_release_plan
from ci_workflows.release_github import (
    desired_release,
    ensure_github_release,
    verify_existing_release,
)
from ci_workflows.release_types import ReleaseError


ROOT = Path(__file__).resolve().parents[1]
SOURCE_SHA = "1" * 40
MANIFEST = '{"a":1,"b":2}'
MANIFEST_SHA = "43258cff783fe7036d8a43033f830adfc60ec037382473548ac742b888292777"


class FakeReleaseAPI:
    def __init__(self, *, existing=None, create_result=None, create_error=None, raced=None):
        self.existing = existing
        self.create_result = create_result
        self.create_error = create_error
        self.raced = raced
        self.get_calls = 0
        self.created_payload = None

    def get_by_tag(self, tag: str):
        self.get_calls += 1
        if self.get_calls == 1:
            return self.existing
        return self.raced

    def create(self, payload):
        self.created_payload = dict(payload)
        if self.create_error is not None:
            raise self.create_error
        return self.create_result


class GitHubReleaseReplayTest(unittest.TestCase):
    def setUp(self) -> None:
        plan = resolve_release_plan(ROOT, "iptv-backend", "StreamScapeTV/iptv-backend")
        self.desired = desired_release(
            plan=plan,
            release_version="1.4.2",
            source_sha=SOURCE_SHA,
            manifest_json=MANIFEST,
            manifest_sha256=MANIFEST_SHA,
        )
        self.exact = {
            "tag_name": self.desired.tag_name,
            "name": self.desired.name,
            "body": self.desired.body,
            "draft": False,
            "prerelease": False,
            "html_url": "https://github.com/StreamScapeTV/iptv-backend/releases/tag/1.4.2",
        }

    def test_matching_existing_release_is_idempotent(self) -> None:
        api = FakeReleaseAPI(existing=self.exact)
        url, state = ensure_github_release(api, self.desired)
        self.assertEqual(self.exact["html_url"], url)
        self.assertEqual("existing-matched", state)
        self.assertIsNone(api.created_payload)
        self.assertEqual("StreamScapeTV/iptv-backend", self.desired.repository)

    def test_existing_release_with_different_manifest_fails_closed(self) -> None:
        conflicting = dict(self.exact)
        conflicting["body"] = conflicting["body"].replace(MANIFEST_SHA, "0" * 64)
        with self.assertRaisesRegex(ReleaseError, r"^github_release_conflict$"):
            ensure_github_release(FakeReleaseAPI(existing=conflicting), self.desired)

    def test_create_path_uses_exact_tag_source_and_disables_generated_notes(self) -> None:
        api = FakeReleaseAPI(existing=None, create_result=self.exact)
        url, state = ensure_github_release(api, self.desired)
        self.assertEqual("created", state)
        self.assertEqual(self.exact["html_url"], url)
        self.assertEqual("1.4.2", api.created_payload["tag_name"])
        self.assertEqual(SOURCE_SHA, api.created_payload["target_commitish"])
        self.assertFalse(api.created_payload["generate_release_notes"])
        self.assertFalse(api.created_payload["draft"])
        self.assertFalse(api.created_payload["prerelease"])

    def test_create_race_rechecks_and_accepts_only_exact_match(self) -> None:
        api = FakeReleaseAPI(
            existing=None,
            create_error=ReleaseError("github_release_create_conflict"),
            raced=self.exact,
        )
        url, state = ensure_github_release(api, self.desired)
        self.assertEqual(self.exact["html_url"], url)
        self.assertEqual("existing-matched-after-race", state)
        self.assertEqual(2, api.get_calls)

    def test_create_race_with_different_release_still_fails(self) -> None:
        raced = dict(self.exact)
        raced["name"] = "someone else's release"
        api = FakeReleaseAPI(
            existing=None,
            create_error=ReleaseError("github_release_create_conflict"),
            raced=raced,
        )
        with self.assertRaisesRegex(ReleaseError, r"^github_release_conflict$"):
            ensure_github_release(api, self.desired)

    def test_release_body_contains_only_canonical_manifest(self) -> None:
        self.assertIn(MANIFEST_SHA, self.desired.body)
        fenced = self.desired.body.split("```json\n", 1)[1].split("\n```", 1)[0]
        self.assertEqual({"a": 1, "b": 2}, json.loads(fenced))
        self.assertEqual(MANIFEST, fenced)

    def test_verify_existing_rejects_external_release_url(self) -> None:
        external = dict(self.exact)
        external["html_url"] = "https://example.com/release/1.4.2"
        with self.assertRaisesRegex(ReleaseError, r"^github_release_response_rejected$"):
            verify_existing_release(external, self.desired)

    def test_verify_existing_rejects_other_streamscape_repository_url(self) -> None:
        redirected = dict(self.exact)
        redirected["html_url"] = (
            "https://github.com/StreamScapeTV/agent-state/releases/tag/1.4.2"
        )
        with self.assertRaisesRegex(ReleaseError, r"^github_release_response_rejected$"):
            verify_existing_release(redirected, self.desired)


if __name__ == "__main__":
    unittest.main()
