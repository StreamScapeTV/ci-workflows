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

    def test_failed_entrypoint_still_cleans_owned_link_and_credentials(self):
        netrc.install(self.auth, self.home)
        workflow = yaml.safe_load((ROOT / ".github/workflows/repository.yml").read_text())
        steps = {step.get("name"): step for step in workflow["jobs"]["execute"]["steps"]}
        install = steps["Expose isolated netrc to native macOS package resolution"]
        self.assertIn("inputs.operation != 'device-test'", install["if"])
        self.assertIn("host_os == 'macos'", install["if"])
        cleanup = steps["Cleanup ephemeral registry and repository evidence"]
        self.assertIn("always()", cleanup["if"])
        # Run the real cleanup shell after a failed product command with synthetic paths.
        central = self.root / "central-ci/scripts/ci"
        central.mkdir(parents=True)
        (central / "repository_macos_netrc.py").symlink_to(Path(netrc.__file__))
        self.auth.rename(self.root / "central-registry-auth")
        # Install again at the actual fixed location expected by the workflow.
        self.destination.unlink()
        auth = self.root / "central-registry-auth"
        (auth / "native-netrc.json").unlink()
        netrc.install(auth, self.home)
        self.native.stop()
        result = subprocess.run(
            ["bash", "-c", "false\n" + cleanup["run"]], cwd=self.root,
            env={**os.environ, "RUNNER_TEMP": str(self.root)}, capture_output=True, text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(os.path.lexists(self.destination))
        self.assertFalse(auth.exists())


if __name__ == "__main__":
    unittest.main()
