from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from ci_workflows.foundation_types import FoundationError
from ci_workflows.policy import scan_tracked_repository

ROOT = Path(__file__).resolve().parents[1]


class BearerMarkerRepositoryPolicyTests(unittest.TestCase):
    def scan(self, content: str) -> tuple[int, int]:
        with tempfile.TemporaryDirectory() as raw:
            repository = Path(raw)
            subprocess.run(["git", "init", "-q", str(repository)], check=True)
            subprocess.run(
                ["git", "-C", str(repository), "config", "user.email", "fixture@example.test"],
                check=True,
            )
            subprocess.run(
                ["git", "-C", str(repository), "config", "user.name", "Fixture"],
                check=True,
            )
            (repository / "value.py").write_text(content, encoding="utf-8")
            subprocess.run(["git", "-C", str(repository), "add", "value.py"], check=True)
            subprocess.run(
                ["git", "-C", str(repository), "commit", "-qm", "fixture"],
                check=True,
            )
            return scan_tracked_repository(
                repository,
                repository="StreamScapeTV/example",
                contract_root=ROOT,
            )

    def test_protocol_syntax_and_reviewed_placeholders_are_not_credentials(self) -> None:
        marker = "Authorization" + ": Bearer "
        for value in (
            "{token}",
            "<token>",
            "",
            "TOKEN",
            "synthetic-access-token",
        ):
            with self.subTest(value=value):
                self.assertEqual(self.scan(f'value = "{marker}{value}"\n'), (1, 1))

    def test_credential_shaped_suffixes_still_fail_closed(self) -> None:
        marker = "Authorization" + ": Bearer "
        for credential in (
            "A1b2C3d4",
            "opaque_" + "C" * 32,
        ):
            with self.subTest(length=len(credential)):
                with self.assertRaises(FoundationError) as caught:
                    self.scan(f'value = "{marker}{credential}"\n')
                self.assertEqual(caught.exception.instruction, "tracked_secret_detected")


if __name__ == "__main__":
    unittest.main()
