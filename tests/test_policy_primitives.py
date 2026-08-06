from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from ci_workflows.foundation_types import FoundationError
from ci_workflows.policy import (
    ArtifactDeclaration,
    scan_tracked_repository,
    validate_artifacts,
    validate_cache_request,
    verify_clean_tree,
    verify_generated_outputs,
    verify_repository_policy,
)

ROOT = Path(__file__).resolve().parents[1]


class PolicyPrimitiveTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.repo = Path(self.temp.name) / "repo"
        self.repo.mkdir()
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        subprocess.run(
            ["git", "-C", str(self.repo), "config", "user.email", "fixture@example.test"],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(self.repo), "config", "user.name", "Fixture"],
            check=True,
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def commit(self, relative: str, content: str = "safe\n") -> Path:
        path = self.repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        subprocess.run(["git", "-C", str(self.repo), "add", relative], check=True)
        subprocess.run(["git", "-C", str(self.repo), "commit", "-qm", relative], check=True)
        return path

    def test_clean_tree_tracked_scan_and_zero_artifacts_pass(self) -> None:
        self.commit("src/value.txt")
        report = verify_repository_policy(
            self.repo,
            repository="StreamScapeTV/example",
            phase="after",
            artifact_manifest_json="[]",
            artifact_exception_id=None,
            trust_mode="untrusted-validation",
            contract_root=ROOT,
        )
        self.assertEqual(report.tracked_files, 1)
        self.assertEqual(report.scanned_files, 1)
        self.assertEqual(report.artifact_count, 0)
        self.assertTrue(report.evidence_id.startswith("policy-"))

    def test_dirty_tree_fails_for_tracked_and_untracked_changes(self) -> None:
        path = self.commit("value.txt")
        path.write_text("changed\n", encoding="utf-8")
        with self.assertRaises(FoundationError) as caught:
            verify_clean_tree(self.repo)
        self.assertEqual(caught.exception.instruction, "repository_tree_dirty")
        subprocess.run(["git", "-C", str(self.repo), "checkout", "--", "value.txt"], check=True)
        (self.repo / "untracked.txt").write_text("new\n", encoding="utf-8")
        with self.assertRaises(FoundationError) as caught:
            verify_clean_tree(self.repo)
        self.assertEqual(caught.exception.instruction, "repository_tree_dirty")

    def test_generated_output_drift_fails_closed(self) -> None:
        path = self.commit(
            "docs/architecture/foundation-primitives.md",
            "generated baseline\n",
        )
        self.assertEqual(verify_generated_outputs(self.repo, contract_root=ROOT), 2)
        path.write_text("generated drift\n", encoding="utf-8")
        with self.assertRaises(FoundationError) as caught:
            verify_generated_outputs(self.repo, contract_root=ROOT)
        self.assertEqual(caught.exception.instruction, "generated_output_dirty")

    def test_forbidden_file_and_token_like_content_fail_closed(self) -> None:
        self.commit(".env", "EXAMPLE=value\n")
        with self.assertRaises(FoundationError) as caught:
            scan_tracked_repository(
                self.repo,
                repository="StreamScapeTV/example",
                contract_root=ROOT,
            )
        self.assertEqual(caught.exception.instruction, "forbidden_tracked_file")

        other = Path(self.temp.name) / "token-repo"
        other.mkdir()
        subprocess.run(["git", "init", "-q", str(other)], check=True)
        subprocess.run(["git", "-C", str(other), "config", "user.email", "fixture@example.test"], check=True)
        subprocess.run(["git", "-C", str(other), "config", "user.name", "Fixture"], check=True)
        token = "ghp_" + "B" * 40
        (other / "value.txt").write_text("token=" + token + "\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(other), "add", "value.txt"], check=True)
        subprocess.run(["git", "-C", str(other), "commit", "-qm", "token"], check=True)
        with self.assertRaises(FoundationError) as caught:
            scan_tracked_repository(
                other,
                repository="StreamScapeTV/example",
                contract_root=ROOT,
            )
        self.assertEqual(caught.exception.instruction, "tracked_secret_detected")

    def test_tracked_symlink_escape_is_rejected(self) -> None:
        outside = Path(self.temp.name) / "outside.txt"
        outside.write_text("outside\n", encoding="utf-8")
        link = self.repo / "linked.txt"
        link.symlink_to(outside)
        subprocess.run(["git", "-C", str(self.repo), "add", "linked.txt"], check=True)
        subprocess.run(["git", "-C", str(self.repo), "commit", "-qm", "link"], check=True)
        with self.assertRaises(FoundationError) as caught:
            scan_tracked_repository(
                self.repo,
                repository="StreamScapeTV/example",
                contract_root=ROOT,
            )
        self.assertEqual(caught.exception.instruction, "tracked_symlink_escape")

    def test_undeclared_artifacts_and_unknown_exceptions_fail_closed(self) -> None:
        artifact = ArtifactDeclaration(name="diagnostic", size_bytes=100, retention_days=1)
        with self.assertRaises(FoundationError) as caught:
            validate_artifacts(
                [artifact],
                exception_id=None,
                trust_mode="untrusted-validation",
                contract_root=ROOT,
            )
        self.assertEqual(caught.exception.instruction, "undeclared_artifact")
        with self.assertRaises(FoundationError) as caught:
            validate_artifacts(
                [artifact],
                exception_id="unknown-exception",
                trust_mode="untrusted-validation",
                contract_root=ROOT,
            )
        self.assertEqual(caught.exception.instruction, "artifact_exception_not_registered")
        self.assertIsNone(
            validate_artifacts(
                [],
                exception_id=None,
                trust_mode="untrusted-validation",
                contract_root=ROOT,
            )
        )
        accepted = ArtifactDeclaration(
            name="android-validation-diagnostics",
            size_bytes=1024,
            retention_days=1,
        )
        self.assertEqual(
            validate_artifacts(
                [accepted],
                exception_id="android-validation-diagnostics",
                trust_mode="untrusted-validation",
                contract_root=ROOT,
            ),
            "android-validation-diagnostics",
        )
        with self.assertRaises(FoundationError) as caught:
            validate_artifacts(
                [accepted, accepted],
                exception_id="android-validation-diagnostics",
                trust_mode="trusted-validation",
                contract_root=ROOT,
            )
        self.assertEqual(caught.exception.instruction, "artifact_exception_limit_exceeded")
        forbidden_name = ArtifactDeclaration(
            name="unbounded-log",
            size_bytes=1,
            retention_days=1,
        )
        with self.assertRaises(FoundationError) as caught:
            validate_artifacts(
                [forbidden_name],
                exception_id="android-validation-diagnostics",
                trust_mode="trusted-validation",
                contract_root=ROOT,
            )
        self.assertEqual(caught.exception.instruction, "artifact_exception_name_forbidden")

    def test_cache_disabled_default_and_poisoning_boundaries(self) -> None:
        disabled = validate_cache_request(
            mode="disabled",
            repository="StreamScapeTV/example",
            source_sha=None,
            lock_digest=None,
            platform="linux",
            profile="node",
            trust_mode=None,
            contract_root=ROOT,
        )
        self.assertFalse(disabled.restore)
        self.assertFalse(disabled.save)
        self.assertIsNone(disabled.key)
        restored = validate_cache_request(
            mode="restore-only",
            repository="StreamScapeTV/example",
            source_sha="a" * 40,
            lock_digest="b" * 64,
            platform="linux",
            profile="node",
            trust_mode="untrusted-validation",
            contract_root=ROOT,
        )
        self.assertTrue(restored.restore)
        self.assertFalse(restored.save)
        self.assertTrue(restored.key and restored.key.startswith("cache-"))
        with self.assertRaises(FoundationError) as caught:
            validate_cache_request(
                mode="read-write",
                repository="StreamScapeTV/example",
                source_sha="a" * 40,
                lock_digest="b" * 64,
                platform="linux",
                profile="node",
                trust_mode="untrusted-validation",
                contract_root=ROOT,
            )
        self.assertEqual(caught.exception.instruction, "cache_trust_scope_forbidden")
        with self.assertRaises(FoundationError) as caught:
            validate_cache_request(
                mode="restore-only",
                repository="StreamScapeTV/example",
                source_sha="main",
                lock_digest="b" * 64,
                platform="linux",
                profile="node",
                trust_mode="untrusted-validation",
                contract_root=ROOT,
            )
        self.assertEqual(caught.exception.instruction, "cache_key_material_required")


if __name__ == "__main__":
    unittest.main()
