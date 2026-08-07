from __future__ import annotations

import json
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ci_workflows.foundation_types import FoundationError
from ci_workflows.workspace import (
    WorkspaceContext,
    cleanup_workspace,
    prepare_workspace,
    register_state_path,
    resolve_state_root,
)

ROOT = Path(__file__).resolve().parents[1]


class WorkspacePrimitiveTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.workspace = self.base / "workspace"
        self.runner_temp = self.base / "runner-temp"
        self.workspace.mkdir()
        self.runner_temp.mkdir()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def context(self, runner_os: str = "Linux", run_id: str = "100") -> WorkspaceContext:
        return WorkspaceContext(
            workspace=self.workspace,
            runner_temp=self.runner_temp,
            repository="StreamScapeTV/example",
            run_id=run_id,
            run_attempt=1,
            job="validate",
            runner_os=runner_os,
        )

    def prepare(self, runner_os: str = "Linux", profile: str = "minimal"):
        return prepare_workspace(
            self.context(runner_os),
            profile=profile,
            contract_root=ROOT,
        )

    def prepare_read_only_parent(
        self,
        *,
        runner_os: str,
        run_id: str,
    ):
        state = prepare_workspace(
            self.context(runner_os, run_id=run_id),
            profile="full_validation",
            contract_root=ROOT,
        )
        parent = state.root / "npm" / "cache" / "nested"
        parent.mkdir()
        value = parent / "value"
        value.write_text("cache\n", encoding="utf-8")
        value.chmod(stat.S_IRUSR)
        parent.chmod(stat.S_IRUSR | stat.S_IXUSR)
        return state

    def test_strict_environment_and_workflow_scoped_root(self) -> None:
        state = self.prepare()
        self.assertEqual(state.profile, "minimal")
        self.assertEqual(state.cache_mode, "disabled")
        self.assertIsNone(state.cache_key)
        self.assertEqual(state.environment["LC_ALL"], "C.UTF-8")
        self.assertEqual(state.environment["TZ"], "UTC")
        self.assertEqual(state.environment["GIT_TERMINAL_PROMPT"], "0")
        self.assertTrue(state.root.is_relative_to(self.runner_temp.resolve()))
        for variable in (
            "HOME",
            "TMPDIR",
            "XDG_CACHE_HOME",
            "XDG_CONFIG_HOME",
            "XDG_DATA_HOME",
            "CI_CREDENTIAL_ROOT",
            "CI_EVIDENCE_ROOT",
            "CI_ARTIFACT_ROOT",
            "CI_GENERATED_ROOT",
            "CI_TOOL_ROOT",
            "CI_DEPENDENCY_ROOT",
        ):
            self.assertTrue(Path(state.environment[variable]).is_dir())
            self.assertTrue(Path(state.environment[variable]).is_relative_to(state.root))
        report = cleanup_workspace(
            state.root,
            expected_state_id=state.state_id,
            contract_root=ROOT,
        )
        self.assertFalse(state.root.exists())
        self.assertFalse(report.partial_setup)

    def test_profile_paths_are_bounded_and_cache_is_opt_in(self) -> None:
        state = prepare_workspace(
            self.context(),
            profile="node",
            cache_mode="restore-only",
            source_sha="a" * 40,
            lock_digest="b" * 64,
            trust_mode="untrusted-validation",
            contract_root=ROOT,
        )
        self.assertTrue(Path(state.environment["npm_config_cache"]).is_dir())
        self.assertTrue(Path(state.environment["npm_config_prefix"]).is_dir())
        self.assertEqual(state.cache_mode, "restore-only")
        self.assertTrue(state.cache_key and state.cache_key.startswith("cache-"))
        cleanup_workspace(state.root, expected_state_id=state.state_id, contract_root=ROOT)

    def test_state_root_is_derived_from_protected_runner_temp(self) -> None:
        state = self.prepare()
        self.assertEqual(
            resolve_state_root(
                runner_temp=self.runner_temp.resolve(),
                state_id=state.state_id,
                declared_root=str(state.root),
                contract_root=ROOT,
            ),
            state.root,
        )
        outside = self.base / "forged-root"
        outside.mkdir()
        with self.assertRaises(FoundationError) as caught:
            resolve_state_root(
                runner_temp=self.runner_temp.resolve(),
                state_id=state.state_id,
                declared_root=str(outside),
                contract_root=ROOT,
            )
        self.assertEqual(
            caught.exception.instruction,
            "workspace_root_environment_mismatch",
        )
        self.assertTrue(outside.exists())
        cleanup_workspace(
            state.root,
            expected_state_id=state.state_id,
            contract_root=ROOT,
        )

    def test_path_traversal_and_duplicate_registration_fail_closed(self) -> None:
        state = self.prepare()
        with self.assertRaises(FoundationError) as caught:
            register_state_path(
                state.root,
                name="escape",
                relative="../escape",
                kind="artifact",
                contract_root=ROOT,
            )
        self.assertEqual(caught.exception.instruction, "invalid_relative_path")
        register_state_path(
            state.root,
            name="report",
            relative="artifacts/report",
            kind="artifact",
            contract_root=ROOT,
        )
        with self.assertRaises(FoundationError) as caught:
            register_state_path(
                state.root,
                name="report",
                relative="artifacts/other",
                kind="artifact",
                contract_root=ROOT,
            )
        self.assertEqual(caught.exception.instruction, "duplicate_registered_path")
        cleanup_workspace(state.root, expected_state_id=state.state_id, contract_root=ROOT)

    def test_parent_escape_registration_is_rejected(self) -> None:
        state = self.prepare()
        with self.assertRaises(FoundationError) as caught:
            register_state_path(
                state.root,
                name="parent-escape",
                relative="artifacts/../../outside",
                kind="artifact",
                contract_root=ROOT,
            )
        self.assertEqual(caught.exception.instruction, "invalid_relative_path")
        cleanup_workspace(state.root, expected_state_id=state.state_id, contract_root=ROOT)

    def test_registered_symlink_file_and_directory_escape_are_rejected(self) -> None:
        outside_file = self.base / "outside-file"
        outside_file.write_text("keep\n", encoding="utf-8")
        outside_directory = self.base / "outside-directory"
        outside_directory.mkdir()
        (outside_directory / "sentinel").write_text("keep\n", encoding="utf-8")

        for index, target in enumerate((outside_file, outside_directory), start=1):
            with self.subTest(target=target.name):
                state = prepare_workspace(
                    self.context("Linux", run_id=str(300 + index)),
                    profile="minimal",
                    contract_root=ROOT,
                )
                untouched = state.root / "credentials" / "untouched"
                untouched.write_text("keep\n", encoding="utf-8")
                evidence = state.root / "evidence"
                evidence.rmdir()
                evidence.symlink_to(
                    target,
                    target_is_directory=target.is_dir(),
                )
                with self.assertRaises(FoundationError) as caught:
                    cleanup_workspace(
                        state.root,
                        expected_state_id=state.state_id,
                        contract_root=ROOT,
                    )
                self.assertEqual(caught.exception.instruction, "symlink_escape_detected")
                self.assertTrue(untouched.exists())
                self.assertTrue(target.exists())
                evidence.unlink()
                evidence.mkdir()
                cleanup_workspace(
                    state.root,
                    expected_state_id=state.state_id,
                    contract_root=ROOT,
                )

    def test_internal_symlinks_are_unlinked_without_following_or_chmod_outside(self) -> None:
        state = self.prepare(profile="minimal")
        outside_file = self.base / "outside-file-mode"
        outside_file.write_text("outside\n", encoding="utf-8")
        outside_file.chmod(stat.S_IRUSR)
        outside_directory = self.base / "outside-directory-mode"
        outside_directory.mkdir()
        outside_sentinel = outside_directory / "sentinel"
        outside_sentinel.write_text("outside\n", encoding="utf-8")
        outside_directory.chmod(stat.S_IRUSR | stat.S_IXUSR)
        file_mode = stat.S_IMODE(outside_file.stat().st_mode)
        directory_mode = stat.S_IMODE(outside_directory.stat().st_mode)

        artifacts = state.root / "artifacts"
        (artifacts / "file-link").symlink_to(outside_file)
        (artifacts / "directory-link").symlink_to(
            outside_directory,
            target_is_directory=True,
        )
        cleanup_workspace(
            state.root,
            expected_state_id=state.state_id,
            contract_root=ROOT,
        )
        self.assertTrue(outside_file.exists())
        self.assertTrue(outside_sentinel.exists())
        self.assertEqual(stat.S_IMODE(outside_file.stat().st_mode), file_mode)
        self.assertEqual(
            stat.S_IMODE(outside_directory.stat().st_mode),
            directory_mode,
        )
        outside_directory.chmod(stat.S_IRWXU)
        outside_file.chmod(stat.S_IRUSR | stat.S_IWUSR)

    def test_malicious_registry_entry_is_rejected_before_any_deletion(self) -> None:
        state = self.prepare()
        untouched = state.root / "credentials" / "untouched"
        untouched.write_text("keep\n", encoding="utf-8")
        outside = self.base / "outside-registry"
        outside.mkdir()
        sentinel = outside / "sentinel"
        sentinel.write_text("keep\n", encoding="utf-8")

        registry = state.root / ".ci-workflows-registry.json"
        payload = json.loads(registry.read_text(encoding="utf-8"))
        payload["paths"][-1]["relative"] = "../../outside-registry"
        registry.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaises(FoundationError) as caught:
            cleanup_workspace(
                state.root,
                expected_state_id=state.state_id,
                contract_root=ROOT,
            )
        self.assertEqual(caught.exception.instruction, "invalid_relative_path")
        self.assertTrue(untouched.exists())
        self.assertTrue(sentinel.exists())

    def test_interrupted_partial_setup_is_safely_terminalized(self) -> None:
        state = self.prepare()
        (state.root / ".ci-workflows-registry.json").unlink()
        report = cleanup_workspace(
            state.root,
            expected_state_id=state.state_id,
            contract_root=ROOT,
        )
        self.assertTrue(report.partial_setup)
        self.assertFalse(state.root.exists())

    def test_failed_caller_command_still_cleans_registered_state(self) -> None:
        state = self.prepare()
        credential = state.root / "credentials" / "command-token"
        credential.write_text("transient\n", encoding="utf-8")
        completed = subprocess.run(
            [sys.executable, "-c", "raise SystemExit(7)"],
            check=False,
        )
        self.assertEqual(completed.returncode, 7)
        report = cleanup_workspace(
            state.root,
            expected_state_id=state.state_id,
            contract_root=ROOT,
        )
        self.assertGreater(report.removed_sensitive_paths, 0)
        self.assertFalse(state.root.exists())

    def test_linux_read_only_file_beneath_read_only_parent_is_removed(self) -> None:
        state = self.prepare_read_only_parent(
            runner_os="Linux",
            run_id="401",
        )
        report = cleanup_workspace(
            state.root,
            expected_state_id=state.state_id,
            contract_root=ROOT,
        )
        self.assertEqual(report.platform, "Linux")
        self.assertFalse(state.root.exists())

    def test_macos_read_only_file_beneath_read_only_parent_is_removed(self) -> None:
        state = self.prepare_read_only_parent(
            runner_os="macOS",
            run_id="402",
        )
        report = cleanup_workspace(
            state.root,
            expected_state_id=state.state_id,
            contract_root=ROOT,
        )
        self.assertEqual(report.platform, "macOS")
        self.assertFalse(state.root.exists())

    def test_multiple_nested_read_only_directories_are_removed(self) -> None:
        state = prepare_workspace(
            self.context("Linux", run_id="403"),
            profile="full_validation",
            contract_root=ROOT,
        )
        first = state.root / "npm" / "cache" / "one"
        second = first / "two"
        third = second / "three"
        third.mkdir(parents=True)
        value = third / "value"
        value.write_text("nested\n", encoding="utf-8")
        value.chmod(stat.S_IRUSR)
        for directory in (third, second, first):
            directory.chmod(stat.S_IRUSR | stat.S_IXUSR)
        cleanup_workspace(
            state.root,
            expected_state_id=state.state_id,
            contract_root=ROOT,
        )
        self.assertFalse(state.root.exists())

    def test_read_only_registered_sensitive_state_is_removed(self) -> None:
        state = self.prepare()
        credentials = state.root / "credentials"
        secret = credentials / "secret"
        secret.write_text("sensitive\n", encoding="utf-8")
        secret.chmod(stat.S_IRUSR)
        credentials.chmod(stat.S_IRUSR | stat.S_IXUSR)
        report = cleanup_workspace(
            state.root,
            expected_state_id=state.state_id,
            contract_root=ROOT,
        )
        self.assertGreater(report.removed_sensitive_paths, 0)
        self.assertFalse(state.root.exists())

    def test_genuine_cleanup_failure_still_reports_residue(self) -> None:
        state = self.prepare()
        (state.root / "credentials" / "blocked").write_text(
            "blocked\n",
            encoding="utf-8",
        )
        with mock.patch(
            "ci_workflows.workspace.os.unlink",
            side_effect=PermissionError("blocked unlink"),
        ):
            with self.assertRaises(FoundationError) as caught:
                cleanup_workspace(
                    state.root,
                    expected_state_id=state.state_id,
                    contract_root=ROOT,
                )
        self.assertEqual(caught.exception.instruction, "cleanup_residue_detected")
        self.assertTrue(state.root.exists())
        cleanup_workspace(
            state.root,
            expected_state_id=state.state_id,
            contract_root=ROOT,
        )

    def test_state_identity_mismatch_prevents_cleanup(self) -> None:
        state = self.prepare()
        with self.assertRaises(FoundationError) as caught:
            cleanup_workspace(
                state.root,
                expected_state_id="workspace-wrongidentity",
                contract_root=ROOT,
            )
        self.assertEqual(caught.exception.instruction, "workspace_state_id_mismatch")
        cleanup_workspace(state.root, expected_state_id=state.state_id, contract_root=ROOT)


if __name__ == "__main__":
    unittest.main()
