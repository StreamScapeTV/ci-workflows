from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from ci_workflows.foundation_types import FoundationError
from ci_workflows.policy import scan_tracked_repository

ROOT = Path(__file__).resolve().parents[1]


class BearerMarkerPolicyTests(unittest.TestCase):
    def make_repo(self, content: str) -> Path:
        directory = Path(tempfile.mkdtemp())
        subprocess.run(["git", "init", "-q", str(directory)], check=True)
        subprocess.run(["git", "-C", str(directory), "config", "user.email", "fixture@example.test"], check=True)
        subprocess.run(["git", "-C", str(directory), "config", "user.name", "Fixture"], check=True)
        (directory / "value.py").write_text(content, encoding="utf-8")
        subprocess.run(["git", "-C", str(directory), "add", "value.py"], check=True)
        subprocess.run(["git", "-C", str(directory), "commit", "-qm", "fixture"], check=True)
        return directory

    def test_protocol_syntax_and_placeholders_are_not_credentials(self) -> None:
        for content in (
            'header = f"Authorization: Bearer {token}"\n',
            'example = "Authorization: Bearer <token>"\n',
            'prefix = "Authorization: Bearer "\n',
        ):
            with self.subTest(content=content):
                repository = self.make_repo(content)
                self.assertEqual(
                    scan_tracked_repository(
                        repository,
                        repository="StreamScapeTV/example",
                        contract_root=ROOT,
                    ),
                    (1, 1),
                )

    def test_hard_coded_bearer_credential_still_fails_closed(self) -> None:
        marker = "Authorization" + ": Bearer "
        credential = "opaque_" + "C" * 32
        repository = self.make_repo(f'value = "{marker}{credential}"\n')
        with self.assertRaises(FoundationError) as caught:
            scan_tracked_repository(
                repository,
                repository="StreamScapeTV/example",
                contract_root=ROOT,
            )
        self.assertEqual(caught.exception.instruction, "tracked_secret_detected")


if __name__ == "__main__":
    unittest.main()
