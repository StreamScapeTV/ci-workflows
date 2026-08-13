from __future__ import annotations

import argparse
import io
import tempfile
import unittest
from pathlib import Path

from ci_workflows.ciw_device_lock import configure_device_lock, execute_device_lock
from ci_workflows.ciw_types import CIWContext, CIWError

ROOT = Path(__file__).resolve().parents[1]


class DeviceLockCIWAdapterTests(unittest.TestCase):
    @staticmethod
    def environment(root: Path) -> dict[str, str]:
        return {
            "CIW_DEVICE_LOCK_ROOT": str(root),
            "GITHUB_REPOSITORY": "StreamScapeTV/iptv-android",
            "GITHUB_RUN_ID": "445566",
            "GITHUB_RUN_ATTEMPT": "1",
            "CIW_LOCK_DEVICE_FAMILY": "android",
            "CIW_LOCK_DEVICE_CAPABILITY": "instrumentation",
            "CIW_LOCK_DEVICE_IDENTITY_HASH": "a" * 64,
            "CIW_LOCK_TESTED_SOURCE_SHA": "b" * 40,
            "CIW_LOCK_AUTHORIZATION_RECEIPT": "issue-136-owner-authorized-android",
            "CIW_LOCK_REQUEST_ID": "issue-136-ciw-adapter",
            "CIW_LOCK_LEASE_SECONDS": "300",
            "CIW_LOCK_RESOURCE_RECEIPT": "",
            "CIW_LOCK_MINIMUM_REMAINING_SECONDS": "30",
        }

    @staticmethod
    def context(environment: dict[str, str]) -> CIWContext:
        return CIWContext(
            root=ROOT,
            environment=environment,
            stdout=io.StringIO(),
            stderr=io.StringIO(),
        )

    def test_configure_exposes_only_the_bounded_phase_selector(self) -> None:
        parser = argparse.ArgumentParser()
        configure_device_lock(parser)
        for phase in ("acquire", "verify", "release", "residue"):
            self.assertEqual(phase, parser.parse_args(["--phase", phase]).phase)
        with self.assertRaises(SystemExit):
            parser.parse_args(["--phase", "caller-selected-backend"])
        with self.assertRaises(SystemExit):
            parser.parse_args([])

    def test_registered_execute_projects_one_typed_result(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "lock-root"
            root.mkdir(mode=0o700)
            environment = self.environment(root)
            result = execute_device_lock(
                argparse.Namespace(phase="acquire"),
                self.context(environment),
            )
            self.assertEqual("device", result.domain)
            self.assertEqual("lock", result.operation)
            self.assertEqual("acquired", result.outputs["result"])
            self.assertTrue(result.outputs["resource_lock_receipt"].startswith("dlr1."))
            self.assertRegex(result.outputs["receipt_id"], r"^[0-9a-f]{64}$")

    def test_registered_execute_preserves_safe_device_lock_error_code(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "unsafe-root"
            root.mkdir(mode=0o755)
            environment = self.environment(root)
            with self.assertRaises(CIWError) as caught:
                execute_device_lock(
                    argparse.Namespace(phase="acquire"),
                    self.context(environment),
                )
            self.assertEqual("device", caught.exception.domain)
            self.assertEqual("backend_unavailable", caught.exception.code)
            self.assertNotIn(str(root), str(caught.exception))


if __name__ == "__main__":
    unittest.main()
