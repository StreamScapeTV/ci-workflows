from __future__ import annotations

import unittest
from pathlib import Path

import yaml

from ci_workflows.validation_model import ActionsLoader

ROOT = Path(__file__).resolve().parents[1]
CACHE_SHA = "82e093dd5912a0f264b9939275657211b378e389"
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
        self.assertEqual(save["with"]["expected_cache_key"], "${{ steps.dependency_cache_restore.outputs.cache_key }}")
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
        self.assertEqual(save["with"]["expected_cache_key"], "${{ steps.dependency_cache_restore.outputs.cache_key }}")
        for profile in ("toolchain-smoke", "consumer-script", "device-handoff"):
            self.assertIn(profile, restore["if"])
            self.assertIn(profile, save["if"])
        self.assertIn("steps.android.outcome == 'success'", save["if"])
        self.assertIn("github.ref_protected", save["if"])
        self.assertNotIn("restore-keys", text)
        self.assertNotIn("upload-artifact", text)

    def test_mobile_flutter_restores_pub_after_binding_and_saves_before_cleanup(self) -> None:
        text, workflow = self.load("reusable-flutter.yml")
        mobile_steps = workflow["jobs"]["mobile"]["steps"]
        bind_index = next(i for i, row in enumerate(mobile_steps) if row.get("id") == "pub_cache_bind")
        restore_index = next(i for i, row in enumerate(mobile_steps) if row.get("id") == "dependency_cache_restore")
        execute_index = next(i for i, row in enumerate(mobile_steps) if row.get("id") == "execute")
        save_index = next(i for i, row in enumerate(mobile_steps) if row.get("id") == "dependency_cache_save")
        verify_index = next(i for i, row in enumerate(mobile_steps) if row.get("id") == "persistent_cache_verify")
        self.assertLess(bind_index, restore_index)
        self.assertLess(restore_index, execute_index)
        self.assertLess(execute_index, save_index)
        self.assertLess(save_index, verify_index)
        restore = mobile_steps[restore_index]
        save = mobile_steps[save_index]
        self.assertEqual(restore["uses"], CACHE_ACTION)
        self.assertEqual(save["uses"], CACHE_ACTION)
        self.assertEqual(restore["with"]["family"], "pub")
        self.assertEqual(save["with"]["family"], "pub")
        self.assertIn("steps.execute.outcome == 'success'", save["if"])
        self.assertIn("github.ref_protected", save["if"])
        self.assertIn("github.event.repository.default_branch", save["if"])
        apple_steps = workflow["jobs"]["apple"]["steps"]
        self.assertFalse(any(row.get("uses") == CACHE_ACTION for row in apple_steps))
        self.assertNotIn("restore-keys", text)
        self.assertNotIn("upload-artifact", text)


if __name__ == "__main__":
    unittest.main()
