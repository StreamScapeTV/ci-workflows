from __future__ import annotations

import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ci_workflows.runtime_primitives import (
    RuntimePrimitiveError,
    canonical_json,
    checkout_exact_repository,
    create_temporary_workspace,
    finalize_temporary_path,
    finalize_temporary_paths,
    github_output_values,
    normalize_input,
    run_process,
    secret_environment,
    write_github_outputs,
)


class ProcessPrimitiveTests(unittest.TestCase):
    def test_success_uses_explicit_cwd_environment_stdin_and_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            cwd = Path(directory).resolve()
            result = run_process(
                [
                    sys.executable,
                    "-c",
                    (
                        "import os,pathlib,sys;"
                        "print(os.environ['VISIBLE']);"
                        "print(sys.stdin.read());"
                        "print(pathlib.Path.cwd().name)"
                    ),
                ],
                cwd=cwd,
                environment={"VISIBLE": "yes"},
                stdin="payload",
                timeout_seconds=5,
            )
        self.assertTrue(result.ok)
        self.assertFalse(result.timed_out)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stderr, "")
        self.assertEqual(
            result.stdout.splitlines(),
            ["yes", "payload", cwd.name],
        )

    def test_nonzero_exit_is_a_structured_result(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result = run_process(
                [
                    sys.executable,
                    "-c",
                    "import sys; print('out'); print('err', file=sys.stderr); sys.exit(7)",
                ],
                cwd=Path(directory).resolve(),
                environment={},
            )
        self.assertFalse(result.ok)
        self.assertFalse(result.timed_out)
        self.assertEqual(result.returncode, 7)
        self.assertEqual(result.stdout, "out\n")
        self.assertEqual(result.stderr, "err\n")

    def test_timeout_is_a_structured_result(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            result = run_process(
                [sys.executable, "-c", "import time; time.sleep(1)"],
                cwd=Path(directory).resolve(),
                environment={},
                timeout_seconds=0.05,
            )
        self.assertFalse(result.ok)
        self.assertTrue(result.timed_out)
        self.assertIsNone(result.returncode)


class InputAndSecretPrimitiveTests(unittest.TestCase):
    def test_non_secret_input_prefers_argument_then_environment_then_default(self) -> None:
        environment = {"INPUT_MODE": "  full  "}
        self.assertEqual(
            normalize_input("mode", environment=environment, choices=("full", "fast")),
            "full",
        )
        self.assertEqual(
            normalize_input(
                "mode",
                argument=" fast ",
                environment=environment,
                choices=("full", "fast"),
            ),
            "fast",
        )
        self.assertEqual(
            normalize_input("other", environment=environment, default="default"),
            "default",
        )

    def test_non_secret_input_rejects_missing_choice_and_multiline_values(self) -> None:
        with self.assertRaises(RuntimePrimitiveError) as caught:
            normalize_input("mode", environment={}, required=True)
        self.assertEqual(caught.exception.code, "input_required")
        with self.assertRaises(RuntimePrimitiveError) as caught:
            normalize_input(
                "mode",
                argument="arbitrary",
                environment={},
                choices=("full", "fast"),
            )
        self.assertEqual(caught.exception.code, "input_choice_invalid")
        with self.assertRaises(RuntimePrimitiveError) as caught:
            normalize_input("mode", argument="bad\nvalue", environment={})
        self.assertEqual(caught.exception.code, "input_value_invalid")

    def test_named_secret_lookup_returns_value_without_error_text_leakage(self) -> None:
        self.assertEqual(
            secret_environment(
                "REGISTRY_TOKEN",
                environment={"REGISTRY_TOKEN": "secret-value"},
            ),
            "secret-value",
        )
        with self.assertRaises(RuntimePrimitiveError) as caught:
            secret_environment(
                "REGISTRY_TOKEN",
                environment={"OTHER_SECRET": "do-not-log"},
            )
        self.assertEqual(caught.exception.code, "secret_required")
        self.assertNotIn("do-not-log", str(caught.exception))


class WorkspaceAndCleanupPrimitiveTests(unittest.TestCase):
    def test_workspace_and_auth_state_cleanup_is_bounded_and_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory).resolve()
            workspace = create_temporary_workspace(parent, prefix="runtime")
            self.assertEqual(workspace.parent, parent)
            self.assertEqual(stat.S_IMODE(workspace.stat().st_mode), 0o700)
            auth = workspace / "auth.json"
            auth.write_text("temporary", encoding="utf-8")
            nested = workspace / "nested"
            nested.mkdir()
            (nested / "state").write_text("temporary", encoding="utf-8")

            self.assertEqual(
                finalize_temporary_paths([workspace, auth], root=parent),
                2,
            )
            self.assertFalse(workspace.exists())
            self.assertEqual(
                finalize_temporary_paths([workspace, auth], root=parent),
                0,
            )

    def test_cleanup_unlinks_target_symlink_without_following_it(self) -> None:
        with (
            tempfile.TemporaryDirectory() as directory,
            tempfile.TemporaryDirectory() as outside_directory,
        ):
            root = Path(directory).resolve()
            outside = Path(outside_directory).resolve() / "keep.txt"
            outside.write_text("keep", encoding="utf-8")
            link = root / "auth-link"
            link.symlink_to(outside)
            self.assertTrue(finalize_temporary_path(link, root=root))
            self.assertFalse(link.exists())
            self.assertEqual(outside.read_text(encoding="utf-8"), "keep")

    def test_cleanup_rejects_escape(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            outside = root.parent / "outside-runtime-state"
            with self.assertRaises(RuntimePrimitiveError) as caught:
                finalize_temporary_path(outside, root=root)
        self.assertEqual(caught.exception.code, "cleanup_target_outside_root")


class CheckoutPrimitiveTests(unittest.TestCase):
    def test_exact_checkout_delegates_to_existing_credential_safe_implementation(self) -> None:
        sha = "a" * 40
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory).resolve()
            with mock.patch(
                "ci_workflows.runtime_primitives.exact_checkout",
                return_value={
                    "repository": "StreamScapeTV/example",
                    "head_sha": sha,
                    "path": "source",
                    "fetch_depth": "2",
                    "verified": "true",
                },
            ) as checkout:
                result = checkout_exact_repository(
                    "StreamScapeTV/example",
                    sha,
                    workspace=workspace,
                    fetch_depth=2,
                    token_environment="CHECKOUT_TOKEN",
                    environment={"CHECKOUT_TOKEN": "transient-secret"},
                )
        self.assertTrue(result.verified)
        self.assertEqual(result.head_sha, sha)
        self.assertEqual(result.fetch_depth, 2)
        checkout.assert_called_once_with(
            repository="StreamScapeTV/example",
            admitted_sha=sha,
            path="source",
            fetch_depth=2,
            token="transient-secret",
            workspace=workspace,
        )


class OutputPrimitiveTests(unittest.TestCase):
    def test_canonical_json_and_github_output_serialization(self) -> None:
        self.assertEqual(
            canonical_json({"b": 2, "a": [1, True]}),
            '{"a":[1,true],"b":2}',
        )
        values = github_output_values(
            {
                "plain": "ok",
                "structured": {"b": 2, "a": [1, True]},
                "number": 3,
            }
        )
        self.assertEqual(values["plain"], "ok")
        self.assertEqual(values["structured"], '{"a":[1,true],"b":2}')
        self.assertEqual(values["number"], "3")

    def test_write_github_outputs_appends_only_single_line_values(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory).resolve() / "github-output"
            output.touch()
            values = write_github_outputs(
                output,
                {"result": "passed", "details": {"count": 2}},
            )
            self.assertEqual(values["details"], '{"count":2}')
            self.assertEqual(
                output.read_text(encoding="utf-8"),
                'result=passed\ndetails={"count":2}\n',
            )
        with self.assertRaises(RuntimePrimitiveError) as caught:
            github_output_values({"result": "bad\nvalue"})
        self.assertEqual(caught.exception.code, "github_output_value_invalid")


if __name__ == "__main__":
    unittest.main()
