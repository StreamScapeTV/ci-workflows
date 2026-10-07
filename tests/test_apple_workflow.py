from pathlib import Path
import unittest
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

    def test_validation_surface_keeps_only_candidate_and_screenshot(self) -> None:
        inputs = self.workflow["on"]["workflow_call"]["inputs"]
        self.assertNotIn("test_platform", inputs)
        self.assertNotIn("native_component", inputs)
        plan = self.plan_steps["Resolve fixed Apple execution lanes"]["run"]
        for kept in ("candidate)", "screenshot-review)", "testflight)"):
            self.assertIn(kept, plan)
        for retired in (
            "targeted-tests", "native-component", "release-build", "swift-package",
            "simulator)", "macos-test", "ios-targeted", "tvos-targeted",
        ):
            self.assertNotIn(retired, self.source)

    def test_candidate_is_fixed_platform_builds_plus_macos_selected_tests(self) -> None:
        plan = self.plan_steps["Resolve fixed Apple execution lanes"]["run"]
        self.assertIn('"lane":"ios-build"', plan)
        self.assertIn('"lane":"tvos-build"', plan)
        self.assertIn('"lane":"macos-candidate-tests"', plan)
        self.assertIn("candidate requires 1 through 20 unique string selectors", plan)
        commands = self.execute_steps["Run fixed Apple lane"]["run"]
        self.assertIn("macos-candidate-tests)", commands)
        self.assertIn("-only-testing:${selector}", commands)
        self.assertIn("ios-build)", commands)
        self.assertIn("tvos-build)", commands)
        self.assertEqual(self.workflow["jobs"]["execute"]["runs-on"], "macos-latest")

    def test_screenshot_review_keeps_fixed_two_lane_evidence_path(self) -> None:
        plan = self.plan_steps["Resolve fixed Apple execution lanes"]["run"]
        self.assertIn('"lane":"screenshot-ios"', plan)
        self.assertIn('"lane":"screenshot-tvos"', plan)
        commands = self.execute_steps["Run fixed Apple lane"]
        self.assertIn("run-apple-screenshot-review.sh", commands["run"])
        for name in (
            "STREAMSCAPE_API_BASE", "STREAMSCAPE_EMAIL", "STREAMSCAPE_PASSWORD",
            "STREAMSCAPE_DEMO_EMAIL", "STREAMSCAPE_DEMO_PASSWORD",
            "XTREAM_URL", "XTREAM_USERNAME", "XTREAM_PASSWORD",
        ):
            self.assertIn("screenshot-review", commands["env"][name])
        self.assertIn("Upload screenshot-review candidate evidence by route", self.execute_steps)

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

    def test_agent_state_lifecycle_has_one_start_and_one_finish(self) -> None:
        names = [s.get("name") for s in self.workflow["jobs"]["plan"]["steps"]]
        self.assertIn("Mark Agent State run as running", names)
        finish = self.workflow["jobs"]["finish"]
        self.assertIn("inputs.ci_run_id != ''", finish["if"])
        finalizers = [s for s in finish["steps"] if isinstance(s, dict) and s.get("with", {}).get("phase") == "finish"]
        self.assertEqual(len(finalizers), 1)

    def test_dispatch_apple_validation_is_keep_list_only(self) -> None:
        request = {s.get("name"): s for s in self.dispatch["jobs"]["request"]["steps"] if s.get("name")}
        keep = request["Validate retained legacy validation profile"]["run"]
        self.assertIn("validation.apple/candidate", keep)
        self.assertIn("validation.apple/screenshot-review", keep)
        self.assertIn("retired Apple validation profile", keep)
        job = self.dispatch["jobs"]["apple"]
        self.assertEqual(set(job["with"]), {"repository", "ref", "source_is_tag", "test_profile", "test_selectors", "ci_run_id"})


if __name__ == "__main__":
    unittest.main()
