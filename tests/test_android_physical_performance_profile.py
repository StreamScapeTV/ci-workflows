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
HELPER = ROOT / "scripts/ci/android_physical_browserstack.py"


def load_helper():
    spec = importlib.util.spec_from_file_location("android_physical_browserstack", HELPER)
    if spec is None or spec.loader is None:
        raise RuntimeError("unable to load BrowserStack helper")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class AndroidPhysicalPerformanceProfileTests(unittest.TestCase):
    def setUp(self) -> None:
        self.android = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
        self.dispatch = yaml.safe_load(DISPATCH.read_text(encoding="utf-8"))
        self.helper = load_helper()

    def test_browserstack_provider_and_representative_classes_are_fixed(self) -> None:
        self.assertEqual(self.helper.PROVIDER, "browserstack")
        self.assertEqual(self.helper.DEVICE_TUNNEL_VERSION, "1.29.0")
        self.assertEqual(
            self.helper.DEVICE_TUNNEL_URL,
            "https://sdk-assets.browserstack.com/binary-linux-x64-1.29.0.zip",
        )
        self.assertEqual(
            self.helper.DEDICATED_DEVICES_URL,
            "https://api-cloud.browserstack.com/app-automate/dedicated_devices",
        )
        self.assertEqual(
            self.helper.DEVICE_SPECS,
            (
                {
                    "deviceClass": "phone",
                    "deviceModel": "Samsung Galaxy S24",
                    "osVersion": "14.0",
                },
                {
                    "deviceClass": "tablet",
                    "deviceModel": "Samsung Galaxy Tab S9",
                    "osVersion": "13.0",
                },
                {
                    "deviceClass": "television",
                    "deviceModel": "Nvidia Shield TV Pro 2019",
                    "osVersion": "11.0",
                },
            ),
        )
        self.assertEqual(
            self.helper.WRAPPER,
            "scripts/ci/run-android-physical-performance.sh",
        )

    def test_device_selection_requires_one_available_real_exact_match(self) -> None:
        spec = self.helper.DEVICE_SPECS[0]
        selected = self.helper.select_device(
            [
                {
                    "os": "android",
                    "device": spec["deviceModel"],
                    "os_version": spec["osVersion"],
                    "realMobile": True,
                    "state": "available",
                    "udid": "private-device-123",
                }
            ],
            spec,
        )
        self.assertEqual(selected["deviceClass"], "phone")
        self.assertEqual(
            selected["deviceIdentitySha256"],
            hashlib.sha256(b"private-device-123").hexdigest(),
        )
        self.assertNotIn(
            "private-device-123",
            json.dumps({key: value for key, value in selected.items() if key != "udid"}),
        )

        cases = (
            ([], "device_class_unavailable"),
            (
                [
                    {
                        "os": "android",
                        "device": spec["deviceModel"],
                        "os_version": spec["osVersion"],
                        "realMobile": False,
                        "state": "available",
                        "udid": "emulated",
                    }
                ],
                "device_class_unavailable",
            ),
            (
                [
                    {
                        "os": "android",
                        "device": spec["deviceModel"],
                        "os_version": spec["osVersion"],
                        "realMobile": True,
                        "state": "in use",
                        "udid": "busy",
                    }
                ],
                "device_busy_or_offline",
            ),
            (
                [
                    {
                        "os": "android",
                        "device": spec["deviceModel"],
                        "os_version": spec["osVersion"],
                        "realMobile": True,
                        "state": "available",
                        "udid": "one",
                    },
                    {
                        "os": "android",
                        "device": spec["deviceModel"],
                        "os_version": spec["osVersion"],
                        "realMobile": True,
                        "state": "available",
                        "udid": "two",
                    },
                ],
                "device_class_ambiguous",
            ),
        )
        for devices, code in cases:
            with self.subTest(code=code):
                with self.assertRaises(self.helper.ExpectedFailure) as raised:
                    self.helper.select_device(devices, spec)
                self.assertEqual(raised.exception.code, code)

    def test_tunnel_connection_metadata_is_bounded_and_provider_owned(self) -> None:
        value = {
            "deviceId": "private-device",
            "sessionId": "session-123",
            "buildId": "build-456",
            "localPort": "localhost:5038",
        }
        metadata = self.helper._extract_connection_metadata(value, "private-device")
        self.assertEqual(
            metadata,
            {
                "providerSessionId": "session-123",
                "providerBuildId": "build-456",
                "localPort": "5038",
            },
        )
        anonymous = {
            "sessionId": "session-direct",
            "buildId": "build-direct",
            "localPort": "5040",
        }
        self.assertEqual(
            self.helper._extract_connection_metadata(anonymous, "private-device"),
            {
                "providerSessionId": "session-direct",
                "providerBuildId": "build-direct",
                "localPort": "5040",
            },
        )
        with self.assertRaises(self.helper.ExpectedFailure):
            self.helper._extract_connection_metadata(
                {"deviceId": "private-device", "sessionId": "session-123"},
                "private-device",
            )

    def test_tunnel_connection_metadata_never_binds_another_device(self) -> None:
        wrong = {
            "deviceId": "other-device",
            "sessionId": "session-other",
            "buildId": "build-other",
            "localPort": "localhost:5039",
        }
        with self.assertRaises(self.helper.ExpectedFailure) as raised:
            self.helper._extract_connection_metadata(wrong, "selected-device")
        self.assertEqual(raised.exception.code, "provider_transport_failure")

        mixed = [
            {"deviceId": "selected-device", "state": "connecting"},
            wrong,
        ]
        with self.assertRaises(self.helper.ExpectedFailure):
            self.helper._extract_connection_metadata(mixed, "selected-device")

        ambiguous_anonymous = [
            {"sessionId": "one", "buildId": "build-one", "localPort": "5041"},
            {"sessionId": "two", "buildId": "build-two", "localPort": "5042"},
        ]
        with self.assertRaises(self.helper.ExpectedFailure):
            self.helper._extract_connection_metadata(ambiguous_anonymous, "selected-device")

    def test_adb_endpoint_must_match_reviewed_device_model_and_os(self) -> None:
        def result(stdout: str, returncode: int = 0):
            return subprocess.CompletedProcess([], returncode, stdout=stdout, stderr="")

        def adb_run(args, **_kwargs):
            marker = args[-1]
            values = {
                "get-state": "device\n",
                "ro.kernel.qemu": "0\n",
                "ro.product.marketname": "Galaxy S24\n",
                "ro.product.model": "SM-S921B\n",
                "ro.build.version.release": "14\n",
            }
            return result(values[marker])

        with mock.patch.object(self.helper.shutil, "which", return_value="/usr/bin/adb"), mock.patch.object(
            self.helper, "_run", side_effect=adb_run
        ):
            serial = self.helper.wait_for_adb(
                "5038", Path("/tmp/private.log"), "Samsung Galaxy S24", "14.0"
            )
        self.assertEqual(serial, "localhost:5038")

        def wrong_model(args, **kwargs):
            value = adb_run(args, **kwargs)
            if args[-1] in {"ro.product.marketname", "ro.product.model"}:
                return result("Pixel 9\n")
            return value

        def wrong_os(args, **kwargs):
            value = adb_run(args, **kwargs)
            if args[-1] == "ro.build.version.release":
                return result("15\n")
            return value

        for runner in (wrong_model, wrong_os):
            with self.subTest(runner=runner.__name__), mock.patch.object(
                self.helper.shutil, "which", return_value="/usr/bin/adb"
            ), mock.patch.object(self.helper, "_run", side_effect=runner):
                with self.assertRaises(self.helper.ExpectedFailure) as raised:
                    self.helper.wait_for_adb(
                        "5038", Path("/tmp/private.log"), "Samsung Galaxy S24", "14.0"
                    )
                self.assertEqual(raised.exception.code, "provider_transport_failure")

    def _valid_product_result(self, root: Path) -> tuple[Path, Path, dict[str, str]]:
        evidence = root / "evidence"
        evidence.mkdir()
        artifact = evidence / "artifacts" / "app-release.apk"
        artifact.parent.mkdir()
        artifact.write_bytes(b"immutable-release-apk")
        expected = {
            "sourceSha": "a" * 40,
            "deviceClass": "phone",
            "deviceModel": "Samsung Galaxy S24",
            "osVersion": "14.0",
            "provider": "browserstack",
            "providerBuildId": "build-123",
            "providerSessionId": "session-123",
        }
        result = {
            "schemaVersion": 1,
            **expected,
            "androidApiLevel": 34,
            "buildMode": "release",
            "artifactFile": "artifacts/app-release.apk",
            "artifactSha256": hashlib.sha256(b"immutable-release-apk").hexdigest(),
            "status": "passed",
            "measurements": [
                {
                    "id": "startup.cold.p95_ms",
                    "value": 950,
                    "unit": "ms",
                    "status": "passed",
                }
            ],
            "scenarios": [{"id": "startup-cold", "status": "passed"}],
        }
        result_path = root / "result.json"
        result_path.write_text(json.dumps(result), encoding="utf-8")
        return result_path, evidence, expected

    def test_product_result_proves_exact_release_artifact_and_measurements(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            result_path, evidence, expected = self._valid_product_result(Path(td))
            value = self.helper.validate_product_result(result_path, evidence, expected)
            self.assertEqual(
                value["artifactSha256"],
                hashlib.sha256(b"immutable-release-apk").hexdigest(),
            )
            self.assertFalse(value["measurementFailed"])

            raw = json.loads(result_path.read_text(encoding="utf-8"))
            raw["artifactSha256"] = "0" * 64
            result_path.write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaises(self.helper.ExpectedFailure) as raised:
                self.helper.validate_product_result(result_path, evidence, expected)
            self.assertEqual(raised.exception.code, "product_artifact_mismatch")

    def test_product_result_rejects_extra_secret_or_failed_measurement(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            result_path, evidence, expected = self._valid_product_result(Path(td))
            raw = json.loads(result_path.read_text(encoding="utf-8"))
            raw["credential"] = "must-not-survive"
            result_path.write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaises(self.helper.ExpectedFailure) as raised:
                self.helper.validate_product_result(result_path, evidence, expected)
            self.assertEqual(raised.exception.code, "product_result_invalid")

        with tempfile.TemporaryDirectory() as td:
            result_path, evidence, expected = self._valid_product_result(Path(td))
            raw = json.loads(result_path.read_text(encoding="utf-8"))
            raw["measurements"][0]["status"] = "failed"
            result_path.write_text(json.dumps(raw), encoding="utf-8")
            value = self.helper.validate_product_result(result_path, evidence, expected)
            self.assertTrue(value["measurementFailed"])

    def test_workflow_uses_hosted_linux_and_fixed_browserstack_secrets(self) -> None:
        workflow_call = self.android["on"]["workflow_call"]
        secrets = workflow_call["secrets"]
        self.assertIn("BROWSERSTACK_USERNAME", secrets)
        self.assertIn("BROWSERSTACK_ACCESS_KEY", secrets)
        self.assertNotIn("ANDROID_PHYSICAL_PERFORMANCE_AUTHORIZATION", secrets)

        job = self.android["jobs"]["physical_performance"]
        self.assertEqual(job["runs-on"], "ubuntu-24.04")
        self.assertNotIn("strategy", job)
        self.assertNotIn("matrix", json.dumps(job))
        serialized = json.dumps(job)
        for invented in (
            "android-physical-phone",
            "android-physical-tablet",
            "android-physical-tv",
        ):
            self.assertNotIn(invented, serialized)
        helper_checkout = next(
            step
            for step in job["steps"]
            if step.get("name") == "Check out Central physical-performance helpers"
        )
        self.assertEqual(
            helper_checkout["with"]["repository"],
            "StreamScapeTV/ci-workflows",
        )
        self.assertEqual(helper_checkout["with"]["ref"], "main")
        self.assertEqual(
            helper_checkout["with"]["path"],
            "central-physical-performance",
        )
        run = next(
            step
            for step in job["steps"]
            if step.get("name") == "Run bounded BrowserStack physical-performance cohort"
        )
        self.assertIn(
            "central-physical-performance/scripts/ci/android_physical_browserstack.py",
            run["run"],
        )
        self.assertEqual(
            run["env"]["BROWSERSTACK_USERNAME"],
            "${{ secrets.BROWSERSTACK_USERNAME }}",
        )
        self.assertEqual(
            run["env"]["BROWSERSTACK_ACCESS_KEY"],
            "${{ secrets.BROWSERSTACK_ACCESS_KEY }}",
        )
        self.assertIn("Upload Android physical-performance private log", serialized)
        self.assertIn("Upload bounded Android physical-performance evidence", serialized)
        self.assertIn("Finish Agent State run", serialized)

    def test_provider_credentials_never_reach_product_wrapper_environment(self) -> None:
        helper_text = HELPER.read_text(encoding="utf-8")
        self.assertIn('product_env.pop("BROWSERSTACK_USERNAME", None)', helper_text)
        self.assertIn('product_env.pop("BROWSERSTACK_ACCESS_KEY", None)', helper_text)
        self.assertIn('"CI_ANDROID_PHYSICAL_PROVIDER": PROVIDER', helper_text)
        self.assertIn(
            '"CI_ANDROID_PHYSICAL_PROVIDER_SESSION_ID": provider_meta["providerSessionId"]',
            helper_text,
        )
        self.assertNotIn("ANDROID_PHYSICAL_PERFORMANCE_AUTHORIZATION", helper_text)

    def test_dispatch_is_android_only_zero_input_and_not_cancelled(self) -> None:
        request_steps = self.dispatch["jobs"]["request"]["steps"]
        admission = next(
            step
            for step in request_steps
            if step.get("name") == "Validate native targeted test request"
        )["run"]
        self.assertIn('if profile == "physical-performance":', admission)
        self.assertIn('workflow != "validation.android"', admission)
        self.assertRegex(admission, r'repository != "[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+"')
        self.assertIn("Android physical-performance accepts no semantic inputs", admission)
        android = self.dispatch["jobs"]["android"]
        self.assertEqual(
            android["concurrency"]["cancel-in-progress"],
            "${{ needs.request.outputs.test_profile != 'physical-performance' }}",
        )

    def test_no_caller_provider_device_runner_command_or_timeout_surface(self) -> None:
        inputs = set(self.android["on"]["workflow_call"]["inputs"])
        for forbidden in (
            "provider",
            "device",
            "device_model",
            "runner",
            "runner_label",
            "command",
            "environment",
            "timeout",
            "browserstack_username",
            "browserstack_access_key",
        ):
            self.assertNotIn(forbidden, inputs)
        helper_text = HELPER.read_text(encoding="utf-8")
        for marker in (
            "Samsung Galaxy S24",
            "Samsung Galaxy Tab S9",
            "Nvidia Shield TV Pro 2019",
            'DEVICE_TUNNEL_VERSION = "1.29.0"',
            "scripts/ci/run-android-physical-performance.sh",
        ):
            self.assertIn(marker, helper_text)


if __name__ == "__main__":
    unittest.main()
