from pathlib import Path
import unittest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/python.yml"


class PythonWorkflowTests(unittest.TestCase):
    def _workflow(self) -> tuple[dict, list[dict], dict[str, dict]]:
        workflow = yaml.safe_load(WORKFLOW.read_text())
        steps = workflow["jobs"]["ci"]["steps"]
        by_name = {step.get("name"): step for step in steps if step.get("name")}
        return workflow, steps, by_name

    def test_runtime_version_is_source_owned_and_bounded(self) -> None:
        workflow, steps, by_name = self._workflow()
        inputs = workflow["on"]["workflow_call"]["inputs"]
        self.assertEqual(
            set(inputs),
            {"repository", "ref", "test_profile", "ci_run_id", "upload_private_log"},
        )
        self.assertNotIn("python_version", inputs)

        resolver = by_name["Resolve source Python version"]["run"]
        self.assertIn("version='3.x'", resolver)
        self.assertIn("if test -f .python-version", resolver)
        self.assertIn("wc -l < .python-version", resolver)
        self.assertIn("tr -d '\\r\\n' < .python-version", resolver)
        self.assertIn(r"^3\.[0-9]{1,2}\.[0-9]{1,2}$", resolver)
        self.assertIn("must contain exactly one bounded CPython 3.x.y version", resolver)
        self.assertIn("GITHUB_OUTPUT", resolver)

        setup = by_name["Set up source Python"]
        self.assertEqual(setup["uses"], "actions/setup-python@v6")
        self.assertEqual(setup["with"]["python-version"], "${{ steps.python_version.outputs.version }}")

        names = [step.get("name") for step in steps]
        self.assertLess(names.index("Check out source"), names.index("Resolve source Python version"))
        self.assertLess(names.index("Resolve source Python version"), names.index("Set up source Python"))
        self.assertLess(names.index("Set up source Python"), names.index("Run fixed Python profile"))

    def test_python_executor_keeps_generic_profiles_and_one_bounded_privileged_profile(self) -> None:
        source = WORKFLOW.read_text()
        self.assertNotIn('python-version: "3.12"', source)
        self.assertNotIn("inputs.python_version", source)
        self.assertIn("release-gates)", source)
        self.assertIn("bash scripts/run_release_gates.sh", source)
        self.assertIn("agent-state-issue-reconcile)", source)

    def test_agent_state_issue_reconcile_credentials_are_main_only_and_profile_scoped(self) -> None:
        _, _, by_name = self._workflow()
        commands = by_name["Run fixed Python profile"]
        env = commands["env"]
        gate = (
            "inputs.test_profile == 'agent-state-issue-reconcile' && "
            "inputs.repository == 'StreamScapeTV/agent-state-supabase' && "
            "inputs.ref == 'main'"
        )
        self.assertEqual(env["SOURCE_REPOSITORY"], "${{ inputs.repository || github.repository }}")
        self.assertEqual(env["SOURCE_REF"], "${{ inputs.ref || github.ref_name }}")
        self.assertEqual(env["GITHUB_TOKEN"], "${{ " + gate + " && steps.source.outputs.token || '' }}")
        self.assertEqual(
            env["SUPABASE_URL"],
            "${{ " + gate + " && secrets.AGENT_STATE_SUPABASE_URL || '' }}",
        )
        self.assertEqual(
            env["SUPABASE_SERVICE_ROLE_KEY"],
            "${{ " + gate + " && secrets.AGENT_STATE_SUPABASE_SECRET_KEY || '' }}",
        )

        run = commands["run"]
        self.assertIn('test "${SOURCE_REPOSITORY}" = "StreamScapeTV/agent-state-supabase"', run)
        self.assertIn('test "${SOURCE_REF}" = "main"', run)
        self.assertIn("scripts/reconcile_github_issue_inventory.py --owner StreamScapeTV", run)
        self.assertIn("--owner StreamScapeTV --apply", run)
        self.assertIn('("missing", "extra", "github_navigation_mismatches")', run)
        self.assertIn("post-apply GitHub/Agent State equality failed", run)


if __name__ == "__main__":
    unittest.main()
