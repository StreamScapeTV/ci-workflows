from __future__ import annotations

import unittest
from pathlib import Path

import yaml

from ci_workflows import android_contract
from ci_workflows.validation_model import ActionsLoader


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_PATH = ROOT / ".github/workflows/reusable-android.yml"
FIXTURES_PATH = ROOT / "tests/fixtures/android-validation/cases.json"
PUBLIC_PATH = ROOT / "contracts/public-workflows/validation.json"
VALIDATE_ANDROID_SHA = "5f503d419696e5bfae4ed5b11eca3b531dbdca0f"
PRIVATE_HELPER_SHA = "70e08d4ddf8930046632a7135950e924b82e22bf"
PRIVATE_HELPERS = {
    "StreamScapeTV/ci-workflows/actions/validate-android",
    "StreamScapeTV/ci-workflows/actions/exact-checkout",
    "StreamScapeTV/ci-workflows/actions/prepare-workspace",
    "StreamScapeTV/ci-workflows/actions/checkout-private-dependency",
    "StreamScapeTV/ci-workflows/actions/render-evidence",
    "StreamScapeTV/ci-workflows/actions/cleanup-workspace",
}


class AndroidPrivateReuseRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        import json

        cls.source = WORKFLOW_PATH.read_text(encoding="utf-8")
        cls.workflow = yaml.load(cls.source, Loader=ActionsLoader)
        cls.contract = android_contract.load_android_contract(ROOT)
        fixtures = json.loads(FIXTURES_PATH.read_text(encoding="utf-8"))
        cls.public = json.loads(PUBLIC_PATH.read_text(encoding="utf-8"))
        cls.media_case = next(
            case for case in fixtures["positive"] if case["name"] == "media-consumer-script"
        )

    def test_streamscape_media_contract_reaches_protected_android_plan(self) -> None:
        environment = dict(self.media_case["environment"])
        self.assertEqual(environment["GITHUB_REPOSITORY"], "StreamScapeTV/streamscape-media")
        request = android_contract.request_from_environment(environment, self.contract)
        plan = android_contract.resolve_validation_plan(self.contract, request)

        self.assertEqual(plan.admitted_sha, environment["INPUT_ADMITTED_SHA"])
        self.assertEqual(plan.validation_profile, "consumer-script")
        self.assertEqual(plan.task_profile, "media-android-build-script")
        self.assertEqual(plan.planner_runner_profile, "portable")
        self.assertEqual(plan.runner_profile, "mobile")
        self.assertFalse(plan.requires_private_dependency)

    def test_private_consumer_never_clones_central_repository(self) -> None:
        self.assertNotIn("actions/checkout@", self.source)
        self.assertNotIn("job.workflow_repository", self.source)
        self.assertNotIn("job.workflow_sha", self.source)
        self.assertNotIn("path: .ciw", self.source)
        self.assertNotIn("./.ciw/actions/", self.source)
        self.assertNotIn("secrets: inherit", self.source)

        steps = [
            step
            for job in self.workflow["jobs"].values()
            for step in job.get("steps", [])
        ]
        helpers = {
            str(step["uses"]).split("@", 1)[0]: str(step["uses"]).split("@", 1)[1]
            for step in steps
            if str(step.get("uses", "")).startswith("StreamScapeTV/ci-workflows/actions/")
        }
        self.assertEqual(PRIVATE_HELPERS, set(helpers))
        validate_path = "StreamScapeTV/ci-workflows/actions/validate-android"
        self.assertEqual(helpers[validate_path], VALIDATE_ANDROID_SHA)
        self.assertEqual(
            {PRIVATE_HELPER_SHA},
            {sha for path, sha in helpers.items() if path != validate_path},
        )

    def test_public_api_still_supports_streamscape_media_without_new_surface(self) -> None:
        android = next(
            workflow
            for workflow in self.public["workflows"]
            if workflow["api_name"] == "validation.android"
        )
        self.assertIn("StreamScapeTV/streamscape-media", android["supported_consumers"])
        reusable_inputs = set(self.workflow["on"]["workflow_call"]["inputs"])
        self.assertEqual(reusable_inputs, {item["name"] for item in android["inputs"]})


if __name__ == "__main__":
    unittest.main()
