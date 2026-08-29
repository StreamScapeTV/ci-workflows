from pathlib import Path
import unittest

import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "android.yml"
PERFORMANCE_TEST_CLASS = (
    "com.streamscapetv.app.foundation.performance.testing.PerformanceBudgetTest"
)


class AndroidSearchPerformanceProfileContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.workflow_text = WORKFLOW_PATH.read_text(encoding="utf-8")
        cls.workflow = yaml.safe_load(cls.workflow_text)
        steps = cls.workflow["jobs"]["ci"]["steps"]
        cls.profile_step = next(
            step for step in steps if step.get("name") == "Run fixed Android profile"
        )
        cls.profile_script = cls.profile_step["run"]

    def profile_body(self, profile: str) -> str:
        start = self.profile_script.index(f"{profile})")
        end = self.profile_script.index(";;", start)
        return self.profile_script[start:end]

    def test_search_performance_profile_uses_only_fixed_product_class(self) -> None:
        self.assertIn(
            f"performance_test_class='{PERFORMANCE_TEST_CLASS}'",
            self.profile_script,
        )
        body = self.profile_body("search-performance")
        self.assertIn(
            'run_gradle testDebugUnitTest --tests "${performance_test_class}"',
            body,
        )
        self.assertNotIn("TEST_FILTER", body)
        self.assertNotIn("inputs.", body)

    def test_full_excludes_host_sensitive_performance_class(self) -> None:
        body = self.profile_body("full")
        self.assertIn("android-full-correctness.init.gradle", body)
        self.assertIn("excludeTestsMatching", body)
        self.assertIn("${performance_test_class}", body)
        self.assertIn('--init-script "${correctness_init_script}"', body)
        self.assertIn("compileDebugKotlin", body)
        self.assertIn("testDebugUnitTest", body)
        self.assertIn("lintDebug", body)
        self.assertIn("assembleDebug", body)

    def test_profile_does_not_broaden_develop_dependency_cache(self) -> None:
        steps = self.workflow["jobs"]["ci"]["steps"]
        save_step = next(
            step
            for step in steps
            if step.get("name") == "Save IPTV Android develop dependency cache"
        )
        self.assertNotIn("search-performance", save_step["if"])
        self.assertIn("inputs.test_profile == 'full'", save_step["if"])
        self.assertIn("inputs.test_profile == 'release'", save_step["if"])


if __name__ == "__main__":
    unittest.main()
