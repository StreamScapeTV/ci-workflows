from __future__ import annotations

import json
import unittest
from pathlib import Path

import yaml

from ci_workflows.validation_model import ActionsLoader

ROOT = Path(__file__).resolve().parents[1]
CACHE_SHA = "bf23a523614c21552b4c2782533f41d6447c4dd3"
CACHE_ACTION = f"StreamScapeTV/ci-workflows/actions/dependency-cache@{CACHE_SHA}"


class DependencyCacheWorkflowIntegrationTests(unittest.TestCase):
    def load(self, name: str):
        path = ROOT / ".github/workflows" / name
        text = path.read_text(encoding="utf-8")
        return text, yaml.load(text, Loader=ActionsLoader)

    def test_node_uses_restore_before_execution_and_trusted_save_after_success(self) -> None:
        text, workflow = self.load("reusable-node.yml")
        steps = workflow["jobs"]["validate"]["steps"]
        restore_index = next(i for i, row in enumerate(steps) if row.get("id") == "dependency_cache_restore")
        execute_index = next(i for i, row in enumerate(steps) if row.get("id") == "node")
        save_index = next(i for i, row in enumerate(steps) if row.get("id") == "dependency_cache_save")
        cleanup_index = next(i for i, row in enumerate(steps) if row.get("id") == "cleanup")
        self.assertLess(restore_index, execute_index)
        self.assertLess(execute_index, save_index)
        self.assertLess(save_index, cleanup_index)
        restore = steps[restore_index]
        save = steps[save_index]
        self.assertEqual(restore["uses"], CACHE_ACTION)
        self.assertEqual(save["uses"], CACHE_ACTION)
        self.assertEqual(restore["with"]["phase"], "restore")
        self.assertEqual(save["with"]["phase"], "save")
        self.assertEqual(restore["with"]["family"], "npm")
        self.assertEqual(save["with"]["family"], "npm")
        self.assertIn("install_profile == 'npm-ci'", restore["if"])
        self.assertIn("steps.node.outcome == 'success'", save["if"])
        self.assertIn("github.ref_protected", save["if"])
        self.assertIn("github.event.repository.default_branch", save["if"])
        self.assertNotIn("restore-keys", text)
        self.assertNotIn("upload-artifact", text)

    def test_android_caches_only_gradle_profiles_and_saves_before_cleanup(self) -> None:
        text, workflow = self.load("reusable-android.yml")
        steps = workflow["jobs"]["validate"]["steps"]
        restore_index = next(i for i, row in enumerate(steps) if row.get("id") == "dependency_cache_restore")
        execute_index = next(i for i, row in enumerate(steps) if row.get("id") == "android")
        save_index = next(i for i, row in enumerate(steps) if row.get("id") == "dependency_cache_save")
        cleanup_index = next(i for i, row in enumerate(steps) if row.get("id") == "android_cleanup")
        self.assertLess(restore_index, execute_index)
        self.assertLess(execute_index, save_index)
        self.assertLess(save_index, cleanup_index)
        restore = steps[restore_index]
        save = steps[save_index]
        self.assertEqual(restore["uses"], CACHE_ACTION)
        self.assertEqual(save["uses"], CACHE_ACTION)
        self.assertEqual(restore["with"]["family"], "gradle")
        self.assertEqual(save["with"]["family"], "gradle")
        for profile in ("toolchain-smoke", "consumer-script", "device-handoff"):
            self.assertIn(profile, restore["if"])
            self.assertIn(profile, save["if"])
        self.assertIn("steps.android.outcome == 'success'", save["if"])
        self.assertIn("github.ref_protected", save["if"])
        self.assertNotIn("restore-keys", text)
        self.assertNotIn("upload-artifact", text)

    def test_pub_family_is_bounded_to_marker_owned_package_cache(self) -> None:
        contract = json.loads((ROOT / "contracts/cache-policy.json").read_text(encoding="utf-8"))
        pub = contract["native_dependency_cache"]["families"]["pub"]
        self.assertEqual(pub["identity_globs"], ["pubspec.lock", "pubspec.yaml"])
        self.assertEqual(pub["paths"], [{"base_env": "PUB_CACHE", "relative": "."}])
        self.assertNotIn("build", json.dumps(pub).casefold())


if __name__ == "__main__":
    unittest.main()
