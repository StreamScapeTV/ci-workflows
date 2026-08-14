"""Regression coverage for Android standard-wrapper runtime probing."""
from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from ci_workflows import android_contract, android_execution
from ci_workflows.android_types import AndroidValidationError

ROOT = Path(__file__).resolve().parents[1]
CASES = json.loads(
    (ROOT / "tests/fixtures/android-validation/cases.json").read_text(
        encoding="utf-8"
    )
)
CONTRACT = android_contract.load_android_contract(ROOT)


def standard_plan():
    environment = dict(CASES["positive"][1]["environment"])
    environment["INPUT_VALIDATION_PROFILE"] = "compile"
    environment["INPUT_TASK_PROFILE"] = "app-compile"
    environment.pop("INPUT_TARGETED_TEST_SELECTOR", None)
    request = android_contract.request_from_environment(environment, CONTRACT)
    return android_contract.resolve_validation_plan(CONTRACT, request)


def prepare_standard_wrapper(root: Path):
    plan = standard_plan()
    launcher = root / "gradlew"
    properties = root / "gradle/wrapper/gradle-wrapper.properties"
    jar = root / "gradle/wrapper/gradle-wrapper.jar"
    properties.parent.mkdir(parents=True)
    launcher.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    launcher.chmod(0o700)
    properties.write_text(
        "distributionUrl=https\\://services.gradle.org/distributions/"
        "gradle-9.5.0-bin.zip\n",
        encoding="utf-8",
    )
    jar.write_bytes(b"reviewed-standard-wrapper-jar")
    wrapper = replace(
        plan.wrapper,
        launcher_blob_sha1=android_contract.git_blob_sha1(launcher),
        properties_blob_sha1=android_contract.git_blob_sha1(properties),
        jar_blob_sha1=android_contract.git_blob_sha1(jar),
    )
    return replace(plan, wrapper=wrapper), launcher, properties, jar


class AndroidStandardWrapperRuntimeTests(unittest.TestCase):
    def test_standard_wrapper_static_integrity_skips_redundant_version_process(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "source"
            state = Path(directory) / "state"
            root.mkdir()
            state.mkdir()
            plan, _, _, _ = prepare_standard_wrapper(root)
            with mock.patch.object(android_execution, "run_command") as command:
                self.assertEqual(
                    android_execution.verify_wrapper(
                        root,
                        state,
                        plan,
                        {"PATH": "/usr/bin:/bin"},
                    ),
                    "9.5.0",
                )
            command.assert_not_called()

    def test_standard_wrapper_identity_drift_still_fails_before_product_execution(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "source"
            state = Path(directory) / "state"
            root.mkdir()
            state.mkdir()
            plan, _, _, jar = prepare_standard_wrapper(root)
            jar.write_bytes(b"drifted-wrapper-jar")
            with mock.patch.object(android_execution, "run_command") as command:
                with self.assertRaises(AndroidValidationError) as failure:
                    android_execution.verify_wrapper(
                        root,
                        state,
                        plan,
                        {"PATH": "/usr/bin:/bin"},
                    )
            self.assertEqual(failure.exception.code, "wrapper_invalid")
            command.assert_not_called()

    def test_standard_wrapper_next_gradle_process_is_reviewed_product_task(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "source"
            state = Path(directory) / "state"
            root.mkdir()
            state.mkdir()
            plan, _, _, _ = prepare_standard_wrapper(root)
            with mock.patch.object(android_execution, "run_command") as command:
                android_execution.verify_wrapper(
                    root,
                    state,
                    plan,
                    {"PATH": "/usr/bin:/bin"},
                )
            command.assert_not_called()
            argv = android_execution._argv(root, plan, plan.commands[0].argv)
            self.assertNotIn("--version", argv)
            self.assertIn("--no-daemon", argv)
            self.assertEqual(argv[-1], ":app:compileDebugKotlin")

    def test_wrapper_probe_diagnostics_distinguish_runtime_failure_classes(self):
        cases = (
            (
                subprocess.TimeoutExpired(cmd=["gradlew"], timeout=120),
                "wrapper_probe_timeout",
            ),
            (OSError("bounded launch failure"), "wrapper_probe_launch_failed"),
            (
                subprocess.CompletedProcess(["gradlew"], 2, "", "bounded failure"),
                "wrapper_probe_nonzero",
            ),
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for outcome, expected_rule in cases:
                with self.subTest(rule=expected_rule):
                    if isinstance(outcome, subprocess.CompletedProcess):
                        patch = mock.patch("subprocess.run", return_value=outcome)
                    else:
                        patch = mock.patch("subprocess.run", side_effect=outcome)
                    with patch, self.assertRaises(AndroidValidationError) as failure:
                        android_execution.run_command(
                            ["gradlew", "--version"],
                            cwd=root,
                            environment={"PATH": "/usr/bin:/bin"},
                            timeout_seconds=120,
                            failure_code="wrapper_invalid",
                            timeout_rule_id="wrapper_probe_timeout",
                            launch_rule_id="wrapper_probe_launch_failed",
                            nonzero_rule_id="wrapper_probe_nonzero",
                            diagnostic_subject="gradlew",
                        )
                    self.assertEqual(failure.exception.code, "wrapper_invalid")
                    self.assertEqual(
                        failure.exception.diagnostic_values(),
                        {
                            "policy_rule": expected_rule,
                            "policy_subject": "gradlew",
                        },
                    )


if __name__ == "__main__":
    unittest.main()
