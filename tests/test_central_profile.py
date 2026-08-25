from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import unittest

from ci_workflows.apple_contract_fragments import load_apple_contract
from ci_workflows.apple_multistage import build_protected_full_plan
from ci_workflows.apple_plan_guard import validate_protected_full_plan_json
from ci_workflows.central_profile import (
    CentralProfileError,
    resolve_from_environment,
    resolve_profile,
)
from ci_workflows.ci_broker import BrokerError

ROOT = Path(__file__).resolve().parents[1]
SHA = "a" * 40


def product_config(*, dependency: bool = True) -> dict[str, object]:
    profile: dict[str, object] = {
        "workflow_key": "validation.apple",
        "capability": "apple-host-test",
        "workspace": "Example.xcworkspace",
        "scheme": "Example",
        "test_target": "ExampleTests/SelectedIntegrationTests",
    }
    if dependency:
        profile["private_dependency"] = {
            "repository": "StreamScapeTV/example-media",
            "sha": "b" * 40,
            "subdirectory": ".",
            "id": "example-media",
        }
    return {
        "schema_version": 1,
        "project_key": "example-project",
        "profiles": {"host": profile},
        "automatic": {"push": "host", "tag": "host"},
    }


def write_config(root: Path, value: object) -> None:
    directory = root / ".github"
    directory.mkdir()
    (directory / "central-ci.json").write_text(
        json.dumps(value, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )


class CentralProfileTests(unittest.TestCase):
    def test_exact_product_profile_projects_to_canonical_apple_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "source"
            source.mkdir()
            write_config(source, product_config())
            resolved = resolve_profile(
                source_root=str(source),
                project_key="example-project",
                workflow_key="validation.apple",
                test_profile="host",
                source_repository="StreamScapeTV/example-app",
                admitted_sha=SHA,
            )

        self.assertEqual(resolved.validation_scope, "protected-full")
        self.assertEqual(resolved.capability, "apple-host-test")
        self.assertEqual(resolved.private_dependency_repository, "StreamScapeTV/example-media")
        self.assertEqual(resolved.private_dependency_sha, "b" * 40)
        self.assertEqual(resolved.private_dependency_id, "example-media")
        validate_protected_full_plan_json(resolved.validation_plan_json)
        plan = build_protected_full_plan(
            resolved.validation_plan_json,
            repository=resolved.source_repository,
            admitted_sha=resolved.admitted_sha,
            source_trust="trusted-exact",
            contract=load_apple_contract(ROOT),
            private_dependency_repository=resolved.private_dependency_repository,
            private_dependency_sha=resolved.private_dependency_sha,
            private_dependency_subdirectory=resolved.private_dependency_subdirectory,
            private_dependency_id=resolved.private_dependency_id,
        )
        self.assertEqual(len(plan.stages), 1)
        self.assertEqual(plan.stages[0].platform, "macos")
        self.assertEqual(plan.stages[0].operation, "test")
        self.assertEqual(
            plan.stages[0].plan.commands[0].fixed_arguments,
            ("-only-testing:ExampleTests/SelectedIntegrationTests",),
        )

    def test_profile_without_dependency_keeps_optional_channel_empty(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "source"
            source.mkdir()
            write_config(source, product_config(dependency=False))
            resolved = resolve_profile(
                source_root=str(source),
                project_key="example-project",
                workflow_key="validation.apple",
                test_profile="host",
                source_repository="StreamScapeTV/example-app",
                admitted_sha=SHA,
            )
        self.assertEqual(resolved.private_dependency_repository, "")
        self.assertEqual(resolved.private_dependency_sha, "")
        self.assertEqual(resolved.private_dependency_subdirectory, ".")
        self.assertEqual(resolved.private_dependency_id, "")

    def test_project_workflow_and_profile_identity_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "source"
            source.mkdir()
            write_config(source, product_config())
            cases = (
                ({"project_key": "wrong-project"}, CentralProfileError, "project_config_mismatch"),
                ({"workflow_key": "validation.python"}, BrokerError, "workflow_profile_mismatch"),
                ({"test_profile": "missing"}, BrokerError, "private_ci_profile_missing"),
            )
            base = {
                "source_root": str(source),
                "project_key": "example-project",
                "workflow_key": "validation.apple",
                "test_profile": "host",
                "source_repository": "StreamScapeTV/example-app",
                "admitted_sha": SHA,
            }
            for patch, error_type, code in cases:
                with self.subTest(code=code), self.assertRaisesRegex(error_type, code):
                    resolve_profile(**{**base, **patch})

    def test_duplicate_json_key_and_symlinked_config_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "source"
            source.mkdir()
            github = source / ".github"
            github.mkdir()
            config = github / "central-ci.json"
            config.write_text(
                '{"schema_version":1,"project_key":"one","project_key":"two","profiles":{}}',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(CentralProfileError, "private_ci_config_duplicate_key"):
                resolve_profile(
                    source_root=str(source),
                    project_key="one",
                    workflow_key="validation.apple",
                    test_profile="host",
                    source_repository="StreamScapeTV/example-app",
                    admitted_sha=SHA,
                )
            config.unlink()
            target = Path(temporary) / "outside.json"
            target.write_text(json.dumps(product_config()), encoding="utf-8")
            os.symlink(target, config)
            with self.assertRaisesRegex(CentralProfileError, "private_ci_config_invalid_path"):
                resolve_profile(
                    source_root=str(source),
                    project_key="example-project",
                    workflow_key="validation.apple",
                    test_profile="host",
                    source_repository="StreamScapeTV/example-app",
                    admitted_sha=SHA,
                )

    def test_environment_adapter_writes_only_bounded_profile_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            source.mkdir()
            write_config(source, product_config())
            output = root / "github-output"
            output.touch()
            resolved = resolve_from_environment(
                {
                    "INPUT_SOURCE_ROOT": str(source),
                    "INPUT_PROJECT_KEY": "example-project",
                    "INPUT_WORKFLOW_KEY": "validation.apple",
                    "INPUT_TEST_PROFILE": "host",
                    "INPUT_SOURCE_REPOSITORY": "StreamScapeTV/example-app",
                    "INPUT_ADMITTED_SHA": SHA,
                    "GITHUB_OUTPUT": str(output),
                }
            )
            text = output.read_text(encoding="utf-8")
        self.assertIn("workflow_key=validation.apple\n", text)
        self.assertIn("validation_scope=protected-full\n", text)
        self.assertIn("private_dependency_repository=StreamScapeTV/example-media\n", text)
        self.assertIn(f"admitted_sha={SHA}\n", text)
        self.assertNotIn("token", text.lower())
        self.assertEqual(resolved.source_repository, "StreamScapeTV/example-app")

    def test_composite_action_is_thin_secret_free_and_transport_neutral(self) -> None:
        action = (ROOT / "actions/resolve-central-profile/action.yml").read_text(encoding="utf-8")
        self.assertIn("central-profile resolve", action)
        self.assertNotIn("secrets:", action)
        self.assertNotIn("secrets.", action)
        self.assertNotIn("curl ", action)
        self.assertNotIn("gh ", action)
        self.assertNotIn("xcodebuild", action)
        self.assertNotIn("CI_BROKER", action)
        self.assertNotIn("AGENT_STATE", action)


if __name__ == "__main__":
    unittest.main()
