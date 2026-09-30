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

    def test_candidate_route_directory_mode_is_bounded(self) -> None:
        inputs = self.action["inputs"]
        self.assertFalse(inputs["file_name"]["required"])
        self.assertIn("upload-directory", inputs["operation"]["description"])
        for allowed in (
            "StreamScapeTV/iptv-apple:ios",
            "StreamScapeTV/iptv-apple:tvos",
            "StreamScapeTV/iptv-android:phone-portrait",
            "StreamScapeTV/iptv-android:phone-landscape",
            "StreamScapeTV/iptv-android:tablet-portrait",
            "StreamScapeTV/iptv-android:tablet-landscape",
            "StreamScapeTV/iptv-android:tv",
        ):
            self.assertIn(allowed, self.text)
        self.assertNotIn("review/ios", self.text)
        self.assertNotIn("review/phone-portrait", self.text)
        self.assertIn("upload-directory does not accept file_name", self.text)
        self.assertIn("candidate screenshot evidence exceeds the bounded 20-route limit", self.text)
        self.assertIn("candidate screenshot route/evidence id is invalid", self.text)
        self.assertIn("candidate screenshot PNG is missing", self.text)
        self.assertIn("candidate screenshot metadata is missing", self.text)
        self.assertIn('candidate_png="${route_path}/candidate.png"', self.text)
        self.assertIn('candidate_json="${metadata_dir}/candidate.json"', self.text)
        self.assertIn('route_folder_id="$(ensure_folder "${target_folder_id}" "${screen_id}")"', self.text)
        self.assertIn('meta_folder_id="$(ensure_folder "${route_folder_id}" _meta)"', self.text)
        self.assertNotIn("baseline.png", self.text)
        self.assertNotIn("baseline.json", self.text)

    def test_generic_action_avoids_macos_missing_bash_and_gnu_helpers(self) -> None:
        self.assertNotIn("mapfile", self.text)
        self.assertNotIn("readarray", self.text)
        self.assertNotIn("md5sum", self.text)
        self.assertNotIn("sha256sum", self.text)
        self.assertIn("route_dirs=()", self.text)
        self.assertIn("while IFS= read -r route_dir; do", self.text)
        self.assertIn('route_dirs+=("${route_dir}")', self.text)
        self.assertIn("candidate_rows=()", self.text)
        self.assertIn("while IFS= read -r candidate_row; do", self.text)
        self.assertIn('candidate_rows+=("${candidate_row}")', self.text)
        self.assertIn("file_digest() {", self.text)
        self.assertIn("hashlib.new(algorithm)", self.text)
        self.assertIn("hashlib.sha256(sys.stdin.buffer.read())", self.text)
        self.assertIn("LC_ALL=C sort", self.text)

    def test_route_directory_enumeration_runs_with_bash_32_compatibility(self) -> None:
        script = r'''
set -Eeuo pipefail
route_dirs=()
while IFS= read -r route_dir; do
  route_dirs+=("${route_dir}")
done < <(find "$1" -mindepth 1 -maxdepth 1 -type d -print | LC_ALL=C sort)
printf '%s\n' "${route_dirs[@]}"
'''
        with tempfile.TemporaryDirectory(prefix="drive upload ") as temporary:
            root = Path(temporary)
            expected = [root / "mobile.home", root / "mobile.movies"]
            for route in reversed(expected):
                (route / "_meta").mkdir(parents=True)
                (route / "candidate.png").write_bytes(b"png")
                (route / "_meta" / "candidate.json").write_text("{}", encoding="utf-8")
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
        self.assertIn("candidate evidence is replaceable during review", self.text)


if __name__ == "__main__":
    unittest.main()
