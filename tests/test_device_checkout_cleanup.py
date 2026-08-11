from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from ci_workflows.ciw_device import _source_path
from ci_workflows.device_execution import assert_zero_device_residue, cleanup_checkout_path, cleanup_device_state, validate_exact_checkout
from ci_workflows.device_types import DeviceValidationError
from device_test_support import SHA

class ExactCheckoutAndCleanupTests(unittest.TestCase):
    def test_exact_checkout_equality_and_moved_sha(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            subprocess.run(["git", "-C", str(root), "config", "user.email", "test@example.invalid"], check=True)
            subprocess.run(["git", "-C", str(root), "config", "user.name", "Device Test"], check=True)
            (root / "tracked.txt").write_text("one\n")
            subprocess.run(["git", "-C", str(root), "add", "tracked.txt"], check=True)
            subprocess.run(["git", "-C", str(root), "commit", "-qm", "one"], check=True)
            head = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
            validate_exact_checkout(root, head)
            with self.assertRaisesRegex(DeviceValidationError, "source_mismatch"):
                validate_exact_checkout(root, "f" * 40)
            (root / "untracked.txt").write_text("dirty\n")
            with self.assertRaisesRegex(DeviceValidationError, "source_mismatch"):
                validate_exact_checkout(root, head)

    def test_ciw_cleanup_is_no_follow_and_proves_absence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            outside = workspace / "outside.txt"
            outside.write_text("keep")
            checkout = workspace / ".ciw"
            checkout.mkdir()
            (checkout / "nested").mkdir()
            (checkout / "nested/file.txt").write_text("remove")
            (checkout / "escape").symlink_to(outside)
            cleanup_checkout_path(workspace, ".ciw")
            self.assertFalse(checkout.exists())
            self.assertFalse(checkout.is_symlink())
            self.assertEqual("keep", outside.read_text())

    def test_symlink_checkout_is_unlinked_without_following_target(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            outside = workspace / "outside"
            outside.mkdir()
            (outside / "sentinel").write_text("keep")
            (workspace / ".ciw").symlink_to(outside, target_is_directory=True)
            cleanup_checkout_path(workspace, ".ciw")
            self.assertFalse((workspace / ".ciw").exists())
            self.assertEqual("keep", (outside / "sentinel").read_text())

    def test_device_state_cleanup_preserves_outside_sentinel(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            state = root / "state"
            outside = root / "outside.txt"
            outside.write_text("keep")
            target = state / "device-validation"
            target.mkdir(parents=True)
            (target / "escape").symlink_to(outside)
            cleanup_device_state(state)
            assert_zero_device_residue(state)
            self.assertEqual("keep", outside.read_text())

    def test_execution_source_root_is_fixed_and_never_follows_a_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            source = workspace / "source"
            source.mkdir()
            environment = {"GITHUB_WORKSPACE": str(workspace)}
            self.assertEqual(source.resolve(), _source_path(workspace, "source", environment))
            with self.assertRaisesRegex(DeviceValidationError, "invalid_input"):
                _source_path(workspace, ".ciw", environment)
            source.rmdir()
            outside = workspace / "outside"
            outside.mkdir()
            source.symlink_to(outside, target_is_directory=True)
            with self.assertRaisesRegex(DeviceValidationError, "invalid_input"):
                _source_path(workspace, "source", environment)
