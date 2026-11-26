import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import yaml


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "repository_macos_netrc", ROOT / "scripts/ci/repository_macos_netrc.py"
)
netrc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(netrc)


class MacosNetrcTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.auth = self.root / "auth"
        (self.auth / "home").mkdir(parents=True)
        self.source = self.auth / "home/.netrc"
        self.source.write_text("machine example.invalid login synthetic password synthetic\n")
        self.source.chmod(0o600)
        self.home = self.root / "native-home"
        self.home.mkdir()
        self.destination = self.home / ".netrc"
        self.native = patch.object(
            netrc.subprocess, "run",
            return_value=subprocess.CompletedProcess([], 0, str(self.home) + "\n", ""),
        )
        self.native.start()
        self.addCleanup(self.native.stop)

    def test_install_and_idempotent_cleanup(self):
        netrc.install(self.auth, self.home)
        self.assertTrue(self.destination.is_symlink())
        self.assertEqual(self.destination.read_text(), self.source.read_text())
        self.assertEqual(self.source.stat().st_mode & 0o777, 0o600)
        netrc.cleanup(self.auth)
        netrc.cleanup(self.auth)
        self.assertFalse(os.path.lexists(self.destination))
        self.assertTrue(self.source.exists())

    def test_existing_entries_survive_failed_install_and_cleanup(self):
        for kind in ("file", "directory", "dangling", "matching-link"):
            with self.subTest(kind=kind):
                if kind == "file":
                    self.destination.write_text("existing")
                elif kind == "directory":
                    self.destination.mkdir()
                else:
                    self.destination.symlink_to(
                        self.source if kind == "matching-link" else self.root / "absent"
                    )
                with self.assertRaises(FileExistsError):
                    netrc.install(self.auth, self.home)
                netrc.cleanup(self.auth)
                self.assertTrue(os.path.lexists(self.destination))
                if kind == "directory":
                    self.destination.rmdir()
                else:
                    self.destination.unlink()

    def test_stale_link_of_a_killed_job_is_replaced_on_a_persistent_runner(self):
        stale = self.root / "old-run-temp/central-registry-auth/native-netrc-abc123"
        self.destination.symlink_to(stale)
        netrc.install(self.auth, self.home)
        self.assertTrue(self.destination.is_symlink())
        self.assertNotEqual(os.readlink(self.destination), str(stale))
        self.assertEqual(self.destination.read_text(), self.source.read_text())
        netrc.cleanup(self.auth)
        self.assertFalse(os.path.lexists(self.destination))

    def test_live_link_of_a_concurrent_job_is_never_removed(self):
        other = self.root / "other-run-temp/central-registry-auth"
        other.mkdir(parents=True)
        live = other / "native-netrc-live"
        live.write_text("machine example.invalid\n")
        self.destination.symlink_to(live)
        with self.assertRaises(FileExistsError):
            netrc.install(self.auth, self.home)
        self.assertEqual(os.readlink(self.destination), str(live))

    def test_native_home_directory_alias_is_accepted(self):
        alias = self.root / "native-alias"
        alias.symlink_to(self.home, target_is_directory=True)
        netrc.install(self.auth, alias)
        self.assertTrue(self.destination.is_symlink())
        netrc.cleanup(self.auth)
        self.assertFalse(os.path.lexists(self.destination))

    def test_changed_entry_is_preserved(self):
        for kind in ("file", "retargeted"):
            with self.subTest(kind=kind):
                netrc.install(self.auth, self.home)
                self.destination.unlink()
                if kind == "file":
                    self.destination.write_text("replacement")
                else:
                    self.destination.symlink_to(self.root / "unrelated")
                with self.assertRaisesRegex(ValueError, "entry changed"):
                    netrc.cleanup(self.auth)
                self.assertTrue(os.path.lexists(self.destination))
                self.destination.unlink()
                netrc.cleanup(self.auth)

    def test_native_home_mismatch_and_public_credentials_refused(self):
        with self.assertRaisesRegex(ValueError, "native macOS home"):
            netrc.install(self.auth, self.root)
        self.source.chmod(0o644)
        with self.assertRaisesRegex(ValueError, "private regular file"):
            netrc.install(self.auth, self.home)
        self.assertFalse(os.path.lexists(self.destination))

    def test_marker_failure_removes_new_link(self):
        with patch.object(netrc.json, "dump", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                netrc.install(self.auth, self.home)
        self.assertFalse(os.path.lexists(self.destination))

    def test_interrupted_install_after_link_is_recovered(self):
        create_link = Path.symlink_to

        def interrupted(destination, source):
            create_link(destination, source)
            raise KeyboardInterrupt()

        with patch.object(Path, "symlink_to", interrupted):
            with self.assertRaises(KeyboardInterrupt):
                netrc.install(self.auth, self.home)
        owned = json.loads((self.auth / "native-netrc.json").read_text())
        self.assertTrue(self.destination.is_symlink())
        netrc.cleanup(self.auth)
        self.assertFalse(os.path.lexists(self.destination))
        self.assertFalse(Path(owned["source"]).exists())

    def test_interrupted_install_before_link_does_not_touch_native_home(self):
        with patch.object(Path, "symlink_to", side_effect=KeyboardInterrupt()):
            with self.assertRaises(KeyboardInterrupt):
                netrc.install(self.auth, self.home)
        netrc.cleanup(self.auth)
        self.assertFalse(os.path.lexists(self.destination))



if __name__ == "__main__":
    unittest.main()
