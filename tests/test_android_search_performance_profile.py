from pathlib import Path
import unittest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/android.yml"
DISPATCH = ROOT / ".github/workflows/central-ci-dispatch.yml"


class AndroidRetainedReleaseContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.workflow = yaml.safe_load(WORKFLOW.read_text())
        cls.dispatch = yaml.safe_load(DISPATCH.read_text())
        cls.source = WORKFLOW.read_text()
        cls.steps = cls.workflow["jobs"]["ci"]["steps"]
        cls.by_name = {s.get("name"): s for s in cls.steps if s.get("name")}

    def test_only_specialists_and_play_release_remain(self) -> None:
        jobs = self.workflow["jobs"]
        self.assertEqual(
            set(jobs),
            {"screenshot_plan", "screenshot_cache", "screenshot", "screenshot_finish", "physical_performance", "ci"},
        )
        self.assertEqual(jobs["ci"]["if"], "${{ inputs.test_profile == 'play' }}")
        for retired in (
            "targeted-tests", "targeted-unit", "search-performance",
            "test_filter", "test_platform", "room_schema", "native-component",
        ):
            self.assertNotIn(retired, self.source)
        self.assertNotIn("inputs.test_profile == 'emulator'", self.source)
        self.assertNotIn("  emulator)", self.source)

    def test_play_release_uses_one_fixed_product_wrapper_and_internal_draft(self) -> None:
        command = self.by_name["Run fixed Android profile"]
        run = command["run"]
        self.assertIn("run-android-play-release.sh", run)
        self.assertIn("CI_ANDROID_PLAY_TRACK", run)
        self.assertIn("CI_ANDROID_PLAY_RELEASE_STATUS", run)
        self.assertIn("= internal", run)
        self.assertIn("= draft", run)
        self.assertNotIn("./gradlew", run)
        self.assertNotIn("case \"${TEST_PROFILE}\"", run)

    def test_play_credentials_are_profile_scoped_and_cleaned(self) -> None:
        prepare = self.by_name["Prepare fixed Google Play draft release context"]
        self.assertEqual(prepare["if"], "${{ inputs.test_profile == 'play' }}")
        cleanup = self.by_name["Clean Google Play credential and release state"]
        self.assertIn("always()", cleanup["if"])
        self.assertIn("central-android-play", cleanup["run"])
        scrub = self.by_name["Scrub configured CI secrets from private log"]
        for name in (
            "CI_SECRET_GOOGLE_PLAY_SERVICE_ACCOUNT_JSON",
            "CI_SECRET_ANDROID_PLAY_UPLOAD_KEYSTORE_BASE64",
            "CI_SECRET_ANDROID_PLAY_UPLOAD_KEYSTORE_PASSWORD",
            "CI_SECRET_ANDROID_PLAY_UPLOAD_KEY_ALIAS",
            "CI_SECRET_ANDROID_PLAY_UPLOAD_KEY_PASSWORD",
        ):
            self.assertIn(name, scrub["env"])

    def test_legacy_android_cache_is_read_only_for_remaining_play_lane(self) -> None:
        self.assertIn("actions/cache/restore@v4", self.source)
        self.assertNotIn("actions/cache/save@v4", self.source)
        self.assertIn("Resolve IPTV Android default-branch cache scope", self.by_name)
        self.assertIn("Resolve IPTV Android non-default cache reader scope", self.by_name)

    def test_dispatch_validation_android_is_specialist_only(self) -> None:
        request = {s.get("name"): s for s in self.dispatch["jobs"]["request"]["steps"] if s.get("name")}
        keep = request["Validate retained legacy validation profile"]["run"]
        self.assertIn("validation.android/screenshot-review", keep)
        self.assertIn("validation.android/physical-performance", keep)
        self.assertIn("retired Android validation profile", keep)
        validation_job = self.dispatch["jobs"]["android"]
        self.assertEqual(set(validation_job["with"]), {"repository", "ref", "test_profile", "test_selectors", "ci_run_id"})
        release_job = self.dispatch["jobs"]["android_release"]
        self.assertEqual(release_job["with"]["test_profile"], "play")
        self.assertFalse(release_job["concurrency"]["cancel-in-progress"])


if __name__ == "__main__":
    unittest.main()
