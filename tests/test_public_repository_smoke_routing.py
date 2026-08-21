"""Regression coverage for public Central repository smoke runner routing."""

from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HOSTED_CONTROL_SMOKES = (
    ".github/workflows/helm-validation-smoke.yml",
    ".github/workflows/device-validation-contract-smoke.yml",
    ".github/workflows/device-lock-contract-smoke.yml",
)


class PublicRepositorySmokeRoutingTests(unittest.TestCase):
    def test_portable_public_smokes_use_only_canonical_hosted_linux(self) -> None:
        for relative in HOSTED_CONTROL_SMOKES:
            with self.subTest(workflow=relative):
                source = (ROOT / relative).read_text(encoding="utf-8")
                selectors = re.findall(r"^\s+runs-on:\s*(.+)$", source, re.MULTILINE)
                self.assertTrue(selectors, relative)
                self.assertEqual({"[ubuntu-latest]"}, set(selectors), relative)
                self.assertNotIn("[linux, amd64, general, small]", source)
                self.assertNotIn("[linux, amd64, mobile]", source)
                self.assertNotIn("[macOS, ARM64]", source)
                self.assertNotIn("self-hosted", source)

    def test_device_synthetic_capacity_is_test_data_not_runner_authority(self) -> None:
        source = (
            ROOT / ".github/workflows/device-validation-contract-smoke.yml"
        ).read_text(encoding="utf-8")
        self.assertIn("host_capacity: mobile", source)
        self.assertIn("host_capacity: apple", source)
        self.assertIn("synthetic_mode: \"true\"", source)
        self.assertEqual(3, source.count("runs-on: [ubuntu-latest]"))

    def test_device_lock_remains_synthetic_and_zero_artifact(self) -> None:
        source = (
            ROOT / ".github/workflows/device-lock-contract-smoke.yml"
        ).read_text(encoding="utf-8")
        self.assertIn("Validate synthetic cross-run fencing contract", source)
        self.assertIn("issue-136-synthetic-owner-authorization", source)
        self.assertIn("Verify device lock smoke artifacts remain zero", source)
        self.assertEqual(2, source.count("runs-on: [ubuntu-latest]"))


if __name__ == "__main__":
    unittest.main()
