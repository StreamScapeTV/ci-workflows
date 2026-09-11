from __future__ import annotations

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
        diagnostic = next(step for step in steps if step.get("name") == "Classify Android terminal diagnostic")
        finish = next(step for step in steps if step.get("name") == "Finish Agent State run")
        script = diagnostic["run"]

        self.assertEqual(diagnostic["id"], "terminal_diagnostic")
        self.assertIn("CHECKOUT_OUTCOME", diagnostic["env"])
        self.assertIn("SOURCE_IDENTITY_OUTCOME", diagnostic["env"])
        self.assertIn("SOURCE_OBSERVE_OUTCOME", diagnostic["env"])
        self.assertIn("COMMANDS_OUTCOME", diagnostic["env"])
        self.assertIn("DRIVE_OUTCOME", diagnostic["env"])
        self.assertIn("source checkout failed before product source identity", script)
        self.assertIn("product validation failed after exact source observation", script)
        self.assertIn('diagnostic_key="${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}.txt"', script)
        self.assertIn('diagnostic_status="available"', script)

        self.assertEqual(finish["with"]["phase"], "finish")
        self.assertEqual(finish["with"]["status"], "${{ steps.terminal_diagnostic.outputs.success == 'true' && 'succeeded' || 'failed' }}")
        self.assertEqual(finish["with"]["error_summary"], "${{ steps.terminal_diagnostic.outputs.error_summary }}")
        self.assertEqual(finish["with"]["diagnostic_key"], "${{ steps.terminal_diagnostic.outputs.diagnostic_key }}")
        self.assertEqual(finish["with"]["diagnostic_status"], "${{ steps.terminal_diagnostic.outputs.diagnostic_status }}")

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
