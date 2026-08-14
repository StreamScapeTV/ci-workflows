from __future__ import annotations

import contextlib
import importlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ci_workflows import apple as apple_validation
from ci_workflows import ciw_apple

ROOT = Path(__file__).resolve().parents[1]


class AppleFragmentLoaderIsolationTests(unittest.TestCase):
    @staticmethod
    def source_audit_task() -> dict[str, object]:
        return {
            "validation_profile": "source-audit",
            "working_directory": ".",
            "container": None,
            "simulator_id": None,
            "commands": [],
            "protected_paths": ["AGENTS.md"],
            "cleanup_paths": [],
            "artifact_exception_ids": [],
            "environment": {},
        }

    def fragment_root(self, directory: str) -> Path:
        root = Path(directory)
        contracts = root / "contracts"
        contracts.mkdir()
        (contracts / "apple-validation.json").write_text(
            (ROOT / "contracts/apple-validation.json").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        (contracts / "apple-validation-isolation.json").write_text(
            json.dumps(
                {
                    "tasks": {"fragment-source-audit": self.source_audit_task()},
                    "consumer_contracts": {
                        "fragment-apple-smoke": {
                            "repository": "StreamScapeTV/ci-workflows",
                            "profiles": {"source-audit": "fragment-source-audit"},
                        }
                    },
                }
            ),
            encoding="utf-8",
        )
        return root

    def test_import_does_not_replace_apple_facade_loader(self) -> None:
        original = apple_validation.load_apple_contract
        importlib.reload(ciw_apple)
        self.assertIs(apple_validation.load_apple_contract, original)

    def test_standalone_plan_uses_fragment_loader_without_global_mutation(self) -> None:
        original = apple_validation.load_apple_contract
        with tempfile.TemporaryDirectory() as directory:
            root = self.fragment_root(directory)
            environment = {
                "GITHUB_REPOSITORY": "StreamScapeTV/ci-workflows",
                "GITHUB_WORKSPACE": str(root),
                "INPUT_ADMITTED_SHA": "a" * 40,
                "INPUT_COMMAND_PROFILE": "fragment-apple-smoke",
                "INPUT_VALIDATION_PROFILE": "source-audit",
                "INPUT_PLATFORM": "apple",
                "INPUT_SOURCE_TRUST": "trusted-exact",
            }
            output = io.StringIO()
            with (
                mock.patch.dict(os.environ, environment, clear=True),
                mock.patch.object(
                    ciw_apple,
                    "_planning_outputs",
                    side_effect=lambda _root, plan, _request: plan.planning_outputs(),
                ),
                contextlib.redirect_stdout(output),
            ):
                self.assertEqual(
                    ciw_apple.standalone_main(["--root", str(root), "plan"]),
                    0,
                )
            payload = json.loads(output.getvalue().strip())
            self.assertEqual(payload["task_profile"], "fragment-source-audit")
            self.assertEqual(payload["runner_profile"], "apple")
            self.assertEqual(payload["planner_runner_profile"], "portable")
        self.assertIs(apple_validation.load_apple_contract, original)


if __name__ == "__main__":
    unittest.main()
