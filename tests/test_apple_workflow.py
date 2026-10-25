from pathlib import Path
import unittest
import os
import subprocess
import sys
import tempfile
import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/apple.yml"
DISPATCH = ROOT / ".github/workflows/central-ci-dispatch.yml"

class AppleWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.workflow = yaml.safe_load(WORKFLOW.read_text())
        cls.dispatch = yaml.safe_load(DISPATCH.read_text())
        cls.source = WORKFLOW.read_text()
        cls.plan_steps = {s.get("name"): s for s in cls.workflow["jobs"]["plan"]["steps"] if s.get("name")}
        cls.execute_steps = {s.get("name"): s for s in cls.workflow["jobs"]["execute"]["steps"] if s.get("name")}

    def test_validation_surface_keeps_only_authorized_release(self) -> None:
        inputs = self.workflow["on"]["workflow_call"]["inputs"]
        self.assertNotIn("test_platform", inputs)
        self.assertNotIn("native_component", inputs)
        plan = self.plan_steps["Resolve fixed Apple execution lanes"]["run"]
        self.assertIn("testflight)", plan)
        self.assertNotIn("screenshot-review", self.source)
        for retired in (
            "candidate)", "targeted-tests", "native-component", "release-build", "swift-package",
            "simulator)", "macos-test", "ios-targeted", "tvos-targeted",
        ):
            self.assertNotIn(retired, self.source)

    def test_retired_candidate_is_rejected_before_execution(self) -> None:
        plan = self.plan_steps["Resolve fixed Apple execution lanes"]["run"]
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "outputs"
            result = subprocess.run(
                ["bash", "-c", plan], capture_output=True, text=True,
                env={**os.environ, "TEST_PROFILE": "candidate", "SOURCE_IS_TAG": "false",
                     "RELEASE_VERSION": "", "BUILD_NUMBER": "", "GITHUB_OUTPUT": str(output)},
            )
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertIn("Unsupported retained Apple test_profile", result.stderr)
            self.assertFalse(output.exists())
        self.assertNotIn("test_selectors", self.workflow["on"]["workflow_call"]["inputs"])
        commands = self.execute_steps["Run fixed Apple lane"]["run"]
        for retired in ("macos-candidate-tests", "ios-build)", "tvos-build)", "candidate_test_args"):
            self.assertNotIn(retired, commands)

    def test_helper_isolation_preserves_real_product_cleanliness(self):
        isolation = self.execute_steps['Isolate Central Apple helpers from product source']['run']
        command = self.execute_steps['Run fixed Apple lane']['run']
        guard = 'test -z "$(git status --porcelain --untracked-files=normal)"'
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            product = root / 'product'; product.mkdir()
            runner = root / 'runner'; runner.mkdir()
            subprocess.run(['git', 'init', '-q', str(product)], check=True)
            (product / 'tracked').write_text('original')
            subprocess.run(['git', 'add', '.'], cwd=product, check=True)
            subprocess.run(['git', '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.test',
                            'commit', '-qm', 'fixture'], cwd=product, check=True)
            helpers = product / 'central-ci'; helpers.mkdir()
            (helpers / 'helper').write_text('central')
            env = {**os.environ, 'GITHUB_WORKSPACE': str(product), 'RUNNER_TEMP': str(runner),
                   'GITHUB_ENV': str(root / 'env')}
            before = subprocess.run(['bash', '-c', guard], cwd=product, env=env, capture_output=True)
            self.assertNotEqual(before.returncode, 0)
            subprocess.run(['bash', '-e', '-c', isolation], cwd=product, env=env, check=True)
            clean = subprocess.run(['bash', '-c', guard], cwd=product, env=env, capture_output=True)
            self.assertEqual(clean.returncode, 0, clean.stderr)
            self.assertTrue(Path((root / 'env').read_text().strip().split('=', 1)[1], 'helper').is_file())
            for kind in ('untracked', 'modified', 'symlink'):
                with self.subTest(kind=kind):
                    dirty = product / ('tracked' if kind == 'modified' else 'dirty')
                    if kind == 'symlink':
                        dirty.symlink_to(runner)
                    else:
                        dirty.write_text('dirty')
                    result = subprocess.run(['bash', '-c', guard], cwd=product, env=env, capture_output=True)
                    self.assertNotEqual(result.returncode, 0)
                    if kind == 'modified':
                        dirty.write_text('original')
                    else:
                        dirty.unlink()

    def test_testflight_remains_separately_authorized_release(self) -> None:
        job = self.dispatch["jobs"]["apple_release"]
        self.assertIn("release.apple", job["if"])
        self.assertIn("testflight", job["if"])
        self.assertEqual(job["with"]["test_profile"], "testflight")
        self.assertFalse(job["concurrency"]["cancel-in-progress"])
        plan = self.plan_steps["Resolve fixed Apple execution lanes"]["run"]
        self.assertIn('"lane":"testflight"', plan)
        run = self.execute_steps["Run fixed Apple lane"]["run"]
        self.assertIn("run-apple-testflight.sh", run)
        self.assertIn("CI_APPLE_TESTFLIGHT_BUILD_NUMBER", run)
        self.assertIn("Clean TestFlight credential and release state", self.execute_steps)
        secrets = self.workflow["on"]["workflow_call"]["secrets"]
        self.assertIn("APPLE_DISTRIBUTION_P12_BASE64", secrets)
        self.assertIn("APPLE_DISTRIBUTION_P12_PASSWORD", secrets)
        setup = self.execute_steps["Prepare ephemeral Apple Distribution signing identity"]
        self.assertIn("inputs.test_profile == 'testflight'", setup["if"])
        self.assertIn("distribution-setup", setup["run"])
        cleanup = self.execute_steps["Restore and delete ephemeral Apple Distribution signing identity"]
        self.assertIn("always()", cleanup["if"])
        self.assertIn("distribution-cleanup", cleanup["run"])

    def test_agent_state_lifecycle_has_one_start_and_one_finish(self) -> None:
        names = [s.get("name") for s in self.workflow["jobs"]["plan"]["steps"]]
        self.assertIn("Mark Agent State run as running", names)
        finish = self.workflow["jobs"]["finish"]
        self.assertIn("inputs.ci_run_id != ''", finish["if"])
        finalizers = [s for s in finish["steps"] if isinstance(s, dict) and s.get("with", {}).get("phase") == "finish"]
        self.assertEqual(len(finalizers), 1)

    def test_retired_apple_workflow_is_rejected_and_release_remains(self) -> None:
        request = {step.get("name"): step for step in self.dispatch["jobs"]["request"]["steps"]}
        rejection = request["Reject retired legacy validation workflow"]
        self.assertIn("validation.apple", rejection["if"])
        result = subprocess.run(["bash", "-c", rejection["run"]], capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertIn("validation.repository", result.stderr)
        self.assertNotIn("apple", self.dispatch["jobs"])
        self.assertNotIn("apple", self.dispatch["jobs"]["settle_cancelled"]["needs"])
        self.assertNotIn("needs.apple.result", self.dispatch["jobs"]["settle_cancelled"]["if"])
        self.assertEqual(self.dispatch["jobs"]["apple_release"]["uses"], "./.github/workflows/apple.yml")

if __name__ == "__main__":
    unittest.main()
