from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ci_workflows.dependencies import checkout_private_dependency
from ci_workflows.foundation_types import FoundationError
from ci_workflows.workspace import WorkspaceContext, cleanup_workspace, prepare_workspace

ROOT = Path(__file__).resolve().parents[1]


class DependencyPrimitiveTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.workspace = self.base / "workspace"
        self.runner_temp = self.base / "runner-temp"
        self.origin = self.base / "origin"
        self.workspace.mkdir()
        self.runner_temp.mkdir()
        subprocess.run(["git", "init", "-q", str(self.origin)], check=True)
        subprocess.run(
            ["git", "-C", str(self.origin), "config", "user.email", "fixture@example.test"],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(self.origin), "config", "user.name", "Fixture"],
            check=True,
        )
        (self.origin / "include").mkdir()
        (self.origin / "include" / "contract.txt").write_text("exact\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(self.origin), "add", "."], check=True)
        subprocess.run(["git", "-C", str(self.origin), "commit", "-qm", "fixture"], check=True)
        self.sha = subprocess.check_output(
            ["git", "-C", str(self.origin), "rev-parse", "HEAD"], text=True
        ).strip()
        self.state = prepare_workspace(
            WorkspaceContext(
                workspace=self.workspace,
                runner_temp=self.runner_temp,
                repository="StreamScapeTV/ci-workflows",
                run_id="200",
                run_attempt=1,
                job="dependency",
                runner_os="Linux",
            ),
            profile="minimal",
            contract_root=ROOT,
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def fake_checkout(self, **values: object) -> dict[str, str]:
        target = Path(values["workspace"]) / str(values["path"])
        subprocess.run(["git", "init", "-q", str(target)], check=True)
        subprocess.run(
            ["git", "-C", str(target), "remote", "add", "origin", str(self.origin)],
            check=True,
        )
        subprocess.run(
            [
                "git", "-C", str(target), "fetch", "-q", "--depth=1", "origin",
                str(values["admitted_sha"]),
            ],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(target), "checkout", "-q", "--detach", "FETCH_HEAD"],
            check=True,
        )
        return {
            "repository": str(values["repository"]),
            "head_sha": str(values["admitted_sha"]),
            "path": str(values["path"]),
            "fetch_depth": str(values["fetch_depth"]),
            "verified": "true",
        }

    def checkout(self, **overrides: object):
        values = {
            "state_root": self.state.root,
            "repository": "StreamScapeTV/private-media",
            "admitted_sha": self.sha,
            "dependency_id": "media-core",
            "expected_subpath": "include",
            "fetch_depth": 1,
            "token": "",
            "contract_root": ROOT,
        }
        values.update(overrides)
        return checkout_private_dependency(**values)

    def test_exact_dependency_is_detached_and_connection_state_is_erased(self) -> None:
        with mock.patch("ci_workflows.dependencies._exact_checkout", side_effect=self.fake_checkout):
            result = self.checkout()
        target = self.state.root / result.relative_path
        self.assertEqual(result.head_sha, self.sha)
        self.assertTrue((target / "include" / "contract.txt").is_file())
        self.assertEqual(
            subprocess.check_output(["git", "-C", str(target), "rev-parse", "HEAD"], text=True).strip(),
            self.sha,
        )
        symbolic = subprocess.run(
            ["git", "-C", str(target), "symbolic-ref", "-q", "HEAD"],
            check=False,
        )
        self.assertNotEqual(symbolic.returncode, 0)
        self.assertEqual(
            subprocess.check_output(["git", "-C", str(target), "remote"], text=True),
            "",
        )
        config = (target / ".git" / "config").read_text(encoding="utf-8")
        self.assertNotIn("origin", config)
        self.assertNotIn("extraheader", config.lower())
        cleanup_workspace(
            self.state.root,
            expected_state_id=self.state.state_id,
            contract_root=ROOT,
        )

    def test_expected_subpath_failure_removes_partial_dependency(self) -> None:
        with mock.patch("ci_workflows.dependencies._exact_checkout", side_effect=self.fake_checkout):
            with self.assertRaises(FoundationError) as caught:
                self.checkout(expected_subpath="missing")
        self.assertEqual(caught.exception.instruction, "dependency_subpath_missing")
        self.assertFalse((self.state.root / "dependencies/media-core").exists())

    def test_credential_residue_is_detected_and_removed(self) -> None:
        token = "ghp_" + "A" * 40

        def checkout_with_residue(**values: object) -> dict[str, str]:
            result = self.fake_checkout(**values)
            target = Path(values["workspace"]) / str(values["path"])
            subprocess.run(
                ["git", "-C", str(target), "config", "foundation.fixture", token],
                check=True,
            )
            return result

        with mock.patch(
            "ci_workflows.dependencies._exact_checkout",
            side_effect=checkout_with_residue,
        ):
            with self.assertRaises(FoundationError) as caught:
                self.checkout(token=token)
        self.assertEqual(caught.exception.instruction, "dependency_credential_residue")
        self.assertFalse((self.state.root / "dependencies/media-core").exists())

    def test_mutable_sha_unapproved_repository_and_path_escape_fail_closed(self) -> None:
        with mock.patch("ci_workflows.dependencies._exact_checkout") as checkout:
            for values, expected in (
                ({"admitted_sha": "main"}, "dependency_sha_must_be_full_sha"),
                ({"repository": "external/example"}, "dependency_repository_not_approved"),
            ):
                with self.subTest(values=values):
                    with self.assertRaises(FoundationError) as caught:
                        self.checkout(**values)
                    self.assertEqual(caught.exception.instruction, expected)
            checkout.assert_not_called()
        with mock.patch("ci_workflows.dependencies._exact_checkout", side_effect=self.fake_checkout):
            with self.assertRaises(FoundationError) as caught:
                self.checkout(expected_subpath="../escape")
        self.assertEqual(caught.exception.instruction, "invalid_dependency_subpath")
        self.assertFalse((self.state.root / "dependencies/media-core").exists())


if __name__ == "__main__":
    unittest.main()
