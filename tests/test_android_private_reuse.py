from __future__ import annotations

import json
import unittest
from pathlib import Path

from ci_workflows.validation_model import ActionsLoader
import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_PATH = ROOT / ".github/workflows/reusable-android.yml"
PUBLIC_PATH = ROOT / "contracts/public-workflows/validation.json"
ANDROID_SHA = "ac56fd7b3fac55f231e7b2ba715a5aebebbe51ef"
FOUNDATION_SHA = "70e08d4ddf8930046632a7135950e924b82e22bf"


class AndroidPrivateReuseRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = WORKFLOW_PATH.read_text(encoding="utf-8")
        cls.workflow = yaml.load(cls.source, Loader=ActionsLoader)
        cls.public = json.loads(PUBLIC_PATH.read_text(encoding="utf-8"))

    def test_private_consumer_uses_only_immutable_central_helpers(self) -> None:
        self.assertNotIn("actions/checkout@", self.source)
        self.assertNotIn("job.workflow_repository", self.source)
        self.assertNotIn("job.workflow_sha", self.source)
        self.assertNotIn("path: .ciw", self.source)
        self.assertNotIn("./.ciw/actions/", self.source)
        self.assertNotIn("secrets: inherit", self.source)
        helpers = {
            str(step["uses"]).split("@", 1)[0]: str(step["uses"]).split("@", 1)[1]
            for step in self.workflow["jobs"]["validate"]["steps"]
            if str(step.get("uses", "")).startswith("StreamScapeTV/ci-workflows/actions/")
        }
        self.assertEqual(
            helpers["StreamScapeTV/ci-workflows/actions/validate-android"],
            ANDROID_SHA,
        )
        for helper in (
            "exact-checkout",
            "prepare-workspace",
            "checkout-private-dependency",
            "render-evidence",
            "cleanup-workspace",
        ):
            self.assertEqual(
                helpers[f"StreamScapeTV/ci-workflows/actions/{helper}"],
                FOUNDATION_SHA,
            )

    def test_private_dependency_token_reaches_only_checkout_boundary(self) -> None:
        steps = self.workflow["jobs"]["validate"]["steps"]
        dependency = next(step for step in steps if step["id"] == "dependency")
        self.assertEqual(
            dependency["with"]["token"],
            "${{ secrets.private_dependency_token }}",
        )
        for step in steps:
            if step is dependency:
                continue
            self.assertNotIn("private_dependency_token", json.dumps(step))

    def test_public_android_api_is_repository_identity_free(self) -> None:
        android = next(
            workflow
            for workflow in self.public["workflows"]
            if workflow["api_name"] == "validation.android"
        )
        self.assertNotIn("supported_consumers", android)
        self.assertNotIn("supported_products", android)
        reusable_inputs = set(self.workflow["on"]["workflow_call"]["inputs"])
        self.assertEqual(reusable_inputs, {item["name"] for item in android["inputs"]})
        for forbidden in (
            "validation_profile",
            "task_profile",
            "consumer_script_profile",
            "private_dependency_contract_id",
            "product_id",
        ):
            self.assertNotIn(forbidden, reusable_inputs)
        self.assertIn("validation_plan_json", reusable_inputs)
        self.assertIn("private_dependency_repository", reusable_inputs)
        self.assertIn("private_dependency_sha", reusable_inputs)
        self.assertIn("private_dependency_subdirectory", reusable_inputs)


if __name__ == "__main__":
    unittest.main()
