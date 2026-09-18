from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]
ANDROID = ROOT / ".github" / "workflows" / "android.yml"
DISPATCH = ROOT / ".github" / "workflows" / "central-ci-dispatch.yml"


class AndroidPhysicalPerformanceProfileContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.android_text = ANDROID.read_text(encoding="utf-8")
        cls.android = yaml.safe_load(cls.android_text)
        cls.dispatch = yaml.safe_load(DISPATCH.read_text(encoding="utf-8"))

    def test_profile_is_fixed_and_does_not_reuse_hosted_or_emulator_lane(self) -> None:
        jobs = self.android["jobs"]
        ordinary = jobs["ci"]
        self.assertIn("inputs.test_profile != 'physical-performance'", ordinary["if"])

        plan = jobs["physical_performance_plan"]
        self.assertEqual(plan["runs-on"], "ubuntu-24.04")
        steps = {step.get("name"): step for step in plan["steps"] if step.get("name")}
        validate = steps["Validate fixed Android physical-performance request"]["run"]
        self.assertIn("bounded to StreamScapeTV/iptv-android", validate)
        self.assertIn("does not accept test_filter", validate)
        self.assertIn("does not accept test_selectors", validate)
        self.assertIn("does not accept test_platform", validate)
        self.assertIn("does not accept project_directory", validate)
        self.assertIn("does not accept room_schema", validate)
        self.assertIn("does not accept release_version", validate)
        self.assertIn("does not accept build_number", validate)

        physical = jobs["physical_performance"]
        includes = physical["strategy"]["matrix"]["include"]
        self.assertEqual(
            includes,
            [
                {"device_class": "phone", "runner_label": "android-physical-phone"},
                {"device_class": "tablet", "runner_label": "android-physical-tablet"},
                {"device_class": "television", "runner_label": "android-physical-tv"},
            ],
        )
        self.assertEqual(
            physical["runs-on"],
            ["linux", "x64", "${{ matrix.runner_label }}"],
        )
        self.assertFalse(physical["concurrency"]["cancel-in-progress"])
        self.assertNotIn("ubuntu-latest", json.dumps(physical))
        self.assertNotIn("emulator-5554", json.dumps(physical))
        self.assertNotIn("system-images;", json.dumps(physical))

    def test_operator_authorization_is_central_secret_and_gates_scheduling(self) -> None:
        secrets = self.android["on"]["workflow_call"]["secrets"]
        self.assertIn("ANDROID_PHYSICAL_PERFORMANCE_AUTHORIZATION", secrets)
        plan = self.android["jobs"]["physical_performance_plan"]
        authorization = next(
            step for step in plan["steps"]
            if step.get("name") == "Resolve operator physical-lab authorization"
        )
        self.assertEqual(
            authorization["env"]["AUTHORIZATION"],
            "${{ secrets.ANDROID_PHYSICAL_PERFORMANCE_AUTHORIZATION }}",
        )
        self.assertIn("android-physical-performance-v1", authorization["run"])
        physical_if = self.android["jobs"]["physical_performance"]["if"]
        self.assertIn("lab_authorized == 'true'", physical_if)
        self.assertIn("wrapper_available == 'true'", physical_if)

    def test_product_command_detail_stays_behind_one_fixed_wrapper(self) -> None:
        text = json.dumps(self.android["jobs"]["physical_performance"])
        self.assertIn("scripts/ci/run-android-physical-performance.sh", text)
        self.assertIn("CI_ANDROID_PHYSICAL_PROFILE=performance", text)
        self.assertIn("CI_ANDROID_PHYSICAL_DEVICE_CLASS", text)
        self.assertIn("CI_ANDROID_PHYSICAL_SOURCE_SHA", text)
        self.assertIn("CI_ANDROID_PHYSICAL_ANDROID_API_LEVEL", text)
        self.assertIn("CI_ANDROID_PHYSICAL_EVIDENCE_DIR", text)
        self.assertIn("CI_ANDROID_PHYSICAL_RESULT_FILE", text)
        self.assertIn('export ANDROID_SERIAL=', text)
        self.assertNotIn("inputs.runner", text)
        self.assertNotIn("inputs.serial", text)
        self.assertNotIn("inputs.command", text)
        self.assertNotIn("inputs.script", text)

    def test_source_identity_and_terminal_evidence_are_agent_state_bound(self) -> None:
        plan = self.android["jobs"]["physical_performance_plan"]
        plan_names = [step.get("name") for step in plan["steps"]]
        self.assertIn("Mark Agent State run as running", plan_names)
        self.assertIn("Record Android physical-performance source SHA", plan_names)
        source_identity = next(
            step for step in plan["steps"]
            if step.get("name") == "Record Android physical-performance source identity"
        )["run"]
        self.assertIn("git -C", source_identity)
        self.assertIn("rev-parse HEAD", source_identity)
        self.assertIn("wrapper_available", source_identity)

        finish = self.android["jobs"]["physical_performance_finish"]
        summary = next(
            step for step in finish["steps"]
            if step.get("name") == "Build Android physical-performance terminal summary"
        )["run"]
        self.assertIn("operator_lab_authorization_missing", summary)
        self.assertIn("product_wrapper_missing", summary)
        self.assertIn("device_or_product_failure", summary)
        self.assertIn("android-physical-phone", summary)
        self.assertIn("android-physical-tablet", summary)
        self.assertIn("android-physical-tv", summary)
        terminal = next(
            step for step in finish["steps"]
            if step.get("name") == "Finish Agent State physical-performance run"
        )
        self.assertIn("physical-performance.json", terminal["with"]["diagnostic_key"])
        self.assertIn("did not produce a complete phone/tablet/TV PASS", terminal["with"]["error_summary"])

    def test_dispatch_admission_accepts_no_semantic_inputs(self) -> None:
        step = next(
            step for step in self.dispatch["jobs"]["request"]["steps"]
            if step.get("name") == "Validate native targeted test request"
        )
        script = step["run"]
        self.assertIn('profile == "physical-performance"', script)
        self.assertIn("Android physical-performance accepts no semantic inputs", script)
        valid = subprocess.run(
            ["bash", "-c", script],
            env={
                **os.environ,
                "WORKFLOW_KEY": "validation.android",
                "TEST_PROFILE": "physical-performance",
                "REPOSITORY": "StreamScapeTV/iptv-android",
                "INPUTS_JSON": "{}",
            },
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(valid.returncode, 0, valid.stderr)
        invalid = subprocess.run(
            ["bash", "-c", script],
            env={
                **os.environ,
                "WORKFLOW_KEY": "validation.android",
                "TEST_PROFILE": "physical-performance",
                "REPOSITORY": "StreamScapeTV/iptv-android",
                "INPUTS_JSON": json.dumps({"test_platform": "phone"}),
            },
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertNotEqual(invalid.returncode, 0)
        self.assertIn("accepts no semantic inputs", invalid.stderr)

    def _discovery_script(self) -> str:
        steps = self.android["jobs"]["physical_performance"]["steps"]
        return next(
            step for step in steps
            if step.get("name") == "Discover one representative physical Android device"
        )["run"]

    def _run_discovery(self, device_class: str, fixture: dict[str, dict[str, str]]) -> tuple[subprocess.CompletedProcess[str], dict[str, str]]:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            bin_dir = root / "bin"
            bin_dir.mkdir()
            fake = bin_dir / "adb"
            fake.write_text(
                """#!/usr/bin/env python3
import json, os, sys
fixture=json.loads(os.environ['ADB_FIXTURE'])
args=sys.argv[1:]
if args==['version']:
    print('Android Debug Bridge version 1.0.41'); raise SystemExit(0)
if args==['devices']:
    print('List of devices attached')
    for serial, values in fixture.items():
        print(serial+'\\t'+values.get('state','device'))
    raise SystemExit(0)
if len(args)>=4 and args[0]=='-s':
    serial=args[1]; cmd=args[2:]; values=fixture[serial]
    if cmd[:3]==['shell','getprop','ro.kernel.qemu']:
        print(values.get('qemu','0'))
    elif cmd[:3]==['shell','getprop','ro.build.version.sdk']:
        print(values.get('api','37'))
    elif cmd[:3]==['shell','getprop','ro.build.characteristics']:
        print(values.get('characteristics','default'))
    elif cmd[:3]==['shell','getprop','ro.product.model']:
        print(values.get('model','Fixture Device'))
    elif cmd==['shell','wm','size']:
        print(values.get('size','Physical size: 1080x2400'))
    elif cmd==['shell','wm','density']:
        print(values.get('density','Physical density: 420'))
    else:
        raise SystemExit(3)
    raise SystemExit(0)
raise SystemExit(2)
""",
                encoding="utf-8",
            )
            fake.chmod(0o755)
            output = root / "output"
            output.write_text("", encoding="utf-8")
            env = {
                **os.environ,
                "EXPECTED_CLASS": device_class,
                "GITHUB_OUTPUT": str(output),
                "RUNNER_TEMP": str(root),
                "ADB_FIXTURE": json.dumps(fixture),
                "PATH": f"{bin_dir}:{os.environ['PATH']}",
            }
            result = subprocess.run(
                ["bash", "-c", self._discovery_script()],
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )
            values = dict(
                line.split("=", 1)
                for line in output.read_text(encoding="utf-8").splitlines()
                if "=" in line
            )
            return result, values

    def test_discovery_classifies_phone_tablet_and_tv_without_exposing_serial(self) -> None:
        fixtures = {
            "phone": {"p-one": {"size": "Physical size: 1080x2400", "density": "Physical density: 420", "model": "Pixel Fixture"}},
            "tablet": {"t-one": {"size": "Physical size: 1600x2560", "density": "Physical density: 320", "model": "Tablet Fixture"}},
            "television": {"tv-one": {"characteristics": "tv", "size": "Physical size: 3840x2160", "density": "Physical density: 320", "model": "TV Fixture"}},
        }
        for device_class, fixture in fixtures.items():
            with self.subTest(device_class=device_class):
                result, values = self._run_discovery(device_class, fixture)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(values["failure_code"], "")
                self.assertRegex(values["device_identity_sha256"], r"^[0-9a-f]{64}$")
                self.assertNotIn(next(iter(fixture)), values.values())

    def test_discovery_rejects_emulator_and_ambiguous_class(self) -> None:
        emulator, emulator_values = self._run_discovery(
            "phone",
            {"emulator-5554": {"qemu": "1", "model": "Emulator"}},
        )
        self.assertNotEqual(emulator.returncode, 0)
        self.assertEqual(emulator_values["failure_code"], "device_class_unavailable")

        ambiguous, ambiguous_values = self._run_discovery(
            "phone",
            {
                "phone-one": {"model": "Phone One"},
                "phone-two": {"model": "Phone Two"},
            },
        )
        self.assertNotEqual(ambiguous.returncode, 0)
        self.assertEqual(ambiguous_values["failure_code"], "device_class_ambiguous")


    def _evidence_script(self) -> str:
        steps = self.android["jobs"]["physical_performance"]["steps"]
        return next(
            step for step in steps
            if step.get("name") == "Build bounded physical-performance evidence"
        )["run"]

    def _run_evidence(self, *, artifact_bytes: bytes = b"apk-bytes", artifact_sha: str | None = None, extra_product: dict | None = None):
        import hashlib
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            device = {
                "serial": "private-serial",
                "deviceClass": "phone",
                "androidApiLevel": 37,
                "deviceModel": "Fixture Phone",
                "deviceIdentitySha256": "a" * 64,
            }
            (root / "android-physical-device.json").write_text(
                json.dumps(device), encoding="utf-8"
            )
            artifact_root = root / "android-physical-product-evidence" / "artifacts"
            artifact_root.mkdir(parents=True)
            artifact = artifact_root / "app-release.apk"
            artifact.write_bytes(artifact_bytes)
            product = {
                "schemaVersion": 1,
                "sourceSha": "b" * 40,
                "deviceClass": "phone",
                "androidApiLevel": 37,
                "buildMode": "release",
                "artifactFile": "artifacts/app-release.apk",
                "artifactSha256": artifact_sha or hashlib.sha256(artifact_bytes).hexdigest(),
                "status": "passed",
                "measurements": [
                    {"id": "startup.cold.p95_ms", "value": 1200, "unit": "ms", "status": "passed"}
                ],
                "scenarios": [
                    {"id": "startup-cold", "status": "passed"}
                ],
            }
            if extra_product:
                product.update(extra_product)
            (root / "android-physical-product-result.json").write_text(
                json.dumps(product), encoding="utf-8"
            )
            output = root / "github-output"
            output.write_text("", encoding="utf-8")
            env = {
                **os.environ,
                "DEVICE_CLASS": "phone",
                "SOURCE_SHA": "b" * 40,
                "DISCOVER_OUTCOME": "success",
                "DISCOVER_FAILURE": "",
                "PRODUCT_EXIT_CODE": "0",
                "RUNNER_TEMP": str(root),
                "GITHUB_OUTPUT": str(output),
            }
            result = subprocess.run(
                ["bash", "-c", self._evidence_script()],
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )
            values = dict(
                line.split("=", 1)
                for line in output.read_text(encoding="utf-8").splitlines()
                if "=" in line
            )
            evidence = json.loads((root / "central-android-physical-phone.json").read_text(encoding="utf-8"))
            return result, values, evidence

    def test_evidence_executes_and_proves_exact_artifact_bytes(self) -> None:
        result, values, evidence = self._run_evidence()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(values["success"], "true")
        self.assertEqual(values["failure_code"], "")
        self.assertEqual(evidence["status"], "passed")
        self.assertEqual(evidence["productResult"]["artifactFile"], "artifacts/app-release.apk")

        mismatch, mismatch_values, mismatch_evidence = self._run_evidence(artifact_sha="0" * 64)
        self.assertEqual(mismatch.returncode, 0, mismatch.stderr)
        self.assertEqual(mismatch_values["success"], "false")
        self.assertEqual(mismatch_values["failure_code"], "product_artifact_mismatch")
        self.assertIsNone(mismatch_evidence["productResult"])

    def test_invalid_product_result_is_not_copied_into_private_evidence(self) -> None:
        result, values, evidence = self._run_evidence(extra_product={"credential": "must-not-survive"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(values["success"], "false")
        self.assertEqual(values["failure_code"], "product_result_invalid")
        self.assertIsNone(evidence["productResult"])
        self.assertNotIn("must-not-survive", json.dumps(evidence))
    def test_product_result_schema_is_bounded_and_secret_free(self) -> None:
        steps = self.android["jobs"]["physical_performance"]["steps"]
        evidence = next(
            step for step in steps if step.get("name") == "Build bounded physical-performance evidence"
        )["run"]
        for marker in (
            'expected_keys = {"schemaVersion", "sourceSha", "deviceClass", "androidApiLevel", "buildMode", "artifactFile", "artifactSha256", "status", "measurements", "scenarios"}',
            'product_artifact_mismatch',
            'for chunk in iter(lambda: stream.read(1024 * 1024), b""):',
            '1 <= len(measurements) <= 128',
            '1 <= len(scenarios) <= 64',
            '"ms", "bytes", "bytes_delta", "count", "fraction", "percent"',
            '"passed", "failed", "observed"',
            'product_gate_failed',
        ):
            self.assertIn(marker, evidence)
        self.assertNotIn("url", evidence.lower())
        self.assertNotIn("token", evidence.lower())
        self.assertNotIn("password", evidence.lower())

    def test_dispatch_does_not_cancel_physical_run_for_newer_request(self) -> None:
        android = self.dispatch["jobs"]["android"]
        self.assertEqual(
            android["concurrency"]["cancel-in-progress"],
            "${{ needs.request.outputs.test_profile != 'physical-performance' }}",
        )


if __name__ == "__main__":
    unittest.main()
