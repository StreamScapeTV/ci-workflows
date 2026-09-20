from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/android.yml"
DISPATCH = ROOT / ".github/workflows/central-ci-dispatch.yml"
HELPER = ROOT / "scripts/ci/android_physical_attached.py"


def load_helper():
    spec = importlib.util.spec_from_file_location("android_physical_attached", HELPER)
    if spec is None or spec.loader is None:
        raise RuntimeError("unable to load owner-attached Android helper")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class AndroidPhysicalPerformanceProfileTests(unittest.TestCase):
    def setUp(self) -> None:
        self.android = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
        self.dispatch = yaml.safe_load(DISPATCH.read_text(encoding="utf-8"))
        self.helper = load_helper()

    def test_owner_attached_provider_and_wrapper_are_fixed(self) -> None:
        self.assertEqual(self.helper.PROVIDER, "owner-attached")
        self.assertEqual(
            self.helper.WRAPPER,
            "scripts/ci/run-android-physical-performance.sh",
        )

    def test_device_parser_distinguishes_ready_and_nonready(self) -> None:
        ready, nonready = self.helper._parse_devices(
            """List of devices attached
abc123 device product:a model:SM-A325F transport_id:1
def456 unauthorized usb:1-1 transport_id:2
"""
        )
        self.assertEqual(["abc123"], ready)
        self.assertEqual([("def456", "unauthorized")], nonready)

    def test_discovery_requires_exactly_one_real_device(self) -> None:
        def result(stdout: str, returncode: int = 0):
            return subprocess.CompletedProcess([], returncode, stdout=stdout, stderr="")

        with mock.patch.object(self.helper, "_adb_path", return_value="/usr/bin/adb"), mock.patch.object(
            self.helper, "_run", return_value=result("List of devices attached\n")
        ):
            with self.assertRaises(self.helper.ExpectedFailure) as raised:
                self.helper.discover_owner_device(Path("/tmp/private.log"))
            self.assertEqual("owner_device_unavailable", raised.exception.code)

        with mock.patch.object(self.helper, "_adb_path", return_value="/usr/bin/adb"), mock.patch.object(
            self.helper,
            "_run",
            return_value=result(
                "List of devices attached\none device model:A\ntwo device model:B\n"
            ),
        ):
            with self.assertRaises(self.helper.ExpectedFailure) as raised:
                self.helper.discover_owner_device(Path("/tmp/private.log"))
            self.assertEqual("owner_device_ambiguous", raised.exception.code)

    def test_discovery_rejects_emulator_and_records_redacted_physical_identity(self) -> None:
        def result(stdout: str, returncode: int = 0):
            return subprocess.CompletedProcess([], returncode, stdout=stdout, stderr="")

        with mock.patch.object(self.helper, "_adb_path", return_value="/usr/bin/adb"), mock.patch.object(
            self.helper,
            "_run",
            return_value=result("List of devices attached\nemulator-5554 device model:sdk\n"),
        ):
            with self.assertRaises(self.helper.ExpectedFailure) as raised:
                self.helper.discover_owner_device(Path("/tmp/private.log"))
            self.assertEqual("owner_device_not_physical", raised.exception.code)

        props = {
            "ro.kernel.qemu": "0",
            "ro.product.marketname": "Galaxy A32",
            "ro.product.model": "SM-A325F",
            "ro.build.version.release": "13",
            "ro.build.version.sdk": "33",
            "ro.build.characteristics": "default",
        }

        def fake_prop(_adb, _serial, prop, _log):
            return props[prop]

        def fake_run(args, **_kwargs):
            if args[1:3] == ["devices", "-l"]:
                return result("List of devices attached\nprivate-serial device model:SM-A325F\n")
            if args[-2:] == ["wm", "size"]:
                return result("Physical size: 1080x2400\n")
            if args[-2:] == ["wm", "density"]:
                return result("Physical density: 420\n")
            return result("")

        with mock.patch.object(self.helper, "_adb_path", return_value="/usr/bin/adb"), mock.patch.object(
            self.helper, "_run", side_effect=fake_run
        ), mock.patch.object(self.helper, "_adb_prop", side_effect=fake_prop), mock.patch.dict(
            "os.environ", {"GITHUB_RUN_ID": "12345", "GITHUB_RUN_ATTEMPT": "2"}, clear=False
        ):
            device = self.helper.discover_owner_device(Path("/tmp/private.log"))

        self.assertEqual("phone", device["deviceClass"])
        self.assertEqual("Galaxy A32", device["deviceModel"])
        self.assertEqual("13", device["osVersion"])
        self.assertEqual(33, device["androidApiLevel"])
        self.assertEqual("github-12345-2", device["providerBuildId"])
        self.assertEqual(
            "device-" + hashlib.sha256(b"private-serial").hexdigest()[:32],
            device["providerSessionId"],
        )
        self.assertNotIn("private-serial", json.dumps({k: v for k, v in device.items() if k != "serial"}))

    def _valid_product_result(self, root: Path) -> tuple[Path, Path, dict[str, object]]:
        evidence = root / "evidence"
        evidence.mkdir()
        artifact = evidence / "streamscape-release.apk"
        artifact.write_bytes(b"immutable-release-apk")
        expected: dict[str, object] = {
            "sourceSha": "a" * 40,
            "deviceClass": "phone",
            "deviceModel": "Galaxy A32",
            "osVersion": "13",
            "androidApiLevel": 33,
            "provider": "owner-attached",
            "providerBuildId": "github-123-1",
            "providerSessionId": "device-abcdef",
        }
        result = {
            "schemaVersion": 1,
            **expected,
            "buildMode": "release",
            "artifactFile": "streamscape-release.apk",
            "artifactSha256": hashlib.sha256(b"immutable-release-apk").hexdigest(),
            "status": "passed",
            "measurements": [
                {
                    "id": "startup/cold_activity_total_p95",
                    "value": 950,
                    "unit": "ms",
                    "status": "observed",
                }
            ],
            "scenarios": [{"id": "cold-process-launch-samples", "status": "passed"}],
        }
        result_path = root / "result.json"
        result_path.write_text(json.dumps(result), encoding="utf-8")
        return result_path, evidence, expected

    def test_product_result_proves_exact_release_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            result_path, evidence, expected = self._valid_product_result(Path(td))
            value = self.helper.validate_product_result(result_path, evidence, expected)
            self.assertEqual(
                hashlib.sha256(b"immutable-release-apk").hexdigest(),
                value["artifactSha256"],
            )
            self.assertFalse(value["measurementFailed"])

            raw = json.loads(result_path.read_text(encoding="utf-8"))
            raw["artifactSha256"] = "0" * 64
            result_path.write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaises(self.helper.ExpectedFailure) as raised:
                self.helper.validate_product_result(result_path, evidence, expected)
            self.assertEqual("product_artifact_mismatch", raised.exception.code)

    def test_workflow_uses_existing_mac_arm64_runner_and_no_browserstack_secrets(self) -> None:
        secrets = self.android["on"]["workflow_call"]["secrets"]
        self.assertNotIn("BROWSERSTACK_USERNAME", secrets)
        self.assertNotIn("BROWSERSTACK_ACCESS_KEY", secrets)

        job = self.android["jobs"]["physical_performance"]
        self.assertEqual(["macOS", "ARM64"], job["runs-on"])
        serialized = json.dumps(job)
        self.assertNotIn("BrowserStack", serialized)
        self.assertNotIn("android_physical_browserstack.py", serialized)
        self.assertIn("android_physical_attached.py", serialized)
        self.assertIn("owner-attached physical-performance", serialized)

    def test_product_environment_strips_provider_cloud_credentials(self) -> None:
        helper_text = HELPER.read_text(encoding="utf-8")
        self.assertIn('product_env.pop("BROWSERSTACK_USERNAME", None)', helper_text)
        self.assertIn('product_env.pop("BROWSERSTACK_ACCESS_KEY", None)', helper_text)
        self.assertIn('"CI_ANDROID_PHYSICAL_PROVIDER": PROVIDER', helper_text)

    def test_dispatch_remains_android_only_and_zero_input(self) -> None:
        request_steps = self.dispatch["jobs"]["request"]["steps"]
        admission = next(
            step
            for step in request_steps
            if step.get("name") == "Validate native targeted test request"
        )["run"]
        self.assertIn('if profile == "physical-performance":', admission)
        self.assertIn('workflow != "validation.android"', admission)
        self.assertIn("Android physical-performance accepts no semantic inputs", admission)


if __name__ == "__main__":
    unittest.main()
