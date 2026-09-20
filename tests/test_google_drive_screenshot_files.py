# LEGACY_MIGRATION_RESIDUE: This file verifies still-live compatibility whose source currently contains concrete consumer identity; do not extend that identity coupling.
from pathlib import Path
import os
import subprocess
import tempfile
import unittest
import yaml

ROOT = Path(__file__).resolve().parents[1]


class GoogleDriveScreenshotFileTests(unittest.TestCase):
    def setUp(self) -> None:
        self.path = ROOT / "actions/google-drive/action.yml"
        self.text = self.path.read_text(encoding="utf-8")
        self.action = yaml.safe_load(self.text)

    def test_direct_review_directory_mode_is_bounded(self) -> None:
        inputs = self.action["inputs"]
        self.assertFalse(inputs["file_name"]["required"])
        self.assertIn("upload-directory", inputs["operation"]["description"])
        for allowed in (
            "StreamScapeTV/iptv-apple:review/ios",
            "StreamScapeTV/iptv-apple:review/tvos",
            "StreamScapeTV/iptv-android:review/phone-portrait",
            "StreamScapeTV/iptv-android:review/phone-landscape",
            "StreamScapeTV/iptv-android:review/tablet-portrait",
            "StreamScapeTV/iptv-android:review/tablet-landscape",
            "StreamScapeTV/iptv-android:review/tv",
        ):
            self.assertIn(allowed, self.text)
        self.assertNotIn("finalized/", self.text)
        self.assertIn("upload-directory does not accept file_name", self.text)
        self.assertIn("direct screenshot evidence directory exceeds the bounded 40-file limit", self.text)
        self.assertIn("canonical screen id with .png or .json", self.text)
        self.assertIn("direct screenshot evidence must contain PNG/JSON pairs", self.text)
        self.assertIn("direct screenshot PNG is missing adjacent metadata", self.text)
        self.assertIn("direct screenshot metadata is missing adjacent PNG", self.text)
        self.assertIn("screen id is not valid for the repository/review variant", self.text)

    def test_generic_action_avoids_macos_missing_bash_and_gnu_helpers(self) -> None:
        self.assertNotIn("mapfile", self.text)
        self.assertNotIn("readarray", self.text)
        self.assertNotIn("md5sum", self.text)
        self.assertNotIn("sha256sum", self.text)
        self.assertIn("screenshot_files=()", self.text)
        self.assertIn("while IFS= read -r screenshot_file; do", self.text)
        self.assertIn('screenshot_files+=("${screenshot_file}")', self.text)
        self.assertIn("candidate_rows=()", self.text)
        self.assertIn("while IFS= read -r candidate_row; do", self.text)
        self.assertIn('candidate_rows+=("${candidate_row}")', self.text)
        self.assertIn("file_digest() {", self.text)
        self.assertIn("hashlib.new(algorithm)", self.text)
        self.assertIn("hashlib.sha256(sys.stdin.buffer.read())", self.text)
        self.assertIn("LC_ALL=C sort", self.text)

    def test_directory_enumeration_runs_with_bash_32_compatibility(self) -> None:
        script = r'''
set -Eeuo pipefail
screenshot_files=()
while IFS= read -r screenshot_file; do
  screenshot_files+=("${screenshot_file}")
done < <(find "$1" -mindepth 1 -maxdepth 1 -type f -print | LC_ALL=C sort)
printf '%s\n' "${screenshot_files[@]}"
'''
        with tempfile.TemporaryDirectory(prefix="drive upload ") as temporary:
            root = Path(temporary)
            expected = [root / "mobile.home.json", root / "mobile.home.png"]
            for path in reversed(expected):
                path.write_bytes(path.name.encode("utf-8"))
            result = subprocess.run(
                ["bash", "-c", script, "bash", str(root)],
                env={**os.environ, "BASH_COMPAT": "3.2"},
                capture_output=True,
                text=True,
                check=False,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), [str(path) for path in expected])

    def test_legacy_zip_mode_remains_compatible_but_separate(self) -> None:
        for name in ("ios.zip", "tvos.zip", "phone-portrait.zip", "tv.zip"):
            self.assertIn(name, self.text)
        self.assertIn("legacy repository screenshot ZIP upload", self.text)
        self.assertIn("review evidence is replaceable until finalized", self.text)


if __name__ == "__main__":
    unittest.main()
