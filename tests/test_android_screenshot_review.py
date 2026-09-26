# LEGACY_MIGRATION_RESIDUE: This file verifies still-live compatibility whose source currently contains concrete consumer identity; do not extend that identity coupling.
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import zipfile

import yaml

ROOT = Path(__file__).resolve().parents[1]


class AndroidScreenshotReviewTests(unittest.TestCase):
    def setUp(self) -> None:
        self.workflow = yaml.safe_load((ROOT / ".github/workflows/android.yml").read_text())
        self.dispatch = yaml.safe_load((ROOT / ".github/workflows/central-ci-dispatch.yml").read_text())
        self.drive = yaml.safe_load((ROOT / "actions/google-drive/action.yml").read_text())

    def screenshot_package_validation_script(self) -> str:
        steps = self.workflow["jobs"]["screenshot"]["steps"]
        validate = next(
            step
            for step in steps
            if step["name"] == "Validate Android screenshot-review package and extract direct evidence"
        )
        return validate["run"]

    def run_screenshot_package_validation(self, *, profile: str, captures: list[dict[str, object]]):
        source_sha = "a" * 40
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            package = root / f"{profile}.zip"
            normalized_captures = []
            with zipfile.ZipFile(package, "w") as archive:
                for index, capture in enumerate(captures):
                    value = {
                        "canonical_id": capture["canonical_id"],
                        "package_member": capture.get("package_member", f"captures/{index}.png"),
                        "canonical_path": capture.get("canonical_path", f"/{index}"),
                        "title": capture.get("title", f"Capture {index}"),
                        "presentation": capture.get("presentation", "screen"),
                        "shared_logic_family": capture.get("shared_logic_family", "fixture"),
                        "target": capture.get("target", "fixture"),
                        "navigation_plan": capture.get("navigation_plan", {"steps": []}),
                        "mounted_route": capture.get("mounted_route", f"/{index}"),
                        "width": capture.get("width", 1280),
                        "height": capture.get("height", 720),
                        "captured_at_utc": capture.get("captured_at_utc", "2026-09-26T00:00:00Z"),
                    }
                    if "variant" in capture:
                        value["variant"] = capture["variant"]
                    normalized_captures.append(value)
                    archive.writestr(
                        value["package_member"],
                        b"\x89PNG\r\n\x1a\n" + b"fixture" * 10,
                    )
                archive.writestr(
                    "index.json",
                    json.dumps(
                        {
                            "source_sha": source_sha,
                            "platform": "android",
                            "capture_profile": profile,
                            "drive_root": "repositories/iptv-android/screenshots",
                            "drive_package_path": f"repositories/iptv-android/screenshots/{profile}.zip",
                            "captures": normalized_captures,
                            "cache": {"fixture": True},
                            "normalization": {"fixture": True},
                        }
                    ),
                )
                archive.writestr("routes.json", "{}")

            github_output = root / "github-output"
            github_output.write_text("", encoding="utf-8")
            result = subprocess.run(
                ["bash", "-c", self.screenshot_package_validation_script()],
                cwd=ROOT,
                env={
                    **os.environ,
                    "PACKAGE_PATH": str(package),
                    "CAPTURE_PROFILE": profile,
                    "SOURCE_SHA": source_sha,
                    "GITHUB_OUTPUT": str(github_output),
                },
                text=True,
                capture_output=True,
                check=False,
            )
            evidence = root / "direct-evidence"
            files = {
                path.name: path.read_bytes()
                for path in evidence.iterdir()
            } if evidence.exists() else {}
            return result, files

    def test_dispatch_accepts_only_fixed_android_screenshot_request(self) -> None:
        step = next(step for step in self.dispatch["jobs"]["request"]["steps"] if step.get("name") == "Validate native targeted test request")
        script = step["run"]
        self.assertIn('"validation.android": "StreamScapeTV/iptv-android"', script)
        self.assertIn('"validation.apple": "StreamScapeTV/iptv-apple"', script)
        self.assertIn("Apple screenshot-review accepts no semantic inputs", script)
        self.assertIn("Android screenshot-review accepts only test_selectors", script)
        self.assertIn("Android screenshot-review accepts zero through 20 canonical screen ids", script)
        self.assertIn("mobile.* or tv.* canonical ids", script)
        self.assertNotIn("screenshot-review is supported only by validation.apple\n", script)

    def test_android_profile_keeps_fixed_screenshot_lanes_and_trusted_physical_runner(self) -> None:
        jobs = self.workflow["jobs"]
        self.assertEqual(
            jobs["ci"]["if"],
            "${{ inputs.test_profile != 'screenshot-review' && inputs.test_profile != 'physical-performance' }}",
        )
        physical = jobs["physical_performance"]
        self.assertEqual(physical["runs-on"], ["macOS", "ARM64"])
        self.assertNotIn("strategy", physical)
        for invented_runner in (
            "android-physical-phone",
            "android-physical-tablet",
            "android-physical-tv",
        ):
            self.assertNotIn(invented_runner, str(physical))
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
        self.assertEqual(jobs["screenshot"]["needs"], ["screenshot_plan", "screenshot_cache"])
        self.assertEqual(jobs["screenshot_finish"]["needs"], ["screenshot_plan", "screenshot_cache", "screenshot"])

    def test_product_wrapper_and_cache_contract_are_fixed(self) -> None:
        steps = self.workflow["jobs"]["screenshot"]["steps"]
        private_git = next(step for step in steps if step["name"] == "Connect to private Git service for Android screenshot capture")
        self.assertEqual(private_git["uses"], "StreamScapeTV/ci-workflows/actions/private-git@main")
        self.assertEqual(private_git["if"], "${{ steps.screenshot_selection.outputs.should_run == 'true' }}")
        self.assertEqual(private_git["env"]["TS_OAUTH_CLIENT_ID"], "${{ secrets.TS_OAUTH_CLIENT_ID }}")
        self.assertEqual(private_git["env"]["TS_OAUTH_SECRET"], "${{ secrets.TS_OAUTH_SECRET }}")
        run = next(step for step in steps if step["name"] == "Run fixed Android screenshot-review wrapper")
        self.assertIn("wrapper=scripts/ci/run-android-screenshot-review.sh", run["run"])
        self.assertIn("git ls-files --error-unmatch", run["run"])
        self.assertIn("wrapper must not be a symlink", run["run"])
        self.assertEqual(run["env"]["CI_ANDROID_SCREENSHOT_PROFILE"], "${{ matrix.capture_profile }}")
        self.assertEqual(run["env"]["CI_ANDROID_SCREENSHOT_CACHE_SHA256"], "${{ steps.screenshot_cache.outputs.file_sha256 }}")
        self.assertEqual(run["env"]["CI_ANDROID_SCREENSHOT_CANONICAL_IDS_JSON"], "${{ steps.screenshot_selection.outputs.selectors_json }}")
        self.assertEqual(run["env"]["STREAMSCAPE_API_BASE"], "${{ secrets.STREAMSCAPE_API_BASE }}")
        self.assertEqual(run["env"]["STREAMSCAPE_DEMO_EMAIL"], "${{ secrets.STREAMSCAPE_DEMO_EMAIL }}")
        self.assertEqual(run["env"]["STREAMSCAPE_DEMO_PASSWORD"], "${{ secrets.STREAMSCAPE_DEMO_PASSWORD }}")
        for forbidden in ("STREAMSCAPE_EMAIL", "STREAMSCAPE_PASSWORD", "XTREAM_URL", "XTREAM_USERNAME", "XTREAM_PASSWORD"):
            self.assertNotIn(forbidden, run["env"])
        download = next(step for step in steps if step["name"] == "Download fixed private Android screenshot cache")
        self.assertEqual(download["with"]["operation"], "download")
        self.assertEqual(download["with"]["destination_kind"], "repository-screenshot-cache")
        self.assertEqual(download["with"]["file_name"], "xtream-screenshot-cache.zip")


    def test_selector_transport_is_bounded_and_profile_filtered(self) -> None:
        plan = self.workflow["jobs"]["screenshot_plan"]
        normalize = next(step for step in plan["steps"] if step["name"] == "Normalize bounded Android screenshot selectors")
        script = normalize["run"]
        self.assertIn("zero through 20 canonical screen ids", script)
        self.assertIn("mobile.* or tv.* canonical ids", script)
        self.assertIn("selector is duplicated", script)
        self.assertIn("selection_mode={'default' if not values else 'explicit'}", script)

        screenshot = self.workflow["jobs"]["screenshot"]
        resolve = next(step for step in screenshot["steps"] if step["name"] == "Resolve Android screenshot selectors for capture profile")
        profile_script = resolve["run"]
        self.assertIn('prefix = "tv." if profile == "tv" else "mobile."', profile_script)
        self.assertIn("should_run = bool(selected)", profile_script)
        self.assertIn("if not values:", profile_script)
        self.assertIn("should_run = True", profile_script)

    def test_pre_matrix_cache_owner_probes_validates_and_bootstraps_once(self) -> None:
        cache = self.workflow["jobs"]["screenshot_cache"]
        self.assertEqual(cache["needs"], "screenshot_plan")
        self.assertEqual(cache["outputs"]["cache_sha256"], "${{ steps.cache_identity.outputs.cache_sha256 }}")
        steps = cache["steps"]
        probe = next(step for step in steps if step["name"] == "Probe fixed private Android screenshot cache")
        self.assertEqual(probe["with"]["operation"], "download")
        self.assertEqual(probe["with"]["destination_kind"], "repository-screenshot-cache")
        self.assertTrue(probe["with"]["allow_missing_download"])
        validate = next(step for step in steps if step["name"] == "Validate existing private Android screenshot cache")
        self.assertIn("inspect-cache", validate["run"])
        private_git = next(step for step in steps if step["name"] == "Connect to private Git service for Android screenshot-cache bootstrap")
        self.assertEqual(private_git["uses"], "StreamScapeTV/ci-workflows/actions/private-git@main")
        self.assertIn("cache_probe.outputs.file_found", private_git["if"])
        self.assertEqual(private_git["env"]["TS_OAUTH_CLIENT_ID"], "${{ secrets.TS_OAUTH_CLIENT_ID }}")
        self.assertEqual(private_git["env"]["TS_OAUTH_SECRET"], "${{ secrets.TS_OAUTH_SECRET }}")
        prepare_emulator = next(step for step in steps if step["name"] == "Prepare one-shot Android screenshot-cache emulator")
        self.assertIn('${sdk_root}/platform-tools', prepare_emulator["run"])
        self.assertIn('${GITHUB_PATH}', prepare_emulator["run"])
        bootstrap = next(step for step in steps if step["name"] == "Bootstrap fixed private Android screenshot cache once")
        self.assertIn("bootstrap-private-cache.sh", bootstrap["run"])
        self.assertIn("git ls-files --error-unmatch", bootstrap["run"])
        for name in ("STREAMSCAPE_API_BASE", "STREAMSCAPE_EMAIL", "STREAMSCAPE_PASSWORD", "XTREAM_URL", "XTREAM_USERNAME", "XTREAM_PASSWORD"):
            self.assertEqual(bootstrap["env"][name], "${{ secrets.%s }}" % name)
        upload = next(step for step in steps if step["name"] == "Upload refreshed private Android screenshot cache")
        self.assertEqual(upload["with"]["destination_kind"], "repository-screenshot-cache")
        self.assertEqual(upload["with"]["file_name"], "xtream-screenshot-cache.zip")
        identity = next(step for step in steps if step["name"] == "Resolve exact Android screenshot cache identity")
        self.assertIn("PROBE_USABLE", identity["env"])
        self.assertIn("UPLOAD_SHA256", identity["env"])

        normal_run = next(step for step in self.workflow["jobs"]["screenshot"]["steps"] if step["name"] == "Run fixed Android screenshot-review wrapper")
        for forbidden in ("STREAMSCAPE_EMAIL", "STREAMSCAPE_PASSWORD", "XTREAM_URL", "XTREAM_USERNAME", "XTREAM_PASSWORD"):
            self.assertNotIn(forbidden, normal_run["env"])
        verify = next(step for step in self.workflow["jobs"]["screenshot"]["steps"] if step["name"] == "Verify shared Android screenshot cache identity")
        self.assertEqual(verify["env"]["EXPECTED_CACHE_SHA256"], "${{ needs.screenshot_cache.outputs.cache_sha256 }}")

    def test_package_validation_extracts_direct_per_screen_evidence(self) -> None:
        steps = self.workflow["jobs"]["screenshot"]["steps"]
        validate = next(step for step in steps if step["name"] == "Validate Android screenshot-review package and extract direct evidence")
        script = validate["run"]
        for value in ("phone-portrait", "phone-landscape", "tablet-portrait", "tablet-landscape", "tv"):
            self.assertIn(value, script)
        self.assertIn('expected_root = "repositories/iptv-android/screenshots"', script)
        self.assertIn('"source_sha": source_sha', script)
        self.assertIn('"platform": "android"', script)
        self.assertIn('"capture_profile": profile', script)
        self.assertIn("must not encode source SHA as a Drive folder", script)
        self.assertIn('direct_dir = package.parent / "direct-evidence"', script)
        self.assertIn('screen_id = capture.get("canonical_id")', script)
        self.assertIn('variant = capture.get("variant")', script)
        self.assertIn("capture_identity = (screen_id, variant)", script)
        self.assertIn('evidence_stem = screen_id if variant is None else f"{screen_id}--variant--{variant}"', script)
        self.assertIn('(direct_dir / f"{evidence_stem}.png").write_bytes(image)', script)
        self.assertIn('(direct_dir / f"{evidence_stem}.json").write_text(', script)
        self.assertIn('"cache": index.get("cache")', script)
        self.assertIn('"normalization": index.get("normalization")', script)

        upload = next(step for step in steps if step["name"] == "Upload direct Android screenshot-review evidence")
        self.assertEqual(upload["with"]["operation"], "upload-directory")
        self.assertEqual(upload["with"]["destination_kind"], "repository-screenshots")
        self.assertEqual(upload["with"]["subdirectory"], "review/${{ steps.screenshot_package.outputs.capture_profile }}")
        self.assertEqual(upload["with"]["file_path"], "${{ steps.screenshot_package.outputs.evidence_dir }}")
        self.assertNotIn("file_name", upload["with"])

    def test_tv_variants_share_canonical_id_and_extract_without_filename_collision(self) -> None:
        canonical_id = "tv.browse.movies-series-catalog"
        result, files = self.run_screenshot_package_validation(
            profile="tv",
            captures=[
                {"canonical_id": canonical_id, "variant": "movie"},
                {"canonical_id": canonical_id, "variant": "series"},
            ],
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            set(files),
            {
                f"{canonical_id}--variant--movie.png",
                f"{canonical_id}--variant--movie.json",
                f"{canonical_id}--variant--series.png",
                f"{canonical_id}--variant--series.json",
            },
        )
        movie = json.loads(files[f"{canonical_id}--variant--movie.json"])
        series = json.loads(files[f"{canonical_id}--variant--series.json"])
        self.assertEqual(movie["screen_id"], canonical_id)
        self.assertEqual(series["screen_id"], canonical_id)
        self.assertEqual(movie["variant"], "movie")
        self.assertEqual(series["variant"], "series")

    def test_exact_duplicate_tv_capture_identity_is_rejected(self) -> None:
        result, _ = self.run_screenshot_package_validation(
            profile="tv",
            captures=[
                {"canonical_id": "tv.browse.movies-series-catalog", "variant": "movie"},
                {"canonical_id": "tv.browse.movies-series-catalog", "variant": "movie"},
            ],
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("capture identity is duplicated", result.stderr)

    def test_distinct_capture_identities_cannot_overwrite_one_evidence_stem(self) -> None:
        result, _ = self.run_screenshot_package_validation(
            profile="tv",
            captures=[
                {"canonical_id": "tv.catalog--variant--movie"},
                {"canonical_id": "tv.catalog", "variant": "movie"},
            ],
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("direct-evidence identity is invalid", result.stderr)

    def test_variant_is_bounded_to_safe_tv_capture_identity(self) -> None:
        unsafe, _ = self.run_screenshot_package_validation(
            profile="tv",
            captures=[{"canonical_id": "tv.browse.movies-series-catalog", "variant": "../movie"}],
        )
        self.assertNotEqual(unsafe.returncode, 0)
        self.assertIn("capture variant is invalid", unsafe.stderr)

        mobile, _ = self.run_screenshot_package_validation(
            profile="phone-portrait",
            captures=[{"canonical_id": "mobile.home", "variant": "movie"}],
        )
        self.assertNotEqual(mobile.returncode, 0)
        self.assertIn("capture variant is invalid", mobile.stderr)

    def test_mobile_capture_without_variant_keeps_existing_evidence_names(self) -> None:
        result, files = self.run_screenshot_package_validation(
            profile="phone-portrait",
            captures=[{"canonical_id": "mobile.home"}],
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(set(files), {"mobile.home.png", "mobile.home.json"})
        metadata = json.loads(files["mobile.home.json"])
        self.assertEqual(metadata["screen_id"], "mobile.home")
        self.assertIsNone(metadata["variant"])

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
        self.assertEqual(inputs["allow_missing_download"]["default"], "false")
        script = self.drive["runs"]["steps"][0]["run"]
        self.assertIn("repository-screenshot-cache is bounded to StreamScapeTV/iptv-android", script)
        self.assertIn("repository-screenshot-cache accepts only xtream-screenshot-cache.zip", script)
        self.assertIn("fixed Android screenshot cache is unavailable in owner-private Drive", script)
        self.assertIn("allow_missing_download is restricted to fixed Android screenshot-cache download probes", script)
        self.assertIn("file_found=false", script)
        self.assertIn("file_found=true", script)
        self.assertIn("repository-screenshots rejects unsupported legacy repository/file-name combination", script)
        self.assertIn("repository-screenshots rejects unsupported direct-evidence review path", script)
        self.assertIn("upload-directory", script)
        for name in ("phone-portrait.zip", "phone-landscape.zip", "tablet-portrait.zip", "tablet-landscape.zip", "tv.zip", "ios.zip", "tvos.zip"):
            self.assertIn(name, script)
        self.assertIn("file_sha256", self.drive["outputs"])

    def test_workflow_yaml_parses_and_no_new_workflow_entrypoint_is_needed(self) -> None:
        self.assertIn("screenshot", self.workflow["jobs"])
        self.assertIn("screenshot_plan", self.workflow["jobs"])
        self.assertIn("screenshot_cache", self.workflow["jobs"])
        self.assertIn("screenshot_finish", self.workflow["jobs"])


if __name__ == "__main__":
    unittest.main()
