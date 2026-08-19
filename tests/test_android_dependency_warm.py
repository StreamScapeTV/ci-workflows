from __future__ import annotations

import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
ACTION = ROOT / "actions/warm-gradle-dependencies/action.yml"


class AndroidDependencyWarmActionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = ACTION.read_text(encoding="utf-8")
        cls.action = yaml.safe_load(cls.source)

    def test_action_surface_is_bounded_project_data_only(self) -> None:
        self.assertEqual(
            set(self.action["inputs"]),
            {"admitted_sha", "working_directory", "gradle_wrapper_path"},
        )
        self.assertEqual(
            set(self.action["outputs"]),
            {
                "result",
                "source_sha",
                "gradle_dependency_cache_mode",
                "warm_wall_ms",
            },
        )
        for forbidden in (
            "command",
            "arguments",
            "runner",
            "cache_path",
            "cache_endpoint",
            "repository",
            "token",
            "memory",
            "workers",
        ):
            self.assertNotIn(forbidden, self.action["inputs"])

    def test_action_executes_only_reviewed_dependency_warm_module(self) -> None:
        self.assertEqual(self.action["runs"]["using"], "composite")
        self.assertEqual(len(self.action["runs"]["steps"]), 1)
        step = self.action["runs"]["steps"][0]
        self.assertEqual(step["id"], "warm")
        command = step["run"]
        self.assertIn("python3 -m ci_workflows.gradle_dependency_warm", command)
        self.assertIn("--admitted-sha", command)
        self.assertIn("--working-directory", command)
        self.assertIn("--gradle-wrapper-path", command)
        self.assertNotIn("./gradlew", command)
        self.assertNotIn("--offline", command)
        self.assertNotIn("--refresh-dependencies", command)


if __name__ == "__main__":
    unittest.main()
