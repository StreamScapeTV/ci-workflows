from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ci_workflows.foundation_types import FoundationError
from ci_workflows.workspace import WorkspaceContext, cleanup_workspace, prepare_workspace


ROOT = Path(__file__).resolve().parents[1]


class ReleaseCleanupIntegrationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.workspace = self.base / "workspace"
        self.runner_temp = self.base / "runner-temp"
        self.workspace.mkdir()
        self.runner_temp.mkdir()
        self.outside = self.base / "outside-sentinel"
        self.outside.write_text("keep\n", encoding="utf-8")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def prepare_release_workspace(self, *, run_id: str = "19001"):
        return prepare_workspace(
            WorkspaceContext(
                workspace=self.workspace,
                runner_temp=self.runner_temp,
                repository="StreamScapeTV/iptv-backend",
                run_id=run_id,
                run_attempt=1,
                job="publish-images",
                runner_os="Linux",
            ),
            profile="container",
            contract_root=ROOT,
        )

    def test_terminal_cleanup_removes_registry_auth_and_release_evidence(self) -> None:
        state = self.prepare_release_workspace()
        auth_file = Path(state.environment["REGISTRY_AUTH_FILE"])
        auth_file.parent.mkdir(parents=True, exist_ok=True)
        auth_file.write_text(
            '{"auths":{"registry.invalid":{"auth":"credential-material"}}}\n',
            encoding="utf-8",
        )
        evidence = Path(state.environment["CI_EVIDENCE_ROOT"]) / "release-read-back.json"
        evidence.write_text('{"result":"failure-after-publication"}\n', encoding="utf-8")
        generated = Path(state.environment["CI_GENERATED_ROOT"]) / "release-manifest.json"
        generated.write_text('{"partial":true}\n', encoding="utf-8")

        # This is the same cleanup primitive the final release workflow must invoke
        # from an always() terminal job, including failure/partial-publication paths.
        report = cleanup_workspace(
            state.root,
            expected_state_id=state.state_id,
            contract_root=ROOT,
        )

        self.assertFalse(state.root.exists())
        self.assertTrue(self.outside.exists())
        self.assertEqual("keep\n", self.outside.read_text(encoding="utf-8"))
        self.assertGreaterEqual(report.removed_sensitive_paths, 1)
        self.assertEqual("true", report.output_values()["cleanup_verified"])

    def test_cleanup_cannot_be_redirected_to_caller_selected_path(self) -> None:
        state = self.prepare_release_workspace(run_id="19002")
        forged = self.base / "forged-release-root"
        forged.mkdir()
        (forged / "keep").write_text("do-not-delete\n", encoding="utf-8")

        with self.assertRaises(FoundationError):
            cleanup_workspace(
                forged,
                expected_state_id=state.state_id,
                contract_root=ROOT,
            )

        self.assertTrue((forged / "keep").exists())
        self.assertTrue(state.root.exists())
        cleanup_workspace(
            state.root,
            expected_state_id=state.state_id,
            contract_root=ROOT,
        )

    def test_wrong_release_state_id_fails_without_broad_cleanup(self) -> None:
        state = self.prepare_release_workspace(run_id="19003")
        auth_file = Path(state.environment["REGISTRY_AUTH_FILE"])
        auth_file.parent.mkdir(parents=True, exist_ok=True)
        auth_file.write_text("sensitive\n", encoding="utf-8")

        with self.assertRaises(FoundationError):
            cleanup_workspace(
                state.root,
                expected_state_id="different-release-state",
                contract_root=ROOT,
            )

        self.assertTrue(state.root.exists())
        self.assertTrue(auth_file.exists())
        cleanup_workspace(
            state.root,
            expected_state_id=state.state_id,
            contract_root=ROOT,
        )
        self.assertFalse(state.root.exists())


if __name__ == "__main__":
    unittest.main()
