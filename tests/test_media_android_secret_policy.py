"""Focused regressions for reviewed Media redaction sentinels in Android policy."""
from __future__ import annotations

import unittest
from pathlib import Path
from types import SimpleNamespace

from ci_workflows import android_policy

ROOT = Path(__file__).resolve().parents[1]
MEDIA_REPOSITORY = "StreamScapeTV/streamscape-media"
GUIDED_ACCEPTANCE_PATH = (
    "apple/Tests/StreamscapePlaybackLabSupportTests/"
    "PlaybackLabGuidedAcceptanceRunFlowTests.swift"
)
GUIDED_ACCEPTANCE_BLOB = "2a9149b864bf59079099035c15af54234f54b452"
LIFECYCLE_EVIDENCE_PATH = (
    "apple/Tests/StreamscapePlaybackLabSupportTests/"
    "PlaybackLabLifecycleEvidenceTests.swift"
)
LIFECYCLE_EVIDENCE_BLOB = "5df889bbf613ee7f4dabd07ca931aa81fb4f71a3"
REVIEWED_MEDIA_SENTINELS = {
    GUIDED_ACCEPTANCE_PATH: GUIDED_ACCEPTANCE_BLOB,
    LIFECYCLE_EVIDENCE_PATH: LIFECYCLE_EVIDENCE_BLOB,
}


class MediaAndroidSecretPolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.contract = android_policy.load_android_source_policy(ROOT)

    def test_guided_acceptance_redaction_sentinel_is_exact_and_digest_bound(self) -> None:
        entry = next(
            item
            for item in self.contract["tracked_secret_exceptions"]
            if item["id"]
            == "streamscape_media_guided_acceptance_redaction_sentinels_v1"
        )
        self.assertEqual(entry["repository"], MEDIA_REPOSITORY)
        self.assertEqual(
            entry["validation_profiles"],
            ["compile", "unit-full", "consumer-script"],
        )
        self.assertEqual(entry["rule_id"], "tracked_secret_detected")
        self.assertEqual(entry["digest_algorithm"], "git-blob-sha1")
        self.assertEqual(
            entry["paths"],
            [{
                "path": GUIDED_ACCEPTANCE_PATH,
                "git_blob_sha1": GUIDED_ACCEPTANCE_BLOB,
            }],
        )

    def test_lifecycle_evidence_redaction_sentinel_is_exact_and_digest_bound(self) -> None:
        entry = next(
            item
            for item in self.contract["tracked_secret_exceptions"]
            if item["id"]
            == "streamscape_media_lifecycle_evidence_redaction_sentinels_v1"
        )
        self.assertEqual(entry["repository"], MEDIA_REPOSITORY)
        self.assertEqual(
            entry["validation_profiles"],
            ["compile", "unit-full", "consumer-script"],
        )
        self.assertEqual(entry["rule_id"], "tracked_secret_detected")
        self.assertEqual(entry["digest_algorithm"], "git-blob-sha1")
        self.assertEqual(
            entry["paths"],
            [{
                "path": LIFECYCLE_EVIDENCE_PATH,
                "git_blob_sha1": LIFECYCLE_EVIDENCE_BLOB,
            }],
        )

    def test_exceptions_activate_only_for_reviewed_media_profiles(self) -> None:
        for profile in ("compile", "unit-full", "consumer-script"):
            active = android_policy._active_secret_exceptions(
                self.contract,
                SimpleNamespace(
                    repository=MEDIA_REPOSITORY,
                    validation_profile=profile,
                ),
            )
            for path, digest in REVIEWED_MEDIA_SENTINELS.items():
                self.assertEqual(active[path], digest)

        unrelated_profile = android_policy._active_secret_exceptions(
            self.contract,
            SimpleNamespace(
                repository=MEDIA_REPOSITORY,
                validation_profile="lint",
            ),
        )
        for path in REVIEWED_MEDIA_SENTINELS:
            self.assertNotIn(path, unrelated_profile)

        unrelated_repository = android_policy._active_secret_exceptions(
            self.contract,
            SimpleNamespace(
                repository="StreamScapeTV/iptv-android",
                validation_profile="consumer-script",
            ),
        )
        for path in REVIEWED_MEDIA_SENTINELS:
            self.assertNotIn(path, unrelated_repository)


if __name__ == "__main__":
    unittest.main()
