from __future__ import annotations

import importlib.util
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/ci/ci_log_delete.py"
spec = importlib.util.spec_from_file_location("ci_log_delete", SCRIPT)
assert spec and spec.loader
mod = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = mod
spec.loader.exec_module(mod)


class FakeDrive:
    def __init__(self, children: dict[tuple[str, str, bool], tuple[str, ...]], nonempty: set[str] | None = None) -> None:
        self.children = children
        self.nonempty = set(nonempty or ())
        self.deleted: list[str] = []

    def exact_children(self, parent_id: str, name: str, *, folders: bool) -> tuple[str, ...]:
        return self.children.get((parent_id, name, folders), ())

    def has_any_child(self, parent_id: str) -> bool:
        return parent_id in self.nonempty

    def delete_file(self, file_id: str) -> None:
        self.deleted.append(file_id)
        self.nonempty.discard(file_id)


class CiLogDeleteTests(unittest.TestCase):
    def names(self, workflow: str, profile: str, run: int = 123, attempt: int = 2) -> tuple[str, ...]:
        return mod.expected_log_filenames(workflow, profile, run, attempt)

    def test_standard_log_semantics(self) -> None:
        semantics = [
            ("validation.android", "full"),
            ("release.android", "play"),
            ("validation.python", "release-gates"),
            ("validation.node", "frontend-full"),
            ("validation.flutter", "quality"),
            ("release.maven", "publish"),
            ("validation.container-service", "conformance"),
            ("release.public-native-image-chart", "public-native-image-chart"),
            ("oci.reproducibility", "reproducibility"),
            ("release.apple", "binary-package"),
        ]
        for semantic in semantics:
            with self.subTest(semantic=semantic):
                self.assertEqual(self.names(*semantic), ("123-2.txt",))

    def test_every_current_android_standard_profile_maps_to_standard_log(self) -> None:
        for profile in sorted(mod.ANDROID_STANDARD_PROFILES):
            with self.subTest(profile=profile):
                self.assertEqual(self.names("validation.android", profile), ("123-2.txt",))

    def test_every_current_python_node_and_flutter_profile_maps_to_standard_log(self) -> None:
        for workflow, profiles in (
            ("validation.python", mod.PYTHON_PROFILES),
            ("validation.node", mod.NODE_PROFILES),
            ("validation.flutter", mod.FLUTTER_PROFILES),
        ):
            for profile in sorted(profiles):
                with self.subTest(workflow=workflow, profile=profile):
                    self.assertEqual(self.names(workflow, profile), ("123-2.txt",))

    def test_android_screenshot_review_maps_cache_and_five_capture_logs(self) -> None:
        self.assertEqual(
            self.names("validation.android", "screenshot-review"),
            (
                "123-2-screenshot-cache.txt",
                "123-2-screenshot-phone-portrait.txt",
                "123-2-screenshot-phone-landscape.txt",
                "123-2-screenshot-tablet-portrait.txt",
                "123-2-screenshot-tablet-landscape.txt",
                "123-2-screenshot-tv.txt",
            ),
        )

    def test_apple_matrix_profiles_map_to_exact_lanes(self) -> None:
        expected = {
            "screenshot-review": ("screenshot-ios", "screenshot-tvos"),
            "build": ("hosted-build",),
            "native-component": ("native-component",),
            "test": ("hosted-test",),
            "simulator": ("hosted-simulator",),
            "host": ("macos-test",),
            "targeted-tests": ("macos-targeted-tests", "ios-targeted-tests", "tvos-targeted-tests"),
            "candidate": ("ios-build", "tvos-build", "macos-targeted-tests"),
            "full": ("ios-build", "tvos-build", "macos-test"),
            "release-build": ("release-build",),
            "testflight": ("testflight",),
            "swift-package": ("swift-package",),
        }
        for profile, lanes in expected.items():
            with self.subTest(profile=profile):
                self.assertEqual(self.names("validation.apple", profile), tuple(f"123-2-{lane}.txt" for lane in lanes))

    def test_apple_release_special_names(self) -> None:
        self.assertEqual(self.names("release.apple", "testflight"), ("123-2-testflight.txt",))
        self.assertEqual(self.names("release.apple", "swiftpm-package"), ("123-2-apple-swiftpm.txt",))

    def test_known_no_private_log_semantics_are_empty(self) -> None:
        for workflow, profile in mod.NO_PRIVATE_LOG:
            with self.subTest(workflow=workflow, profile=profile):
                self.assertEqual(self.names(workflow, profile), ())

    def test_unknown_semantics_fail_closed(self) -> None:
        for semantic in (("validation.python", "unknown"), ("maintenance.other", "delete"), ("release.apple", "bogus")):
            with self.subTest(semantic=semantic):
                with self.assertRaises(ValueError):
                    self.names(*semantic)

    def test_delete_is_exact_name_bounded_and_prunes_empty_folders(self) -> None:
        children = {
            ("root", "repo", True): ("repo-a", "repo-b"),
            ("repo-a", "main", True): ("ref-a",),
            ("repo-b", "main", True): ("ref-b",),
            ("ref-a", "123-2.txt", False): ("log-a", "log-dup"),
            ("ref-b", "123-2.txt", False): (),
        }
        drive = FakeDrive(children)
        result = mod.delete_ci_logs(
            drive,
            root_folder_id="root",
            repository="StreamScapeTV/repo",
            ref="main",
            filenames=("123-2.txt",),
        )
        self.assertEqual(result["deleted_files"], 2)
        self.assertEqual(result["deleted_ref_folders"], 2)
        self.assertEqual(result["deleted_repository_folders"], 2)
        self.assertEqual(drive.deleted, ["log-a", "log-dup", "ref-a", "repo-a", "ref-b", "repo-b"])
        self.assertNotIn("root", drive.deleted)

    def test_missing_repository_ref_and_file_are_idempotent_success(self) -> None:
        drive = FakeDrive({})
        result = mod.delete_ci_logs(
            drive,
            root_folder_id="root",
            repository="StreamScapeTV/repo",
            ref="missing",
            filenames=("123-2.txt",),
        )
        self.assertEqual(result, {"deleted_files": 0, "deleted_ref_folders": 0, "deleted_repository_folders": 0})
        self.assertEqual(drive.deleted, [])

    def test_nonempty_ref_or_repository_is_preserved(self) -> None:
        children = {
            ("root", "repo", True): ("repo-a",),
            ("repo-a", "main", True): ("ref-a",),
            ("ref-a", "123-2.txt", False): ("log-a",),
        }
        drive = FakeDrive(children, nonempty={"ref-a", "repo-a"})
        result = mod.delete_ci_logs(
            drive,
            root_folder_id="root",
            repository="StreamScapeTV/repo",
            ref="main",
            filenames=("123-2.txt",),
        )
        self.assertEqual(result["deleted_files"], 1)
        self.assertEqual(result["deleted_ref_folders"], 0)
        self.assertEqual(result["deleted_repository_folders"], 0)
        self.assertEqual(drive.deleted, ["log-a"])


if __name__ == "__main__":
    unittest.main()

class CiLogDeleteSourceContractTests(unittest.TestCase):
    def test_central_dispatch_has_exact_cleanup_validation_and_job_without_log_upload(self) -> None:
        text = (ROOT / ".github/workflows/central-ci-dispatch.yml").read_text(encoding="utf-8")
        self.assertIn("Validate private CI-log deletion request", text)
        self.assertIn("workflow_key == 'maintenance.ci-log-delete'", text)
        self.assertIn("maintenance.ci-log-delete requires exactly the bounded source-run identity fields", text)
        self.assertIn("ci_log_delete:", text)
        self.assertIn("scripts/ci/ci_log_delete.py", text)
        block = text.split("  ci_log_delete:\n", 1)[1].split("\n  branch_delete:\n", 1)[0]
        self.assertNotIn("GOOGLE_DRIVE_ROOT_FOLDER_ID", block)
        self.assertNotIn("file_name:", block)
        self.assertNotIn("Upload CI log", block)
        self.assertNotIn("central-ci.log", block)
        self.assertIn("GOOGLE_DRIVE_CI_LOGS_FOLDER_ID", block)
        self.assertIn("phase: start", block)
        self.assertIn("phase: finish", block)

    def test_mapping_literals_match_current_upload_workflows(self) -> None:
        apple = (ROOT / ".github/workflows/apple.yml").read_text(encoding="utf-8")
        android = (ROOT / ".github/workflows/android.yml").read_text(encoding="utf-8")
        swiftpm = (ROOT / ".github/workflows/apple-swiftpm.yml").read_text(encoding="utf-8")
        for lanes in mod.APPLE_LANES.values():
            for lane in lanes:
                self.assertIn(f'"lane":"{lane}"', apple)
        self.assertIn("${{ github.run_id }}-${{ github.run_attempt }}-${{ matrix.lane }}.txt", apple)
        for profile in mod.ANDROID_SCREENSHOT_PROFILES:
            self.assertIn(f"capture_profile: {profile}", android)
        self.assertIn("${{ github.run_id }}-${{ github.run_attempt }}-screenshot-cache.txt", android)
        self.assertIn("${{ github.run_id }}-${{ github.run_attempt }}-screenshot-${{ matrix.capture_profile }}.txt", android)
        self.assertIn("${{ github.run_id }}-${{ github.run_attempt }}-apple-swiftpm.txt", swiftpm)

    def test_inventory_tracks_fixed_cleanup_script(self) -> None:
        inventory = (ROOT / "INVENTORY.yaml").read_text(encoding="utf-8")
        self.assertIn("ci_log_delete: scripts/ci/ci_log_delete.py", inventory)
