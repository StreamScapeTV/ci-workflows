from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from ci_workflows.foundation_types import FoundationError
from ci_workflows.policy import scan_tracked_repository

ROOT = Path(__file__).resolve().parents[1]


class PrivateKeyPolicyTests(unittest.TestCase):
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

    def commit(self, content: str) -> None:
        path = self.repo / "fixture.txt"
        path.write_text(content, encoding="utf-8")
        subprocess.run(["git", "-C", str(self.repo), "add", "fixture.txt"], check=True)
        subprocess.run(["git", "-C", str(self.repo), "commit", "-qm", "fixture"], check=True)

    def scan(self) -> tuple[int, int]:
        return scan_tracked_repository(
            self.repo,
            repository="StreamScapeTV/example",
            contract_root=ROOT,
        )

    def test_synthetic_private_key_placeholders_pass_but_key_material_fails(self) -> None:
        begin = "-----BEGIN " + "PRIVATE KEY-----"
        end = "-----END " + "PRIVATE KEY-----"
        synthetic_payload = "c3ludGhldGlj"

        self.commit(f'value = "{begin}\\n{synthetic_payload}\\n{end}"\n')
        self.assertEqual(self.scan(), (1, 1))

        self.commit(f"{begin}\n{synthetic_payload}\n{end}\n")
        self.assertEqual(self.scan(), (1, 1))

        credential_payload = "M" * 48
        self.commit(f'value = "{begin}\\n{credential_payload}\\n{end}"\n')
        with self.assertRaises(FoundationError) as caught:
            self.scan()
        self.assertEqual(caught.exception.instruction, "tracked_secret_detected")

        self.commit(f"{begin}\n{credential_payload}\n{end}\n")
        with self.assertRaises(FoundationError) as caught:
            self.scan()
        self.assertEqual(caught.exception.instruction, "tracked_secret_detected")


if __name__ == "__main__":
    unittest.main()
