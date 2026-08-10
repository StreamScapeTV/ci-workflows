"""Regression coverage for Android exact-source status verification."""
from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ci_workflows import android_execution
from ci_workflows.android_types import AndroidValidationError


class AndroidSourceBoundaryTests(unittest.TestCase):
    def test_pre_execution_status_uses_only_the_isolated_git_environment(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / "isolated-home"
            home.mkdir()
            inherited = {
                "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                "HOME": str(home),
                "GIT_CONFIG_NOSYSTEM": "0",
                "GIT_CONFIG_GLOBAL": str(home / "host-config"),
            }
            completed = subprocess.CompletedProcess(
                ["git", "status"],
                0,
                " M tracked.txt\n?? generated.txt\n",
                "",
            )
            with mock.patch.object(
                android_execution,
                "run_command",
                return_value=completed,
            ) as runner:
                status = android_execution.pre_execution_status(
                    Path(directory),
                    inherited,
                )

            self.assertEqual(status, (" M tracked.txt", "?? generated.txt"))
            environment = runner.call_args.kwargs["environment"]
            self.assertEqual(environment["HOME"], str(home))
            self.assertEqual(environment["GIT_CONFIG_NOSYSTEM"], "1")
            self.assertEqual(environment["GIT_CONFIG_GLOBAL"], os.devnull)
            self.assertEqual(environment["GIT_TERMINAL_PROMPT"], "0")
            self.assertEqual(environment["LANG"], "C")
            self.assertEqual(environment["LC_ALL"], "C")
            self.assertNotEqual(
                environment["GIT_CONFIG_GLOBAL"],
                inherited["GIT_CONFIG_GLOBAL"],
            )

    def test_global_excludes_cannot_hide_an_untracked_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository = root / "repository"
            home = root / "host-home"
            repository.mkdir()
            home.mkdir()
            environment = dict(os.environ)
            environment.setdefault("PATH", "/usr/bin:/bin")
            environment["HOME"] = str(home)

            def git(*args: str) -> str:
                result = subprocess.run(
                    ["git", *args],
                    cwd=repository,
                    env=environment,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                return result.stdout.strip()

            git("init", "--quiet")
            git("config", "user.name", "Android Boundary Test")
            git("config", "user.email", "android-boundary@example.invalid")
            tracked = repository / "tracked.txt"
            tracked.write_text("tracked\n", encoding="utf-8")
            git("add", "tracked.txt")
            git("commit", "--quiet", "-m", "initial")
            head = git("rev-parse", "HEAD")

            excludes = root / "global-excludes"
            excludes.write_text("dirty.txt\n", encoding="utf-8")
            (home / ".gitconfig").write_text(
                "[core]\n"
                f"    excludesFile = {excludes}\n",
                encoding="utf-8",
            )
            dirty = repository / "dirty.txt"
            dirty.write_text("must remain visible\n", encoding="utf-8")

            status = android_execution.pre_execution_status(
                repository,
                environment,
            )
            self.assertEqual(status, ("?? dirty.txt",))
            with self.assertRaises(AndroidValidationError) as failure:
                android_execution.verify_exact_source(
                    repository,
                    head,
                    environment,
                )
            self.assertEqual(failure.exception.code, "dirty_tree")

            dirty.unlink()
            self.assertEqual(
                android_execution.pre_execution_status(repository, environment),
                (),
            )
            android_execution.verify_exact_source(repository, head, environment)

            tracked.write_text("mutated\n", encoding="utf-8")
            tracked_status = android_execution.pre_execution_status(
                repository,
                environment,
            )
            self.assertEqual(tracked_status, (" M tracked.txt",))

    def test_missing_or_relative_isolated_home_fails_closed(self) -> None:
        with self.assertRaises(AndroidValidationError) as missing:
            android_execution.isolated_git_environment({"PATH": "/usr/bin"})
        self.assertEqual(missing.exception.code, "invalid_input")

        with self.assertRaises(AndroidValidationError) as relative:
            android_execution.isolated_git_environment(
                {"PATH": "/usr/bin", "HOME": "relative-home"}
            )
        self.assertEqual(relative.exception.code, "invalid_input")


if __name__ == "__main__":
    unittest.main()
