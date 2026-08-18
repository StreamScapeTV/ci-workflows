from __future__ import annotations

import json
import unittest
from pathlib import Path

from ci_workflows.validation_model import ActionsLoader
import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_PATH = ROOT / ".github/workflows/reusable-android.yml"
SEED_WORKFLOW_PATH = ROOT / ".github/workflows/reusable-android-seed-warm.yml"
PUBLIC_PATH = ROOT / "contracts/public-workflows/validation.json"
ANDROID_SHA = "a01e29210603dc8b4cb9e31b9b0c926c2ab5cf37"
GRADLE_SEED_SHA = "7a0977db839468aac24448831a9a0ffd97b3067b"
FOUNDATION_SHA = "70e08d4ddf8930046632a7135950e924b82e22bf"


class AndroidPrivateReuseRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = WORKFLOW_PATH.read_text(encoding="utf-8")
        cls.workflow = yaml.load(cls.source, Loader=ActionsLoader)
        cls.seed_source = SEED_WORKFLOW_PATH.read_text(encoding="utf-8")
        cls.seed_workflow = yaml.load(cls.seed_source, Loader=ActionsLoader)
        cls.public = json.loads(PUBLIC_PATH.read_text(encoding="utf-8"))

    def test_private_consumers_use_only_immutable_central_helpers(self) -> None:
        for workflow, source, expect_seed in (
            (self.workflow, self.source, False),
            (self.seed_workflow, self.seed_source, True),
        ):
            self.assertNotIn("actions/checkout@", source)
            self.assertNotIn("job.workflow_repository", source)
            self.assertNotIn("job.workflow_sha", source)
            self.assertNotIn("path: .ciw", source)
            self.assertNotIn("./.ciw/actions/", source)
            self.assertNotIn("secrets: inherit", source)
            helpers = {
                str(step["uses"]).split("@", 1)[0]: str(step["uses"]).split("@", 1)[1]
                for step in workflow["jobs"]["validate"]["steps"]
                if str(step.get("uses", "")).startswith("StreamScapeTV/ci-workflows/actions/")
            }
            self.assertEqual(helpers["StreamScapeTV/ci-workflows/actions/validate-android"], ANDROID_SHA)
            if expect_seed:
                self.assertEqual(helpers["StreamScapeTV/ci-workflows/actions/upload-gradle-seed"], GRADLE_SEED_SHA)
            else:
                self.assertNotIn("StreamScapeTV/ci-workflows/actions/upload-gradle-seed", helpers)
            for helper in (
                "exact-checkout",
                "prepare-workspace",
                "checkout-private-dependency",
                "render-evidence",
                "cleanup-workspace",
            ):
                self.assertEqual(helpers[f"StreamScapeTV/ci-workflows/actions/{helper}"], FOUNDATION_SHA)

    def test_private_dependency_token_reaches_only_checkout_boundary(self) -> None:
        for workflow in (self.workflow, self.seed_workflow):
            steps = workflow["jobs"]["validate"]["steps"]
            dependency = next(step for step in steps if step["id"] == "dependency")
            self.assertEqual(dependency["with"]["token"], "${{ secrets.private_dependency_token }}")
            for step in steps:
                if step is dependency:
                    continue
                self.assertNotIn("private_dependency_token", json.dumps(step))

    def test_public_android_apis_are_repository_identity_free(self) -> None:
        records = {row["api_name"]: row for row in self.public["workflows"]}
        routine = records["validation.android"]
        warm = records["validation.android-seed-warm"]
        routine_inputs = set(self.workflow["on"]["workflow_call"]["inputs"])
        warm_inputs = set(self.seed_workflow["on"]["workflow_call"]["inputs"])
        self.assertEqual(routine_inputs, {item["name"] for item in routine["inputs"]})
        self.assertEqual(warm_inputs, {item["name"] for item in warm["inputs"]})
        self.assertEqual(routine_inputs, warm_inputs)
        for record in (routine, warm):
            self.assertNotIn("supported_consumers", record)
            self.assertNotIn("supported_products", record)
        for forbidden in (
            "validation_profile",
            "task_profile",
            "consumer_script_profile",
            "private_dependency_contract_id",
            "product_id",
            "promote_gradle_seed",
        ):
            self.assertNotIn(forbidden, routine_inputs)
        self.assertIn("validation_plan_json", routine_inputs)
        self.assertIn("private_dependency_repository", routine_inputs)
        self.assertIn("private_dependency_sha", routine_inputs)
        self.assertIn("private_dependency_subdirectory", routine_inputs)


if __name__ == "__main__":
    unittest.main()
