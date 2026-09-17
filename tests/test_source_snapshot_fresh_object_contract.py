from pathlib import Path
import unittest
import yaml

ROOT = Path(__file__).resolve().parents[1]
DRIVE = ROOT / "actions/google-drive/action.yml"
SNAPSHOT = ROOT / "actions/source-snapshot/action.yml"
DISPATCH = ROOT / ".github/workflows/central-ci-dispatch.yml"


class SourceSnapshotFreshObjectContractTests(unittest.TestCase):
    def test_google_drive_fresh_object_is_bounded_and_uses_create_plus_raw_readback(self) -> None:
        action = yaml.safe_load(DRIVE.read_text())
        self.assertIn("fresh_object", action["inputs"])
        script = action["runs"]["steps"][0]["run"]
        self.assertIn('test "${DRIVE_DESTINATION_KIND}" = source-ref', script)
        self.assertIn('test "${DRIVE_MIME_TYPE}" = application/zip', script)
        self.assertIn('if test "${DRIVE_FRESH_OBJECT}" = true; then', script)
        self.assertIn('existing_id=""', script)
        self.assertIn("--request POST", script)
        self.assertIn("--request PATCH", script)  # retained for ordinary replaceable uploads
        self.assertIn("fresh source archive Drive readback failed", script)
        self.assertIn('cmp -s "${upload_path}" "${fresh_readback_file}"', script)

    def test_both_canonical_snapshot_entrypoints_use_fresh_object_and_lifecycle_pruning(self) -> None:
        snapshot = yaml.safe_load(SNAPSHOT.read_text())
        snapshot_steps = snapshot["runs"]["steps"]
        snapshot_by_name = {step.get("name"): step for step in snapshot_steps}
        self.assertTrue(snapshot_by_name["Upload repository snapshot archive"]["with"]["fresh_object"])
        names = [step.get("name") for step in snapshot_steps]
        self.assertLess(names.index("Validate existing canonical source archive state"), names.index("Upload repository snapshot archive"))
        self.assertLess(names.index("Verify stable identity and raw Drive readback"), names.index("Delete superseded canonical source archive objects"))
        self.assertIn("source_snapshot_lifecycle.py", snapshot_by_name["Delete superseded canonical source archive objects"]["run"])

        dispatch = yaml.safe_load(DISPATCH.read_text())
        steps = dispatch["jobs"]["source_snapshot"]["steps"]
        by_name = {step.get("name"): step for step in steps}
        self.assertTrue(by_name["Upload repository snapshot archive"]["with"]["fresh_object"])
        names = [step.get("name") for step in steps]
        self.assertLess(names.index("Validate existing canonical source archive state"), names.index("Upload repository snapshot archive"))
        self.assertLess(names.index("Verify stable identity and raw Drive readback"), names.index("Delete superseded canonical source archive objects"))
        self.assertIn("needs.request.outputs.is_tag", str(by_name["Validate existing canonical source archive state"]["env"]["TARGET_IS_TAG"]))

    def test_unrelated_drive_consumers_do_not_enable_fresh_object(self) -> None:
        for path in ROOT.glob(".github/workflows/*.yml"):
            if path == DISPATCH:
                continue
            text = path.read_text()
            if "fresh_object:" in text:
                self.fail(f"unexpected workflow fresh_object use: {path}")
        text = DRIVE.read_text()
        self.assertIn("repository-screenshots", text)
        self.assertIn("repository-screenshot-cache", text)
        self.assertIn("DRIVE_FRESH_OBJECT", text)


if __name__ == "__main__":
    unittest.main()
