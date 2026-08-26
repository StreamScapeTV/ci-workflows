"""Regression coverage for retained public Central contract smoke runner routing."""

from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HOSTED_CONTROL_SMOKES = (
    ".github/workflows/device-validation-contract-smoke.yml",
    ".github/workflows/device-lock-contract-smoke.yml",
)


class PublicRepositorySmokeRoutingTests(unittest.TestCase):
    def test_retained_portable_contract_smokes_use_only_canonical_hosted_linux(self) -> None:
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

    def test_device_lock_remains_synthetic_with_one_hosted_job(self) -> None:
        source = (
            ROOT / ".github/workflows/device-lock-contract-smoke.yml"
        ).read_text(encoding="utf-8")
        self.assertIn("Validate synthetic cross-run fencing contract", source)
        self.assertIn("issue-136-synthetic-owner-authorization", source)
        self.assertEqual(1, source.count("runs-on: [ubuntu-latest]"))
        self.assertNotIn("/artifacts", source)

    def test_retired_helm_contract_smoke_is_not_a_github_entrypoint(self) -> None:
        self.assertFalse((ROOT / ".github/workflows/helm-validation-smoke.yml").exists())


if __name__ == "__main__":
    unittest.main()
