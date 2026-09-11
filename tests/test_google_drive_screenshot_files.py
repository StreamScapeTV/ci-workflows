from pathlib import Path
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

    def test_legacy_zip_mode_remains_compatible_but_separate(self) -> None:
        for name in ("ios.zip", "tvos.zip", "phone-portrait.zip", "tv.zip"):
            self.assertIn(name, self.text)
        self.assertIn("legacy repository screenshot ZIP upload", self.text)
        self.assertIn("review evidence is replaceable until finalized", self.text)


if __name__ == "__main__":
    unittest.main()
