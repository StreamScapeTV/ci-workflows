from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path

import yaml

from ci_workflows.ciw_device_lock import main

ROOT = Path(__file__).resolve().parents[1]


class DeviceLockActionTests(unittest.TestCase):
    def action(self) -> dict[str, object]:
        return yaml.safe_load((ROOT / "actions/device-lock/action.yml").read_text(encoding="utf-8"))

    def environment(self, root: Path, output: Path) -> dict[str, str]:
        return {
            "CIW_DEVICE_LOCK_ROOT": str(root),
            "GITHUB_REPOSITORY": "StreamScapeTV/iptv-android",
            "GITHUB_RUN_ID": "12345",
            "GITHUB_RUN_ATTEMPT": "1",
            "GITHUB_OUTPUT": str(output),
            "CIW_LOCK_DEVICE_FAMILY": "android",
            "CIW_LOCK_DEVICE_CAPABILITY": "instrumentation",
            "CIW_LOCK_DEVICE_IDENTITY_HASH": "a" * 64,
            "CIW_LOCK_TESTED_SOURCE_SHA": "b" * 40,
            "CIW_LOCK_AUTHORIZATION_RECEIPT": "issue-136-owner-authorized-android",
            "CIW_LOCK_REQUEST_ID": "issue-136-action-test",
            "CIW_LOCK_LEASE_SECONDS": "300",
            "CIW_LOCK_RESOURCE_RECEIPT": "",
            "CIW_LOCK_MINIMUM_REMAINING_SECONDS": "30",
        }

    @staticmethod
    def output_values(path: Path) -> dict[str, str]:
        return dict(
            line.split("=", 1)
            for line in path.read_text(encoding="utf-8").splitlines()
        )

    def test_action_has_no_caller_selected_backend_or_raw_device_identity(self) -> None:
        action = self.action()
        inputs = set(action["inputs"])
        self.assertEqual(
            {
                "phase",
                "device_family",
                "device_capability",
                "device_identity_hash",
                "tested_source_sha",
                "authorization_receipt",
                "request_id",
                "lease_seconds",
                "resource_lock_receipt",
                "minimum_remaining_seconds",
            },
            inputs,
        )
        for forbidden in (
            "backend",
            "backend_root",
            "endpoint",
            "runner",
            "runner_name",
            "serial",
            "udid",
            "raw_device_identity",
            "credential",
            "secret_name",
        ):
            self.assertNotIn(forbidden, inputs)
        step = action["runs"]["steps"][0]
        self.assertEqual("bash", step["shell"])
        self.assertIn("scripts/ci/ciw.py", step["run"])
        self.assertIn("device lock --phase", step["run"])
        self.assertIn("${CIW_LOCK_PHASE}", step["run"])
        self.assertNotIn("scripts/ci/device_lock.py", step["run"])
        self.assertNotIn("eval ", step["run"])
        self.assertNotIn("curl ", step["run"])
        self.assertNotIn("rm -rf", step["run"])
        self.assertNotIn("CIW_DEVICE_LOCK_ROOT", step.get("env", {}))

    def test_adapter_acquire_verify_release_residue_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "lock-root"
            root.mkdir(mode=0o700)
            output = Path(directory) / "output"
            environment = self.environment(root, output)

            stdout = io.StringIO()
            stderr = io.StringIO()
            self.assertEqual(0, main(["acquire"], environment=environment, stdout=stdout, stderr=stderr))
            acquired = self.output_values(output)
            self.assertEqual("acquired", acquired["result"])
            self.assertEqual("", acquired["failure_code"])
            self.assertTrue(acquired["resource_lock_receipt"].startswith("dlr1."))
            self.assertRegex(acquired["receipt_id"], r"^[0-9a-f]{64}$")
            self.assertEqual("", stderr.getvalue())

            output.write_text("", encoding="utf-8")
            environment["CIW_LOCK_RESOURCE_RECEIPT"] = acquired["resource_lock_receipt"]
            self.assertEqual(0, main(["verify"], environment=environment, stdout=io.StringIO(), stderr=io.StringIO()))
            verified = self.output_values(output)
            self.assertEqual("verified", verified["result"])
            self.assertEqual(acquired["receipt_id"], verified["receipt_id"])

            output.write_text("", encoding="utf-8")
            self.assertEqual(0, main(["release"], environment=environment, stdout=io.StringIO(), stderr=io.StringIO()))
            released = self.output_values(output)
            self.assertEqual("released", released["result"])
            self.assertRegex(released["release_evidence"], r"^[0-9a-f]{64}$")
            self.assertRegex(released["cleanup_evidence"], r"^[0-9a-f]{64}$")
            self.assertEqual("false", released["idempotent"])

            output.write_text("", encoding="utf-8")
            self.assertEqual(0, main(["residue"], environment=environment, stdout=io.StringIO(), stderr=io.StringIO()))
            clean = self.output_values(output)
            self.assertEqual("clean", clean["result"])
            self.assertEqual(released["cleanup_evidence"], clean["cleanup_evidence"])

    def test_adapter_failure_projection_never_prints_backend_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "unsafe-root"
            root.mkdir(mode=0o755)
            output = Path(directory) / "output"
            environment = self.environment(root, output)
            stderr = io.StringIO()
            code = main(["acquire"], environment=environment, stdout=io.StringIO(), stderr=stderr)
            self.assertEqual(2, code)
            failure = self.output_values(output)
            self.assertEqual("failure", failure["result"])
            self.assertEqual("backend_unavailable", failure["failure_code"])
            self.assertNotIn(str(root), stderr.getvalue())
            self.assertEqual("device-lock:backend_unavailable\n", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
