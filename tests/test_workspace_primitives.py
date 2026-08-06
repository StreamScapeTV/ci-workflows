from __future__ import annotations

import json
import stat
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

    def test_strict_environment_and_workflow_scoped_root(self) -> None:
        state = self.prepare()
        self.assertEqual(state.profile, "minimal")
        self.assertEqual(state.cache_mode, "disabled")
        self.assertIsNone(state.cache_key)
        self.assertEqual(state.environment["LC_ALL"], "C.UTF-8")
        self.assertEqual(state.environment["TZ"], "UTC")
        self.assertEqual(state.environment["GIT_TERMINAL_PROMPT"], "0")
        self.assertTrue(state.root.is_relative_to(self.runner_temp))
        for variable in (
            "HOME", "TMPDIR", "XDG_CACHE_HOME", "XDG_CONFIG_HOME",
            "XDG_DATA_HOME", "CI_CREDENTIAL_ROOT", "CI_EVIDENCE_ROOT",
            "CI_ARTIFACT_ROOT", "CI_GENERATED_ROOT", "CI_TOOL_ROOT",
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

    def test_symlink_escape_and_malicious_registry_do_not_delete_outside(self) -> None:
        state = self.prepare()
        outside = self.base / "outside"
        outside.mkdir()
        sentinel = outside / "sentinel"
        sentinel.write_text("keep\n", encoding="utf-8")
        evidence = state.root / "evidence"
        evidence.rmdir()
        evidence.symlink_to(outside, target_is_directory=True)
        with self.assertRaises(FoundationError) as caught:
            cleanup_workspace(state.root, expected_state_id=state.state_id, contract_root=ROOT)
        self.assertEqual(caught.exception.instruction, "symlink_escape_detected")
        self.assertTrue(sentinel.exists())
        evidence.unlink()
        evidence.mkdir()

        registry = state.root / ".ci-workflows-registry.json"
        payload = json.loads(registry.read_text(encoding="utf-8"))
        payload["paths"][0]["relative"] = "../../outside"
        registry.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaises(FoundationError) as caught:
            cleanup_workspace(state.root, expected_state_id=state.state_id, contract_root=ROOT)
        self.assertEqual(caught.exception.instruction, "invalid_relative_path")
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

    def test_linux_and_macos_cleanup_remove_read_only_sensitive_state(self) -> None:
        for index, runner_os in enumerate(("Linux", "macOS"), start=1):
            with self.subTest(runner_os=runner_os):
                state = prepare_workspace(
                    self.context(runner_os, run_id=str(100 + index)),
                    profile="full_validation",
                    contract_root=ROOT,
                )
                credential = state.root / "credentials" / "secret"
                credential.write_text("sensitive\n", encoding="utf-8")
                credential.chmod(stat.S_IRUSR)
                cache = state.root / "npm" / "cache" / "nested"
                cache.mkdir()
                (cache / "value").write_text("cache\n", encoding="utf-8")
                cache.chmod(stat.S_IRUSR | stat.S_IXUSR)
                report = cleanup_workspace(
                    state.root,
                    expected_state_id=state.state_id,
                    contract_root=ROOT,
                )
                self.assertEqual(report.platform, runner_os)
                self.assertGreater(report.removed_sensitive_paths, 0)
                self.assertFalse(state.root.exists())

    def test_cleanup_fails_when_registered_residue_cannot_be_removed(self) -> None:
        state = self.prepare()
        with mock.patch("ci_workflows.workspace._remove_tree", return_value=None):
            with self.assertRaises(FoundationError) as caught:
                cleanup_workspace(
                    state.root,
                    expected_state_id=state.state_id,
                    contract_root=ROOT,
                )
        self.assertEqual(caught.exception.instruction, "cleanup_residue_detected")
        self.assertTrue(state.root.exists())

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
