from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from ci_workflows.central_profile import CentralProfileError, resolve_profile

SHA = "a" * 40


def mixed_config() -> dict[str, object]:
    return {
        "schema_version": 2,
        "project_key": "example-project",
        "profiles": {
            "host": {
                "workflows": {
                    "validation.android": {
                        "capability": "android-hosted",
                        "inputs": {
                            "working_directory": ".",
                            "gradle_wrapper_path": "gradlew",
                            "validation_plan_json": {
                                "groups": [
                                    {"id": "product", "tasks": [":app:test"]}
                                ]
                            },
                        },
                    },
                    "validation.apple": {
                        "capability": "apple-hosted",
                        "inputs": {
                            "command_profile": "streamscape-media-apple",
                            "validation_profile": "swift-package",
                        },
                    },
                }
            }
        },
    }


def write_config(root: Path, value: object) -> None:
    github = root / ".github"
    github.mkdir()
    (github / "central-ci.json").write_text(
        json.dumps(value, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )


def resolve(root: Path, workflow_key: str):
    return resolve_profile(
        source_root=str(root),
        project_key="example-project",
        workflow_key=workflow_key,
        test_profile="host",
        source_repository="StreamScapeTV/example-app",
        admitted_sha=SHA,
    )


class MixedFamilyCentralProfileTests(unittest.TestCase):
    def test_one_host_profile_can_project_reviewed_android_and_apple_families(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "source"
            source.mkdir()
            write_config(source, mixed_config())
            android = resolve(source, "validation.android")
            apple = resolve(source, "validation.apple")

        self.assertEqual(android.test_profile, "host")
        self.assertEqual(android.capability, "android-hosted")
        self.assertEqual(android.executor_family, "linux")
        self.assertEqual(android.validation_scope, "protected-full")
        self.assertEqual(
            set(android.canonical_inputs()),
            {
                "working_directory",
                "gradle_wrapper_path",
                "validation_plan_json",
                "dependency_prebuild_plan_json",
            },
        )

        self.assertEqual(apple.test_profile, "host")
        self.assertEqual(apple.workflow_key, "validation.apple")
        self.assertEqual(apple.capability, "apple-hosted")
        self.assertEqual(apple.executor_family, "macos")
        self.assertEqual(apple.validation_scope, "legacy")
        self.assertEqual(
            apple.canonical_inputs(),
            {
                "command_profile": "streamscape-media-apple",
                "validation_profile": "swift-package",
            },
        )
        self.assertEqual(apple.validation_plan_json, "")
        self.assertIsNone(apple.release_asset())

    def test_workflow_scoped_profile_rejects_unreviewed_workflow_family(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "source"
            source.mkdir()
            config = mixed_config()
            workflows = config["profiles"]["host"]["workflows"]  # type: ignore[index]
            workflows["validation.script"] = {  # type: ignore[index]
                "capability": "script-hosted",
                "inputs": {},
            }
            write_config(source, config)
            with self.assertRaisesRegex(CentralProfileError, "private_ci_profile_invalid"):
                resolve(source, "validation.apple")

    def test_apple_projection_rejects_arbitrary_inputs_and_unreviewed_profile(self) -> None:
        for key, value in (
            ("runner", "self-hosted"),
            ("script_path", "scripts/anything.sh"),
            ("command", "swift test"),
            ("physical_device", "true"),
        ):
            with self.subTest(key=key), tempfile.TemporaryDirectory() as temporary:
                source = Path(temporary) / "source"
                source.mkdir()
                config = mixed_config()
                apple = config["profiles"]["host"]["workflows"]["validation.apple"]  # type: ignore[index]
                apple["inputs"][key] = value  # type: ignore[index]
                write_config(source, config)
                with self.assertRaisesRegex(CentralProfileError, "private_ci_profile_inputs_invalid"):
                    resolve(source, "validation.apple")

        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "source"
            source.mkdir()
            config = mixed_config()
            apple = config["profiles"]["host"]["workflows"]["validation.apple"]  # type: ignore[index]
            apple["inputs"]["validation_profile"] = "ios-simulator"  # type: ignore[index]
            write_config(source, config)
            with self.assertRaisesRegex(CentralProfileError, "invalid_apple_validation_profile"):
                resolve(source, "validation.apple")

    def test_apple_v2_rejects_private_dependency_until_legacy_binding_is_explicit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / "source"
            source.mkdir()
            config = mixed_config()
            apple = config["profiles"]["host"]["workflows"]["validation.apple"]  # type: ignore[index]
            apple["private_dependency"] = {  # type: ignore[index]
                "repository": "StreamScapeTV/example-media",
                "sha": "b" * 40,
                "subdirectory": ".",
                "id": "example-media",
            }
            write_config(source, config)
            with self.assertRaisesRegex(CentralProfileError, "apple_private_dependency_unsupported"):
                resolve(source, "validation.apple")


if __name__ == "__main__":
    unittest.main()
