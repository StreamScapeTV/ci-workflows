from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


class AndroidObservabilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.workflow_path = ROOT / ".github/workflows/android.yml"
        cls.action_path = ROOT / "actions/agent-state/action.yml"
        cls.workflow_text = cls.workflow_path.read_text(encoding="utf-8")
        cls.action_text = cls.action_path.read_text(encoding="utf-8")
        cls.workflow = yaml.safe_load(cls.workflow_text)
        cls.action = yaml.safe_load(cls.action_text)

    def _ordinary_steps(self) -> list[dict]:
        return self.workflow["jobs"]["ci"]["steps"]


    def _run_public_failure_classifier(
        self,
        log_text: str,
        *,
        profile: str,
        platform: str = "",
        room_schema: bool = False,
        commands_outcome: str = "failure",
        android_emulator_outcome: str = "skipped",
    ) -> dict[str, str]:
        steps = self._ordinary_steps()
        classifier = next(
            step
            for step in steps
            if step.get("name") == "Classify public-safe Android product failure"
        )
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            log = root / "central-ci.log"
            output = root / "github-output"
            log.write_text(log_text, encoding="utf-8")
            output.write_text("", encoding="utf-8")
            completed = subprocess.run(
                ["bash", "-c", classifier["run"]],
                cwd=ROOT,
                env={
                    **os.environ,
                    "CI_LOG": str(log),
                    "GITHUB_OUTPUT": str(output),
                    "TEST_PROFILE": profile,
                    "TEST_PLATFORM": platform,
                    "ROOM_SCHEMA": "true" if room_schema else "false",
                    "COMMANDS_OUTCOME": commands_outcome,
                    "ANDROID_EMULATOR_OUTCOME": android_emulator_outcome,
                },
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            values = {}
            for line in output.read_text(encoding="utf-8").splitlines():
                key, value = line.split("=", 1)
                values[key] = value
            return values

    def test_ordinary_android_records_exact_checked_out_product_sha(self) -> None:
        steps = self._ordinary_steps()
        checkout = next(step for step in steps if step.get("name") == "Check out source")
        resolve = next(step for step in steps if step.get("name") == "Resolve observed product source SHA")
        record = next(step for step in steps if step.get("name") == "Record observed product source SHA")

        self.assertEqual(checkout["id"], "checkout")
        self.assertEqual(resolve["id"], "source_identity")
        self.assertIn('source_sha="$(git rev-parse HEAD)"', resolve["run"])
        self.assertNotIn("github.sha", resolve["run"])
        self.assertEqual(record["id"], "source_observe")
        self.assertEqual(record["with"]["phase"], "observe-source")
        self.assertEqual(record["with"]["observed_source_sha"], "${{ steps.source_identity.outputs.source_sha }}")

        names = [step.get("name") for step in steps]
        self.assertLess(names.index("Check out source"), names.index("Resolve observed product source SHA"))
        self.assertLess(names.index("Resolve observed product source SHA"), names.index("Record observed product source SHA"))
        self.assertLess(names.index("Record observed product source SHA"), names.index("Set up Java 25"))

    def test_failed_ordinary_android_exposes_bounded_terminal_diagnostic(self) -> None:
        steps = self._ordinary_steps()
        scrub = next(step for step in steps if step.get("name") == "Scrub configured CI secrets from private log")
        classifier = next(
            step
            for step in steps
            if step.get("name") == "Classify public-safe Android product failure"
        )
        diagnostic = next(step for step in steps if step.get("name") == "Classify Android terminal diagnostic")
        finish = next(step for step in steps if step.get("name") == "Finish Agent State run")
        script = diagnostic["run"]

        self.assertEqual(classifier["id"], "product_failure")
        self.assertIn("steps.scrub.outcome == 'success'", classifier["if"])
        self.assertIn("steps.commands.outcome == 'failure'", classifier["if"])
        self.assertIn("steps.commands.outcome == 'cancelled'", classifier["if"])
        self.assertIn("steps.commands.outcome == 'skipped'", classifier["if"])
        self.assertEqual(
            classifier["env"]["ANDROID_EMULATOR_OUTCOME"],
            "${{ steps.android_emulator.outcome }}",
        )
        self.assertIn('max_scan_bytes = 8 * 1024 * 1024', classifier["run"])
        self.assertNotIn("CI_SECRET_", classifier["env"])

        names = [step.get("name") for step in steps]
        self.assertLess(
            names.index("Scrub configured CI secrets from private log"),
            names.index("Classify public-safe Android product failure"),
        )
        self.assertLess(
            names.index("Classify public-safe Android product failure"),
            names.index("Upload CI log to Google Drive"),
        )

        self.assertEqual(scrub["if"], "${{ always() }}")
        self.assertEqual(diagnostic["id"], "terminal_diagnostic")
        self.assertIn("CHECKOUT_OUTCOME", diagnostic["env"])
        self.assertIn("SOURCE_IDENTITY_OUTCOME", diagnostic["env"])
        self.assertIn("SOURCE_OBSERVE_OUTCOME", diagnostic["env"])
        self.assertIn("COMMANDS_OUTCOME", diagnostic["env"])
        self.assertIn("PRODUCT_FAILURE_OUTCOME", diagnostic["env"])
        self.assertIn("PRODUCT_FAILURE_CLASS", diagnostic["env"])
        self.assertIn("PRODUCT_ERROR_SUMMARY", diagnostic["env"])
        self.assertIn("DRIVE_OUTCOME", diagnostic["env"])
        self.assertIn("source checkout failed before product source identity", script)
        self.assertIn('error_summary="${PRODUCT_ERROR_SUMMARY}"', script)
        self.assertIn('test "${COMMANDS_OUTCOME}" = skipped', script)
        self.assertIn("failure [product_validation]", script)
        self.assertIn('diagnostic_key="${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}.txt"', script)
        self.assertIn('diagnostic_status="available"', script)

        self.assertEqual(finish["with"]["phase"], "finish")
        self.assertEqual(finish["with"]["status"], "${{ steps.terminal_diagnostic.outputs.success == 'true' && 'succeeded' || 'failed' }}")
        self.assertEqual(finish["with"]["error_summary"], "${{ steps.terminal_diagnostic.outputs.error_summary }}")
        self.assertEqual(finish["with"]["diagnostic_key"], "${{ steps.terminal_diagnostic.outputs.diagnostic_key }}")
        self.assertEqual(finish["with"]["diagnostic_status"], "${{ steps.terminal_diagnostic.outputs.diagnostic_status }}")

    def test_targeted_jvm_failure_classification_is_actionable_and_fixed(self) -> None:
        selector = self._run_public_failure_classifier(
            "No tests found for given includes: [com.example.DoesNotExist] secret-value\n",
            profile="targeted-tests",
            platform="jvm",
        )
        self.assertEqual(selector["failure_class"], "test_selector_failure")
        self.assertIn("[test_selector]", selector["error_summary"])
        self.assertIn("correct", selector["error_summary"].lower())
        self.assertNotIn("DoesNotExist", selector["error_summary"])
        self.assertNotIn("secret-value", selector["error_summary"])

        assertion = self._run_public_failure_classifier(
            "There were failing tests. See the report at: private/path\n",
            profile="targeted-tests",
            platform="jvm",
        )
        self.assertEqual(assertion["failure_class"], "assertion_test_failure")
        self.assertIn("[assertion_test]", assertion["error_summary"])
        self.assertNotIn("private/path", assertion["error_summary"])

    def test_full_and_room_failures_distinguish_compile_and_correctness(self) -> None:
        compile_failure = self._run_public_failure_classifier(
            "> Task :app:compileDebugKotlin FAILED\nCompilation error. See private compiler detail\n",
            profile="full",
        )
        self.assertEqual(compile_failure["failure_class"], "compile_failure")
        self.assertIn("[compile]", compile_failure["error_summary"])
        self.assertNotIn("private compiler detail", compile_failure["error_summary"])

        room_failure = self._run_public_failure_classifier(
            "===== room-schema =====\nAndroid room schema validation failed; private row detail\n",
            profile="full",
            room_schema=True,
        )
        self.assertEqual(room_failure["failure_class"], "assertion_test_failure")
        self.assertIn("Room schema correctness", room_failure["error_summary"])
        self.assertNotIn("private row detail", room_failure["error_summary"])

    def test_instrumentation_failure_distinguishes_bootstrap_from_assertion(self) -> None:
        bootstrap = self._run_public_failure_classifier(
            "Android emulator did not boot within the bounded timeout\n",
            profile="targeted-tests",
            platform="instrumentation",
        )
        self.assertEqual(bootstrap["failure_class"], "instrumentation_bootstrap_failure")
        self.assertIn("[instrumentation_bootstrap]", bootstrap["error_summary"])

        assertion = self._run_public_failure_classifier(
            "<testcase name=\"works\"><failure type=\"java.lang.AssertionError\">private</failure></testcase>\n",
            profile="targeted-tests",
            platform="instrumentation",
        )
        self.assertEqual(assertion["failure_class"], "assertion_test_failure")
        self.assertNotIn("private", assertion["error_summary"])

    def test_real_precommand_failure_states_are_publicly_actionable(self) -> None:
        instrumentation_bootstrap = self._run_public_failure_classifier(
            "private emulator setup detail that must not be exposed\n",
            profile="targeted-tests",
            platform="instrumentation",
            commands_outcome="skipped",
            android_emulator_outcome="failure",
        )
        self.assertEqual(
            instrumentation_bootstrap["failure_class"],
            "instrumentation_bootstrap_failure",
        )
        self.assertIn(
            "[instrumentation_bootstrap]",
            instrumentation_bootstrap["error_summary"],
        )
        self.assertNotIn(
            "private emulator setup detail",
            instrumentation_bootstrap["error_summary"],
        )

        infrastructure = self._run_public_failure_classifier(
            "private setup detail that must not be exposed\n",
            profile="full",
            commands_outcome="skipped",
            android_emulator_outcome="skipped",
        )
        self.assertEqual(infrastructure["failure_class"], "infrastructure_failure")
        self.assertIn("[infrastructure]", infrastructure["error_summary"])
        self.assertIn("pre-command", infrastructure["error_summary"])
        self.assertNotIn(
            "private setup detail",
            infrastructure["error_summary"],
        )

    def test_dependency_timeout_and_infrastructure_signatures_are_distinct(self) -> None:
        dependency = self._run_public_failure_classifier(
            "Could not resolve all files for configuration ':app:debugRuntimeClasspath'.\n",
            profile="full",
        )
        self.assertEqual(dependency["failure_class"], "dependency_resolution_failure")
        self.assertIn("[dependency_resolution]", dependency["error_summary"])

        timeout = self._run_public_failure_classifier(
            "java.util.concurrent.TimeoutException: test timed out after 60 seconds\n",
            profile="targeted-tests",
            platform="jvm",
        )
        self.assertEqual(timeout["failure_class"], "timeout")
        self.assertIn("[timeout]", timeout["error_summary"])

        infrastructure = self._run_public_failure_classifier(
            "java.io.IOException: No space left on device\n",
            profile="full",
        )
        self.assertEqual(infrastructure["failure_class"], "infrastructure_failure")
        self.assertIn("[infrastructure]", infrastructure["error_summary"])

    def test_agent_state_finish_supports_existing_diagnostic_fields_without_new_framework(self) -> None:
        inputs = self.action["inputs"]
        for name in ("error_summary", "diagnostic_key", "diagnostic_status"):
            self.assertIn(name, inputs)
            self.assertFalse(inputs[name]["required"])
            self.assertEqual(inputs[name]["default"], "")

        script = self.action["runs"]["steps"][0]["run"]
        self.assertIn("error_summary:$error_summary", script)
        self.assertIn("diagnostic_key:$diagnostic_key", script)
        self.assertIn("diagnostic_status:$diagnostic_status", script)
        self.assertIn("p_patch:$patch", script)
        self.assertNotIn("new diagnostic", script.lower())


if __name__ == "__main__":
    unittest.main()
