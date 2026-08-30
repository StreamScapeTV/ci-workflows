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

    def test_python_executor_has_no_repository_specific_runtime_branch(self) -> None:
        source = WORKFLOW.read_text()
        self.assertNotIn('python-version: "3.12"', source)
        self.assertNotIn("agent-state-supabase", source)
        self.assertNotIn("inputs.python_version", source)
        self.assertIn("release-gates)", source)
        self.assertIn("bash scripts/run_release_gates.sh", source)


if __name__ == "__main__":
    unittest.main()
