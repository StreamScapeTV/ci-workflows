from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from ci_workflows.evidence import build_evidence, redact_text, write_evidence
from ci_workflows.foundation_types import FoundationError

ROOT = Path(__file__).resolve().parents[1]


class EvidencePrimitiveTests(unittest.TestCase):
    def test_evidence_is_deterministic_bounded_and_canonical(self) -> None:
        values = {
            "source_sha": "a" * 40,
            "workflow_release": "foundation-v1.0.0",
            "runner_profile": "portable",
            "toolchain": {"python": "3.13.5", "git": "2.47.3"},
            "command_profile": "repository-policy",
            "result": "success",
            "cleanup_state": "success",
            "cleanup_removed_paths": 12,
            "contract_root": ROOT,
        }
        first = build_evidence(**values)
        second = build_evidence(**values)
        self.assertEqual(first.evidence_id, second.evidence_id)
        self.assertEqual(first.json_text, second.json_text)
        self.assertEqual(json.loads(first.json_text), first.payload)
        self.assertTrue(first.evidence_id.startswith("evidence-"))
        self.assertTrue(first.payload["redacted"])
        self.assertEqual(list(first.payload["toolchain"]), ["git", "python"])

    def test_redaction_removes_token_url_runner_path_and_secret_assignment(self) -> None:
        token = "github_pat_" + "C" * 60
        raw = (
            "API_TOKEN=private "
            + token
            + " https://private.example.test/api "
            + "/home/runner/work/private/file "
            + r"C:\runner\private\file"
        )
        redacted = redact_text(raw, contract_root=ROOT)
        self.assertNotIn(token, redacted)
        self.assertNotIn("private.example.test", redacted)
        self.assertNotIn("/home/runner", redacted)
        self.assertNotIn(r"C:\runner", redacted)
        self.assertNotIn("API_TOKEN=private", redacted)
        self.assertIn("<redacted-token>", redacted)
        self.assertIn("<redacted-url>", redacted)
        self.assertIn("<redacted-path>", redacted)
        self.assertIn("<redacted-environment>", redacted)

    def test_evidence_rejects_concrete_runner_path_and_unbounded_tool_values(self) -> None:
        with self.assertRaises(FoundationError) as caught:
            build_evidence(
                source_sha="a" * 40,
                workflow_release="foundation-v1.0.0",
                runner_profile="host/path",
                toolchain={},
                command_profile="policy",
                result="success",
                cleanup_state="not-run",
                cleanup_removed_paths=0,
                contract_root=ROOT,
            )
        self.assertEqual(caught.exception.instruction, "invalid_runner_profile")
        with self.assertRaises(FoundationError) as caught:
            build_evidence(
                source_sha="a" * 40,
                workflow_release="foundation-v1.0.0",
                runner_profile="portable",
                toolchain={"python": "x" * 129},
                command_profile="policy",
                result="success",
                cleanup_state="not-run",
                cleanup_removed_paths=0,
                contract_root=ROOT,
            )
        self.assertEqual(caught.exception.instruction, "invalid_evidence_tool_version")

    def test_evidence_is_written_only_under_registered_evidence_root(self) -> None:
        evidence = build_evidence(
            source_sha="a" * 40,
            workflow_release="foundation-v1.0.0",
            runner_profile="portable",
            toolchain={"python": "3.13.5"},
            command_profile="policy",
            result="failure",
            cleanup_state="failure",
            cleanup_removed_paths=0,
            contract_root=ROOT,
        )
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            (state / "evidence").mkdir()
            path = write_evidence(state, evidence)
            self.assertEqual(path, state / "evidence/evidence.json")
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(path.read_text(encoding="utf-8"), evidence.json_text + "\n")
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(FoundationError) as caught:
                write_evidence(Path(directory), evidence)
            self.assertEqual(caught.exception.instruction, "evidence_root_unavailable")


if __name__ == "__main__":
    unittest.main()
