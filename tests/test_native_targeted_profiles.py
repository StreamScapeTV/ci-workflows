from pathlib import Path
import unittest
import yaml

ROOT = Path(__file__).resolve().parents[1]


class NativeTargetedProfileTests(unittest.TestCase):
    def test_dispatch_passes_bounded_selector_inputs(self) -> None:
        text = (ROOT / ".github/workflows/central-ci-dispatch.yml").read_text(encoding="utf-8")
        self.assertIn("test_selectors: ${{ fromJSON(needs.request.outputs.inputs_json).test_selectors || '[]' }}", text)
        self.assertIn("test_platform: ${{ fromJSON(needs.request.outputs.inputs_json).test_platform || '' }}", text)
        self.assertIn("test_filter: ${{ fromJSON(needs.request.outputs.inputs_json).test_filter || '' }}", text)
        workflow = yaml.safe_load(text)
        preflight = next(
            step for step in workflow["jobs"]["request"]["steps"]
            if step.get("name") == "Validate native targeted test request"
        )["run"]
        self.assertIn("1 through 20 selectors", preflight)
        self.assertIn("len(selector_payload) > 2048", preflight)
        self.assertIn("test_platform=jvm or instrumentation", preflight)
        self.assertIn("test_platform=macos, ios, or tvos", preflight)
        self.assertIn("legacy targeted-unit", preflight)

    def test_android_targeted_profile_is_multi_selector_and_fixed_task(self) -> None:
        workflow = yaml.safe_load((ROOT / ".github/workflows/android.yml").read_text(encoding="utf-8"))
        inputs = workflow["on"]["workflow_call"]["inputs"]
        self.assertIn("test_selectors", inputs)
        self.assertEqual(inputs["test_platform"]["default"], "")
        self.assertEqual(inputs["test_filter"]["default"], "")
        command = next(
            step for step in workflow["jobs"]["ci"]["steps"]
            if step.get("name") == "Run fixed Android profile"
        )["run"]
        self.assertIn("targeted-tests)", command)
        self.assertIn("1 through 20 test selectors", command)
        self.assertIn("targeted-tests requires test_platform=jvm or instrumentation", command)
        self.assertIn("targeted_args=(testDebugUnitTest)", command)
        self.assertIn('targeted_args+=(--tests "${selector}")', command)
        self.assertIn('run_gradle "${targeted_args[@]}"', command)
        self.assertIn("connectedDebugAndroidTest", command)
        self.assertIn("android.testInstrumentationRunnerArguments.class=", command)
        self.assertIn("targeted-unit)", command)
        self.assertIn("Transitional compatibility for active Agent State callers", command)

    def test_apple_targeted_profile_uses_fixed_lanes_and_no_product_test_name(self) -> None:
        text = (ROOT / ".github/workflows/apple.yml").read_text(encoding="utf-8")
        workflow = yaml.safe_load(text)
        inputs = workflow["on"]["workflow_call"]["inputs"]
        self.assertEqual(inputs["test_selectors"]["default"], "[]")
        self.assertEqual(inputs["test_platform"]["default"], "")
        plan = next(
            step for step in workflow["jobs"]["plan"]["steps"]
            if step.get("name") == "Resolve fixed Apple execution lanes"
        )["run"]
        for lane in ("macos-targeted-tests", "ios-targeted-tests", "tvos-targeted-tests"):
            self.assertIn(lane, plan)
        self.assertIn("test_platform=macos, ios, or tvos", plan)
        command = next(
            step for step in workflow["jobs"]["execute"]["steps"]
            if step.get("name") == "Run fixed Apple lane"
        )["run"]
        self.assertIn('targeted_test_args+=("-only-testing:${selector}")', command)
        self.assertIn("-destination 'platform=macOS'", command)
        self.assertIn("-destination 'platform=iOS Simulator,name=iPhone 17'", command)
        self.assertIn("-destination 'platform=tvOS Simulator,name=Apple TV'", command)
        self.assertNotIn("SelectedBackendStateSyncRoutingIntegrationTests", text)

    def test_targeted_profiles_fail_closed_on_selector_shape(self) -> None:
        android = (ROOT / ".github/workflows/android.yml").read_text(encoding="utf-8")
        apple = (ROOT / ".github/workflows/apple.yml").read_text(encoding="utf-8")
        for text in (android, apple):
            self.assertIn("1 through 20 test selectors", text)
            self.assertIn("must be unique", text)
            self.assertIn("startswith", text)
        self.assertIn(r"[A-Za-z0-9_.$*?-]{1,240}", android)
        self.assertIn(r"[A-Za-z0-9_.$/-]{1,240}", apple)


if __name__ == "__main__":
    unittest.main()
