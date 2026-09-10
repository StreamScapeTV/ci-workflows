from pathlib import Path
import subprocess
import tempfile
import unittest

import yaml

ROOT = Path(__file__).resolve().parents[1]


class AndroidScreenshotReviewTests(unittest.TestCase):
    def setUp(self) -> None:
        self.workflow = yaml.safe_load((ROOT / ".github/workflows/android.yml").read_text())
        self.dispatch = yaml.safe_load((ROOT / ".github/workflows/central-ci-dispatch.yml").read_text())
        self.drive = yaml.safe_load((ROOT / "actions/google-drive/action.yml").read_text())

    def test_dispatch_accepts_only_fixed_android_screenshot_request(self) -> None:
        step = next(step for step in self.dispatch["jobs"]["request"]["steps"] if step.get("name") == "Validate native targeted test request")
        script = step["run"]
        self.assertIn('"validation.android": "StreamScapeTV/iptv-android"', script)
        self.assertIn('"validation.apple": "StreamScapeTV/iptv-apple"', script)
        self.assertIn("screenshot-review accepts no semantic inputs", script)
        self.assertNotIn("screenshot-review is supported only by validation.apple\n", script)

    def test_android_profile_has_exactly_five_fixed_hosted_lanes(self) -> None:
        jobs = self.workflow["jobs"]
        self.assertEqual(jobs["ci"]["if"], "${{ inputs.test_profile != 'screenshot-review' }}")
        screenshot = jobs["screenshot"]
        self.assertEqual(screenshot["runs-on"], "ubuntu-24.04")
        include = screenshot["strategy"]["matrix"]["include"]
        profiles = [row["capture_profile"] for row in include]
        self.assertEqual(
            profiles,
            ["phone-portrait", "phone-landscape", "tablet-portrait", "tablet-landscape", "tv"],
        )
        self.assertNotIn("tv-portrait", profiles)
        self.assertTrue(all("system_image" in row and "avd_device" in row for row in include))
        self.assertEqual(jobs["screenshot_plan"]["if"], "${{ inputs.test_profile == 'screenshot-review' }}")
        self.assertEqual(jobs["screenshot_finish"]["needs"], ["screenshot_plan", "screenshot"])

    def test_product_wrapper_and_cache_contract_are_fixed(self) -> None:
        steps = self.workflow["jobs"]["screenshot"]["steps"]
        run = next(step for step in steps if step["name"] == "Run fixed Android screenshot-review wrapper")
        self.assertIn("wrapper=scripts/ci/run-android-screenshot-review.sh", run["run"])
        self.assertIn("git ls-files --error-unmatch", run["run"])
        self.assertIn("wrapper must not be a symlink", run["run"])
        self.assertEqual(run["env"]["CI_ANDROID_SCREENSHOT_PROFILE"], "${{ matrix.capture_profile }}")
        self.assertEqual(run["env"]["CI_ANDROID_SCREENSHOT_CACHE_SHA256"], "${{ steps.screenshot_cache.outputs.file_sha256 }}")
        self.assertEqual(run["env"]["STREAMSCAPE_API_BASE"], "${{ secrets.STREAMSCAPE_API_BASE }}")
        self.assertEqual(run["env"]["STREAMSCAPE_DEMO_EMAIL"], "${{ secrets.STREAMSCAPE_DEMO_EMAIL }}")
        self.assertEqual(run["env"]["STREAMSCAPE_DEMO_PASSWORD"], "${{ secrets.STREAMSCAPE_DEMO_PASSWORD }}")
        for forbidden in ("STREAMSCAPE_EMAIL", "STREAMSCAPE_PASSWORD", "XTREAM_URL", "XTREAM_USERNAME", "XTREAM_PASSWORD"):
            self.assertNotIn(forbidden, run["env"])
        download = next(step for step in steps if step["name"] == "Download fixed private Android screenshot cache")
        self.assertEqual(download["with"]["operation"], "download")
        self.assertEqual(download["with"]["destination_kind"], "repository-screenshot-cache")
        self.assertEqual(download["with"]["file_name"], "xtream-screenshot-cache.zip")

    def test_package_validation_is_exact_and_flat(self) -> None:
        steps = self.workflow["jobs"]["screenshot"]["steps"]
        validate = next(step for step in steps if step["name"] == "Validate Android screenshot-review package identity")
        script = validate["run"]
        for value in ("phone-portrait", "phone-landscape", "tablet-portrait", "tablet-landscape", "tv"):
            self.assertIn(value, script)
        self.assertIn('expected_root = "repositories/iptv-android/screenshots"', script)
        self.assertIn('"source_sha": source_sha', script)
        self.assertIn('"platform": "android"', script)
        self.assertIn('"capture_profile": profile', script)
        self.assertIn("must not encode source SHA as a Drive folder", script)
        upload = next(step for step in steps if step["name"] == "Upload Android screenshot-review package to repository evidence")
        self.assertEqual(upload["with"]["destination_kind"], "repository-screenshots")
        self.assertEqual(upload["with"]["file_name"], "${{ matrix.capture_profile }}.zip")

    def test_normal_lanes_receive_demo_secrets_only_and_scrub_all_fixed_values(self) -> None:
        steps = self.workflow["jobs"]["screenshot"]["steps"]
        scrub = next(step for step in steps if step["name"] == "Scrub configured CI secrets from Android screenshot log")
        env_text = "\n".join(f"{k}={v}" for k, v in scrub["env"].items())
        for name in (
            "STREAMSCAPE_API_BASE", "STREAMSCAPE_EMAIL", "STREAMSCAPE_PASSWORD",
            "STREAMSCAPE_DEMO_EMAIL", "STREAMSCAPE_DEMO_PASSWORD",
            "XTREAM_URL", "XTREAM_USERNAME", "XTREAM_PASSWORD",
        ):
            self.assertIn(f"secrets.{name}", env_text)

    def test_drive_modes_are_fixed_and_do_not_become_general_download_api(self) -> None:
        inputs = self.drive["inputs"]
        self.assertEqual(inputs["operation"]["default"], "upload")
        script = self.drive["runs"]["steps"][0]["run"]
        self.assertIn("repository-screenshot-cache is bounded to StreamScapeTV/iptv-android", script)
        self.assertIn("repository-screenshot-cache accepts only xtream-screenshot-cache.zip", script)
        self.assertIn("fixed Android screenshot cache is unavailable in owner-private Drive", script)
        self.assertIn("repository-screenshots rejects unsupported repository/file-name combination", script)
        for name in ("phone-portrait.zip", "phone-landscape.zip", "tablet-portrait.zip", "tablet-landscape.zip", "tv.zip", "ios.zip", "tvos.zip"):
            self.assertIn(name, script)
        self.assertIn("file_sha256", self.drive["outputs"])

    def test_workflow_yaml_parses_and_no_new_workflow_entrypoint_is_needed(self) -> None:
        self.assertIn("screenshot", self.workflow["jobs"])
        self.assertIn("screenshot_plan", self.workflow["jobs"])
        self.assertIn("screenshot_finish", self.workflow["jobs"])


if __name__ == "__main__":
    unittest.main()
