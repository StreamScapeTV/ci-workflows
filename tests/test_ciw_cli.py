from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ci_workflows import ciw, foundation_cli, source_cli
from ci_workflows.release_tag_authority import ReleaseAuthority

ROOT = Path(__file__).resolve().parents[1]


class CIWCLITests(unittest.TestCase):
    def test_registered_runner_commands_dispatch_with_stable_results(self) -> None:
        output = io.StringIO()
        errors = io.StringIO()
        code = ciw.main(
            [
                "--root",
                str(ROOT),
                "runners",
                "validate-selector",
                "portable",
            ],
            environment={},
            stdout=output,
            stderr=errors,
        )
        self.assertEqual(0, code)
        self.assertEqual("portable\n", output.getvalue())
        self.assertEqual("", errors.getvalue())

        output = io.StringIO()
        code = ciw.main(
            [
                "--root",
                str(ROOT),
                "runners",
                "select-buildah-tier",
                "--peak-memory-bytes",
                str(128 * 1024 * 1024),
                "--peak-local-storage-bytes",
                str(1024 * 1024 * 1024),
            ],
            environment={},
            stdout=output,
            stderr=io.StringIO(),
        )
        self.assertEqual(0, code)
        self.assertEqual("buildah-tiny\n", output.getvalue())

    def test_runner_rejection_preserves_stable_code(self) -> None:
        errors = io.StringIO()
        code = ciw.main(
            [
                "--root",
                str(ROOT),
                "runners",
                "validate-selector",
                "self-hosted",
            ],
            environment={},
            stdout=io.StringIO(),
            stderr=errors,
        )
        self.assertEqual(2, code)
        self.assertIn("bare-self-hosted", errors.getvalue())

    def test_python_plan_dispatch_resolves_contract_owned_runner(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "output"
            environment = {
                "GITHUB_OUTPUT": str(output),
                "GITHUB_REPOSITORY": "StreamScapeTV/flux",
                "GITHUB_EVENT_NAME": "push",
                "INPUT_ADMITTED_SHA": "a" * 40,
                "INPUT_VALIDATION_PROFILE": "audit",
                "INPUT_COMMAND_PROFILE": "source-audit",
                "INPUT_WORKING_DIRECTORY": ".",
            }
            errors = io.StringIO()
            code = ciw.main(
                [
                    "--root",
                    str(ROOT),
                    "python",
                    "validate",
                    "--phase",
                    "plan",
                ],
                environment=environment,
                stdout=io.StringIO(),
                stderr=errors,
            )
            self.assertEqual(0, code, errors.getvalue())
            values = dict(
                line.split("=", 1)
                for line in output.read_text(encoding="utf-8").splitlines()
            )
            self.assertEqual(values["result"], "planned")
            self.assertEqual(values["runner_profile"], "portable")
            self.assertEqual(
                values["runs_on_json"],
                '["homelab-portable-linux-x64"]',
            )
            self.assertEqual(values["artifact_exception_used"], "false")
            self.assertNotIn("callback", output.read_text(encoding="utf-8"))

    def test_exact_checkout_dispatch_preserves_action_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "output"
            environment = {
                "GITHUB_OUTPUT": str(output),
                "GITHUB_WORKSPACE": directory,
                "CHECKOUT_TOKEN": "transient",
            }
            expected = {
                "repository": "StreamScapeTV/example",
                "head_sha": "a" * 40,
                "path": "source",
                "fetch_depth": "1",
                "verified": "true",
            }
            with mock.patch.object(ciw, "exact_checkout", return_value=expected) as checkout:
                code = ciw.main(
                    [
                        "--root",
                        str(ROOT),
                        "source",
                        "exact-checkout",
                        "--repository",
                        "StreamScapeTV/example",
                        "--admitted-sha",
                        "a" * 40,
                    ],
                    environment=environment,
                    stdout=io.StringIO(),
                    stderr=io.StringIO(),
                )
            self.assertEqual(0, code)
            checkout.assert_called_once()
            lines = dict(
                line.split("=", 1)
                for line in output.read_text(encoding="utf-8").splitlines()
            )
            self.assertEqual(expected, lines)

    def test_release_tag_dispatch_preserves_exact_five_outputs(self) -> None:
        authority = ReleaseAuthority(
            release_mode="existing-tag",
            release_version="1.2.3",
            release_source_sha="a" * 40,
            tag_object_sha="b" * 40,
            tag_commit_sha="a" * 40,
        )
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "output"
            environment = {
                "GITHUB_OUTPUT": str(output),
                "INPUT_RELEASE_MODE": "existing-tag",
                "INPUT_RELEASE_VERSION": "1.2.3",
                "INPUT_RELEASE_SOURCE_SHA": "a" * 40,
            }
            with (
                mock.patch.object(ciw, "event_from_environment", return_value=object()),
                mock.patch.object(ciw, "_release_provider", return_value=object()),
                mock.patch.object(ciw, "resolve_release_authority", return_value=authority),
            ):
                code = ciw.main(
                    [
                        "--root",
                        str(ROOT),
                        "release-tag",
                        "resolve",
                    ],
                    environment=environment,
                    stdout=io.StringIO(),
                    stderr=io.StringIO(),
                )
            self.assertEqual(0, code)
            lines = dict(
                line.split("=", 1)
                for line in output.read_text(encoding="utf-8").splitlines()
            )
            self.assertEqual(
                lines,
                {
                    "release_mode": "existing-tag",
                    "release_version": "1.2.3",
                    "release_source_sha": "a" * 40,
                    "tag_object_sha": "b" * 40,
                    "tag_commit_sha": "a" * 40,
                },
            )

    def test_legacy_module_wrappers_delegate_to_the_same_registry(self) -> None:
        with mock.patch("ci_workflows.ciw.main", return_value=0) as dispatch:
            self.assertEqual(
                0,
                source_cli.main(
                    [
                        "exact-checkout",
                        "--repository",
                        "StreamScapeTV/example",
                        "--admitted-sha",
                        "a" * 40,
                    ]
                ),
            )
            translated = dispatch.call_args.args[0]
            self.assertIn("source", translated)
            self.assertIn("exact-checkout", translated)

        with (
            mock.patch.dict("os.environ", {"INPUT_OPERATION": "verify-set"}, clear=False),
            mock.patch("ci_workflows.ciw.main", return_value=0) as dispatch,
        ):
            self.assertEqual(
                0,
                foundation_cli.main(
                    [
                        "--root",
                        str(ROOT),
                        "verify-toolchain",
                    ]
                ),
            )
            self.assertEqual(
                [
                    "--root",
                    str(ROOT),
                    "tooling",
                    "verify",
                ],
                dispatch.call_args.args[0],
            )

    def test_scripts_and_ten_actions_are_compatibility_delegates(self) -> None:
        for script in (
            "scripts/ci/resolve_source.py",
            "scripts/ci/runner_contract.py",
            "scripts/ci/foundation.py",
            "scripts/ci/release_tag_authority.py",
            "scripts/ci/python.py",
        ):
            source = (ROOT / script).read_text(encoding="utf-8")
            self.assertTrue(
                "ciw" in source or "ci_workflows.source" in source or "foundation_cli" in source,
                script,
            )
        actions = sorted((ROOT / "actions").glob("*/action.yml"))
        self.assertEqual(10, len(actions))
        for action in actions:
            source = action.read_text(encoding="utf-8")
            self.assertIn("scripts/ci/ciw.py", source, action.as_posix())

    def test_unknown_command_is_rejected_by_argparse(self) -> None:
        with self.assertRaises(SystemExit) as caught:
            ciw.parser().parse_args(["source", "caller-selected-handler"])
        self.assertEqual(2, caught.exception.code)


if __name__ == "__main__":
    unittest.main()
