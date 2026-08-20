from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ci_workflows import apple_execution
from ci_workflows.apple_contract_fragments import load_apple_contract
from ci_workflows.apple_multistage import build_protected_full_plan, execute_protected_full
from ci_workflows.apple_types import AppleValidationError

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = "StreamScapeTV/ci-workflows"


def workspace_stage() -> dict[str, object]:
    return {
        "id": "macos-build",
        "platform": "macos",
        "operation": "build-for-testing",
        "working_directory": ".",
        "container": {"kind": "workspace", "path": "App.xcworkspace"},
        "scheme": "App",
        "configuration": "Debug",
        "test_plan": "",
        "package_resolution_mode": "resolve-only",
        "resolved_files": [],
        "script": None,
        "xcodebuild_arguments": [],
        "test_selectors": [],
        "expected_outputs": [],
        "cleanup_paths": [],
    }


def raw_plan() -> str:
    return json.dumps({"stages": [workspace_stage()]}, separators=(",", ":"))


class AppleProtectedGeneratedStateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.contract = load_apple_contract(ROOT)

    @staticmethod
    def git(root: Path, *arguments: str) -> str:
        completed = subprocess.run(
            ["git", *arguments],
            cwd=root,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        return completed.stdout.strip()

    def make_source(self, root: Path) -> tuple[Path, str]:
        source = root / "source"
        workspace = source / "App.xcworkspace"
        workspace.mkdir(parents=True)
        (workspace / "contents.xcworkspacedata").write_text(
            "<Workspace version=\"1.0\"/>\n",
            encoding="utf-8",
        )
        (source / ".gitignore").write_text(
            "App.xcworkspace/xcuserdata/\n"
            "App.xcworkspace/xcshareddata/swiftpm/\n"
            "Package.resolved\n",
            encoding="utf-8",
        )
        self.git(source, "init", "-q")
        self.git(source, "config", "user.email", "ci@example.invalid")
        self.git(source, "config", "user.name", "CI Fixture")
        self.git(source, "add", ".")
        self.git(source, "commit", "-q", "-m", "fixture")
        return source, self.git(source, "rev-parse", "HEAD")

    def build(self, admitted_sha: str):
        return build_protected_full_plan(
            raw_plan(),
            repository=REPOSITORY,
            admitted_sha=admitted_sha,
            source_trust="trusted-pr",
            contract=self.contract,
        )

    @staticmethod
    def toolchain_result(*_args, **_kwargs):
        return (
            "26.6",
            "17F113",
            "6.3.3",
            {
                "iphoneos": "26.5",
                "iphonesimulator": "26.5",
                "appletvos": "26.5",
                "appletvsimulator": "26.5",
                "macosx": "26.5",
            },
            None,
        )

    def run_plan(self, source: Path, sha: str, execute) -> dict[str, str]:
        state = source.parent / "state"
        state.mkdir()
        with (
            mock.patch.object(
                apple_execution,
                "verify_toolchain",
                side_effect=self.toolchain_result,
            ),
            mock.patch.object(apple_execution, "_execute_command", side_effect=execute),
        ):
            return execute_protected_full(
                self.build(sha),
                source_root=source,
                state_root=state,
                environment={},
            )

    def test_ignored_generated_workspace_state_does_not_count_as_source_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source, sha = self.make_source(Path(temporary))

            def execute(*_args, **_kwargs):
                user_state = source / "App.xcworkspace/xcuserdata/ci.xcuserdatad"
                package_state = source / "App.xcworkspace/xcshareddata/swiftpm"
                user_state.mkdir(parents=True)
                package_state.mkdir(parents=True)
                (user_state / "UserInterfaceState.xcuserstate").write_text(
                    "generated\n",
                    encoding="utf-8",
                )
                (package_state / "Package.resolved").write_text(
                    '{"pins":[]}\n',
                    encoding="utf-8",
                )
                return False

            result = self.run_plan(source, sha, execute)
            self.assertEqual(result["result"], "success")
            self.assertEqual(
                self.git(source, "status", "--porcelain=v1", "--untracked-files=all"),
                "",
            )

    def test_tracked_workspace_file_mutation_still_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source, sha = self.make_source(Path(temporary))

            def execute(*_args, **_kwargs):
                (source / "App.xcworkspace/contents.xcworkspacedata").write_text(
                    "<Workspace version=\"2.0\"/>\n",
                    encoding="utf-8",
                )
                return False

            with self.assertRaisesRegex(AppleValidationError, "source_mutation"):
                self.run_plan(source, sha, execute)

    def test_nonignored_untracked_workspace_state_remains_dirty_source(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            source, sha = self.make_source(Path(temporary))

            def execute(*_args, **_kwargs):
                (source / "App.xcworkspace/unreviewed-state.txt").write_text(
                    "not ignored\n",
                    encoding="utf-8",
                )
                return False

            with self.assertRaisesRegex(AppleValidationError, "dirty_source"):
                self.run_plan(source, sha, execute)


if __name__ == "__main__":
    unittest.main()
