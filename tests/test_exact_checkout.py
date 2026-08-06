from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ci_workflows import source  # noqa: E402


class ExactCheckoutTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.origin_work = self.root / "origin-work"
        self.origin_bare = self.root / "origin.git"
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        subprocess.run(
            ["git", "init", "-q", str(self.origin_work)],
            check=True,
        )
        subprocess.run(
            [
                "git",
                "-C",
                str(self.origin_work),
                "config",
                "user.email",
                "test@example.com",
            ],
            check=True,
        )
        subprocess.run(
            [
                "git",
                "-C",
                str(self.origin_work),
                "config",
                "user.name",
                "Source Tests",
            ],
            check=True,
        )
        (self.origin_work / "value.txt").write_text(
            "one\n",
            encoding="utf-8",
        )
        subprocess.run(
            ["git", "-C", str(self.origin_work), "add", "value.txt"],
            check=True,
        )
        subprocess.run(
            [
                "git",
                "-C",
                str(self.origin_work),
                "commit",
                "-qm",
                "one",
            ],
            check=True,
        )
        self.first = subprocess.check_output(
            ["git", "-C", str(self.origin_work), "rev-parse", "HEAD"],
            text=True,
        ).strip()
        (self.origin_work / "value.txt").write_text(
            "two\n",
            encoding="utf-8",
        )
        subprocess.run(
            [
                "git",
                "-C",
                str(self.origin_work),
                "commit",
                "-qam",
                "two",
            ],
            check=True,
        )
        self.second = subprocess.check_output(
            ["git", "-C", str(self.origin_work), "rev-parse", "HEAD"],
            text=True,
        ).strip()
        subprocess.run(
            [
                "git",
                "clone",
                "-q",
                "--bare",
                str(self.origin_work),
                str(self.origin_bare),
            ],
            check=True,
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def checkout(
        self,
        sha: str,
        path: str = "source",
    ) -> dict[str, str]:
        return dict(
            source.exact_checkout(
                repository="StreamScapeTV/example",
                admitted_sha=sha,
                path=path,
                fetch_depth=1,
                token="",
                workspace=self.workspace,
                remote_url=str(self.origin_bare),
            )
        )

    def test_historical_exact_sha_is_detached_and_verified(self) -> None:
        outputs = self.checkout(self.first)
        target = self.workspace / "source"
        self.assertEqual(outputs["head_sha"], self.first)
        self.assertEqual(outputs["verified"], "true")
        self.assertEqual(
            subprocess.check_output(
                ["git", "-C", str(target), "rev-parse", "HEAD"],
                text=True,
            ).strip(),
            self.first,
        )
        symbolic = subprocess.run(
            ["git", "-C", str(target), "symbolic-ref", "-q", "HEAD"],
            check=False,
        )
        self.assertNotEqual(symbolic.returncode, 0)
        persisted = subprocess.run(
            [
                "git",
                "-C",
                str(target),
                "config",
                "--local",
                "--get-regexp",
                r"http\..*extraheader",
            ],
            stdout=subprocess.PIPE,
            text=True,
            check=False,
        )
        self.assertEqual(persisted.stdout, "")

    def test_mutable_ref_malformed_sha_path_escape_and_changed_checkout_fail(
        self,
    ) -> None:
        for sha, expected in (
            ("main", "admitted_sha_must_be_full_sha"),
            ("refs/heads/main", "admitted_sha_must_be_full_sha"),
        ):
            with self.subTest(sha=sha):
                with self.assertRaises(
                    source.SourceAdmissionError
                ) as caught:
                    self.checkout(sha)
                self.assertEqual(caught.exception.instruction, expected)

        with self.assertRaises(source.SourceAdmissionError) as caught:
            self.checkout(self.first, "../escape")
        self.assertEqual(
            caught.exception.instruction,
            "invalid_checkout_path",
        )

        occupied = self.workspace / "occupied"
        occupied.mkdir()
        (occupied / "changed.txt").write_text(
            "caller state\n",
            encoding="utf-8",
        )
        with self.assertRaises(source.SourceAdmissionError) as caught:
            self.checkout(self.first, "occupied")
        self.assertEqual(
            caught.exception.instruction,
            "checkout_path_not_empty",
        )

    def test_failed_fetch_removes_partial_git_state(self) -> None:
        with self.assertRaises(source.SourceAdmissionError) as caught:
            self.checkout("9" * 40, "failed")
        self.assertEqual(
            caught.exception.instruction,
            "exact_checkout_git_failure",
        )
        self.assertFalse((self.workspace / "failed").exists())

        preserved = self.workspace / "preserved-empty"
        preserved.mkdir()
        with self.assertRaises(source.SourceAdmissionError):
            self.checkout("9" * 40, "preserved-empty")
        self.assertTrue(preserved.is_dir())
        self.assertEqual(list(preserved.iterdir()), [])

    def test_different_exact_commit_never_silently_reuses_prior_checkout(
        self,
    ) -> None:
        self.checkout(self.first)
        with self.assertRaises(source.SourceAdmissionError) as caught:
            self.checkout(self.second)
        self.assertEqual(
            caught.exception.instruction,
            "checkout_path_not_empty",
        )


if __name__ == "__main__":
    unittest.main()
