"""Regression coverage for reviewed Streamscape Media redaction sentinels."""
from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from ci_workflows import android_policy
from ci_workflows import policy as foundation_policy
from ci_workflows.android_types import AndroidValidationRequest
from ci_workflows.foundation_types import load_contract

ROOT = Path(__file__).resolve().parents[1]
MEDIA_REPOSITORY = "StreamScapeTV/streamscape-media"
MEDIA_PATH = (
    "apple/Tests/StreamscapePlaybackLabSupportTests/"
    "PlaybackLabBootstrapEvidenceTests.swift"
)
MEDIA_BLOB_SHA1 = "1770311f5b3998b5dbf3f8ee191acd419aa52a56"
GUIDED_ACCEPTANCE_PATH = (
    "apple/Tests/StreamscapePlaybackLabSupportTests/"
    "PlaybackLabGuidedAcceptanceRunFlowTests.swift"
)
GUIDED_ACCEPTANCE_BLOB_SHA1 = "2a9149b864bf59079099035c15af54234f54b452"
LIFECYCLE_EVIDENCE_PATH = (
    "apple/Tests/StreamscapePlaybackLabSupportTests/"
    "PlaybackLabLifecycleEvidenceTests.swift"
)
LIFECYCLE_EVIDENCE_BLOB_SHA1 = "5df889bbf613ee7f4dabd07ca931aa81fb4f71a3"
REVIEWED_MEDIA_SENTINELS = {
    MEDIA_PATH: MEDIA_BLOB_SHA1,
    GUIDED_ACCEPTANCE_PATH: GUIDED_ACCEPTANCE_BLOB_SHA1,
    LIFECYCLE_EVIDENCE_PATH: LIFECYCLE_EVIDENCE_BLOB_SHA1,
}
MEDIA_TASKS = {
    "compile": "media-compile",
    "unit-full": "media-unit-full",
    "consumer-script": "media-android-build-script",
}


class AndroidMediaSourcePolicyTests(unittest.TestCase):
    def request(self, profile: str) -> AndroidValidationRequest:
        return AndroidValidationRequest(
            repository=MEDIA_REPOSITORY,
            admitted_sha="a" * 40,
            validation_profile=profile,
            task_profile=MEDIA_TASKS.get(profile, "profile-not-allowed"),
            working_directory=".",
            gradle_wrapper_path="android/gradlew",
            targeted_test_selector=None,
            consumer_script_profile=None,
            private_dependency_contract_id=None,
            private_dependency_sha=None,
            artifact_exception_id=None,
            device_family=None,
            device_request_id=None,
            source_trust="trusted-pr",
        )

    def test_current_media_sentinel_blob_is_exactly_profile_bounded(self) -> None:
        contract = android_policy.load_android_source_policy(ROOT)

        for profile in MEDIA_TASKS:
            with self.subTest(profile=profile):
                active = android_policy._active_secret_exceptions(
                    contract,
                    self.request(profile),
                )
                self.assertEqual(active, REVIEWED_MEDIA_SENTINELS)

        for profile in sorted(android_policy._ANDROID_PROFILES - set(MEDIA_TASKS)):
            with self.subTest(disallowed_profile=profile):
                active = android_policy._active_secret_exceptions(
                    contract,
                    self.request(profile),
                )
                for path in REVIEWED_MEDIA_SENTINELS:
                    self.assertNotIn(path, active)

    def test_changed_media_sentinel_blob_with_real_token_shape_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repository = Path(temporary) / "repository"
            repository.mkdir()
            subprocess.run(
                ["git", "init", "--quiet", str(repository)],
                check=True,
            )
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(repository),
                    "config",
                    "user.email",
                    "android-media-policy@example.invalid",
                ],
                check=True,
            )
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(repository),
                    "config",
                    "user.name",
                    "Android Media Policy",
                ],
                check=True,
            )
            target = repository / MEDIA_PATH
            target.parent.mkdir(parents=True)
            credential_prefix = "g" + "hp" + "_"
            header = "Author" + "ization: " + "Bear" + "er "
            real_shape = credential_prefix + "B" * 40
            target.write_text(
                f'let credential = "{header}{real_shape}"\n',
                encoding="utf-8",
            )
            subprocess.run(
                ["git", "-C", str(repository), "add", MEDIA_PATH],
                check=True,
            )
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(repository),
                    "commit",
                    "--quiet",
                    "-m",
                    "changed sentinel",
                ],
                check=True,
            )

            repository_policy = load_contract(
                ROOT,
                foundation_policy.REPOSITORY_POLICY,
            )
            source_policy = android_policy.load_android_source_policy(ROOT)
            with self.assertRaises(android_policy.AndroidPolicyFinding) as caught:
                android_policy._scan_tracked_source(
                    repository,
                    request=self.request("compile"),
                    repository_policy=repository_policy,
                    source_policy=source_policy,
                )

            self.assertEqual(caught.exception.subject, MEDIA_PATH)
            self.assertNotIn(real_shape, repr(caught.exception))


if __name__ == "__main__":
    unittest.main()
