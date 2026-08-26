from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import unittest

from ci_workflows.apple_contract_fragments import load_apple_contract
from ci_workflows.apple_multistage import build_protected_full_plan
from ci_workflows.apple_plan_guard import validate_protected_full_plan_json
from ci_workflows.central_profile import CentralProfileError, resolve_from_environment, resolve_profile
from ci_workflows.ci_broker import BrokerError

ROOT = Path(__file__).resolve().parents[1]
SHA = "a" * 40
RELEASE_SHA = "c" * 40
RELEASE_DIGEST = "d" * 64


def apple_config(*, dependency: bool = True, release_asset: bool = False) -> dict[str, object]:
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
    if release_asset:
        profile["private_release_asset"] = {
            "repository": "StreamScapeTV/example-media",
            "tag": "v1.2.1",
            "commit_sha": RELEASE_SHA,
            "asset_name": "example-media-1.2.1-apple-binary.zip",
            "sha256": RELEASE_DIGEST,
            "archive_subpath": "ExampleMediaApple",
            "destination": "Vendor/ExampleMediaApple",
            "id": "example-media-binary",
        }
    return {
        "schema_version": 1,
        "project_key": "example-project",
        "profiles": {"host": profile},
        "automatic": {"push": "host", "tag": "host"},
    }


def android_config() -> dict[str, object]:
    return {
        "schema_version": 2,
        "project_key": "example-project",
        "profiles": {
            "host": {
                "workflow_key": "validation.android",
                "capability": "android-hosted",
                "inputs": {
                    "working_directory": ".",
                    "gradle_wrapper_path": "gradlew",
                    "validation_plan_json": {"groups": [{"id": "product", "tasks": [":app:test"]}]},
                },
            }
        },
    }


def python_config() -> dict[str, object]:
    return {
        "schema_version": 2,
        "project_key": "example-project",
        "profiles": {
            "host": {
                "workflow_key": "validation.python",
                "capability": "python-hosted",
                "inputs": {
                    "validation_profile": "podman-postgres",
                    "python_version": "3.12",
                    "dependency_file": "requirements.lock",
                    "script_path": "scripts/ci/validate.sh",
                },
            }
        },
    }


def write_config(root: Path, value: object) -> None:
    directory = root / ".github"
    directory.mkdir()
    (directory / "central-ci.json").write_text(
        json.dumps(value, sort_keys=True, separators=(",", ":")), encoding="utf-8"
    )


def resolve(root: Path, workflow_key: str) -> object:
    return resolve_profile(
        source_root=str(root),
        project_key="example-project",
        workflow_key=workflow_key,
        test_profile="host",
        source_repository="StreamScapeTV/example-app",
        admitted_sha=SHA,
    )


class CentralProfileTests(unittest.TestCase):
    def test_v1_apple_contract_remains_compatible(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "source"
            source.mkdir()
            write_config(source, apple_config())
            resolved = resolve(source, "validation.apple")
        self.assertEqual(resolved.executor_family, "macos")
        self.assertEqual(resolved.validation_scope, "protected-full")
        self.assertEqual(resolved.capability, "apple-host-test")
        self.assertEqual(resolved.private_dependency_repository, "StreamScapeTV/example-media")
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
        self.assertEqual(plan.stages[0].platform, "macos")

    def test_v1_release_asset_contract_is_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "source"
            source.mkdir()
            write_config(source, apple_config(dependency=False, release_asset=True))
            resolved = resolve(source, "validation.apple")
        self.assertEqual(resolved.private_release_asset_tag, "v1.2.1")
        self.assertEqual(resolved.private_release_asset_sha256, RELEASE_DIGEST)
        self.assertEqual(resolved.private_release_asset_destination, "Vendor/ExampleMediaApple")
        self.assertIsNotNone(resolved.release_asset())

    def test_android_v2_projects_only_canonical_hosted_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "source"
            source.mkdir()
            write_config(source, android_config())
            resolved = resolve(source, "validation.android")
        self.assertEqual(resolved.executor_family, "linux")
        self.assertEqual(resolved.capability, "android-hosted")
        inputs = resolved.canonical_inputs()
        self.assertEqual(set(inputs), {
            "working_directory", "gradle_wrapper_path", "validation_plan_json", "dependency_prebuild_plan_json"
        })
        self.assertEqual(inputs["working_directory"], ".")
        self.assertEqual(inputs["gradle_wrapper_path"], "gradlew")
        self.assertEqual(json.loads(inputs["validation_plan_json"])["groups"][0]["id"], "product")
        self.assertEqual(inputs["dependency_prebuild_plan_json"], "")

    def test_python_v2_projects_only_canonical_hosted_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "source"
            source.mkdir()
            write_config(source, python_config())
            resolved = resolve(source, "validation.python")
        self.assertEqual(resolved.executor_family, "linux")
        self.assertEqual(resolved.capability, "python-hosted")
        inputs = resolved.canonical_inputs()
        self.assertEqual(inputs["validation_profile"], "podman-postgres")
        self.assertEqual(inputs["python_version"], "3.12")
        self.assertEqual(inputs["script_path"], "scripts/ci/validate.sh")
        self.assertEqual(inputs["artifact_exception_id"], "")
        self.assertNotIn("runner", resolved.canonical_inputs_json)
        self.assertNotIn("secret", resolved.canonical_inputs_json)

    def test_v2_rejects_arbitrary_commands_runner_labels_and_secret_names(self) -> None:
        for forbidden in (
            {"command": "curl example"},
            {"runner": "self-hosted"},
            {"secret_name": "TOKEN"},
            {"workflow_path": ".github/workflows/anything.yml"},
        ):
            with self.subTest(forbidden=forbidden), tempfile.TemporaryDirectory() as temporary:
                source = Path(temporary) / "source"
                source.mkdir()
                config = python_config()
                config["profiles"]["host"]["inputs"].update(forbidden)  # type: ignore[index,union-attr]
                write_config(source, config)
                with self.assertRaisesRegex(CentralProfileError, "private_ci_profile_inputs_invalid"):
                    resolve(source, "validation.python")

    def test_workflow_capability_and_profile_mismatch_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "source"
            source.mkdir()
            config = android_config()
            write_config(source, config)
            with self.assertRaisesRegex(CentralProfileError, "workflow_profile_mismatch"):
                resolve(source, "validation.python")
            config["profiles"]["host"]["capability"] = "anything"  # type: ignore[index]
            (source / ".github/central-ci.json").write_text(json.dumps(config), encoding="utf-8")
            with self.assertRaisesRegex(CentralProfileError, "central_profile_unsupported"):
                resolve(source, "validation.android")

    def test_duplicate_json_key_and_symlinked_config_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "source"
            source.mkdir()
            github = source / ".github"
            github.mkdir()
            config = github / "central-ci.json"
            config.write_text('{"schema_version":2,"project_key":"one","project_key":"two","profiles":{}}', encoding="utf-8")
            with self.assertRaisesRegex(CentralProfileError, "private_ci_config_duplicate_key"):
                resolve_profile(
                    source_root=str(source), project_key="one", workflow_key="validation.python",
                    test_profile="host", source_repository="StreamScapeTV/example-app", admitted_sha=SHA,
                )
            config.unlink()
            target = Path(temporary) / "outside.json"
            target.write_text(json.dumps(python_config()), encoding="utf-8")
            os.symlink(target, config)
            with self.assertRaisesRegex(CentralProfileError, "private_ci_config_invalid_path"):
                resolve_profile(
                    source_root=str(source), project_key="example-project", workflow_key="validation.python",
                    test_profile="host", source_repository="StreamScapeTV/example-app", admitted_sha=SHA,
                )

    def test_environment_adapter_outputs_bounded_projection_only(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source"
            source.mkdir()
            write_config(source, python_config())
            output = root / "github-output"
            output.touch()
            resolved = resolve_from_environment(
                {
                    "INPUT_SOURCE_ROOT": str(source),
                    "INPUT_PROJECT_KEY": "example-project",
                    "INPUT_WORKFLOW_KEY": "validation.python",
                    "INPUT_TEST_PROFILE": "host",
                    "INPUT_SOURCE_REPOSITORY": "StreamScapeTV/example-app",
                    "INPUT_ADMITTED_SHA": SHA,
                    "GITHUB_OUTPUT": str(output),
                }
            )
            text = output.read_text(encoding="utf-8")
        self.assertIn("workflow_key=validation.python\n", text)
        self.assertIn("executor_family=linux\n", text)
        self.assertNotIn("token", text.lower())
        self.assertEqual(resolved.source_repository, "StreamScapeTV/example-app")

    def test_composite_action_remains_thin_secret_free_and_transport_neutral(self) -> None:
        action = (ROOT / "actions/resolve-central-profile/action.yml").read_text(encoding="utf-8")
        self.assertIn("central-profile resolve", action)
        self.assertNotIn("secrets:", action)
        self.assertNotIn("secrets.", action)
        self.assertNotIn("curl ", action)
        self.assertNotIn("gh ", action)
        self.assertNotIn("xcodebuild", action)
        self.assertNotIn("AGENT_STATE", action)


if __name__ == "__main__":
    unittest.main()
