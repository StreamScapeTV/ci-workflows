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
                "linux",
                "amd64",
                "general",
                "small",
            ],
            environment={},
            stdout=output,
            stderr=errors,
        )
        self.assertEqual(0, code)
        self.assertEqual("general-small\n", output.getvalue())
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

    def test_bare_general_runner_is_rejected_as_ambiguous(self) -> None:
        errors = io.StringIO()
        code = ciw.main(
            [
                "--root",
                str(ROOT),
                "runners",
                "validate-selector",
                "linux",
                "amd64",
                "general",
            ],
            environment={},
            stdout=io.StringIO(),
            stderr=errors,
        )
        self.assertEqual(2, code)
        self.assertIn("ambiguous-general", errors.getvalue())

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
                '["linux","amd64","general","small"]',
            )
            self.assertEqual(values["artifact_exception_used"], "false")
            self.assertNotIn("callback", output.read_text(encoding="utf-8"))

    def test_gitops_plan_dispatch_resolves_contract_owned_runner(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "output"
            environment = {
                "GITHUB_OUTPUT": str(output),
                "GITHUB_REPOSITORY": "StreamScapeTV/ci-workflows",
                "GITHUB_EVENT_NAME": "push",
                "INPUT_ADMITTED_SHA": "e" * 40,
                "INPUT_VALIDATION_PROFILE": "full",
                "INPUT_CONSUMER_CONTRACT": "synthetic",
                "INPUT_POLICY_SCRIPT_PROFILE": "synthetic-policy",
            }
            errors = io.StringIO()
            code = ciw.main(
                [
                    "--root",
                    str(ROOT),
                    "gitops",
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
                '["linux","amd64","general","small"]',
            )
            self.assertNotIn("callback", output.read_text(encoding="utf-8"))

    def test_flutter_plan_dispatch_resolves_contract_owned_mobile_runner(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "output"
            environment = {
                "GITHUB_OUTPUT": str(output),
                "GITHUB_REPOSITORY": "StreamScapeTV/ci-workflows",
                "GITHUB_EVENT_NAME": "push",
                "INPUT_ADMITTED_SHA": "c" * 40,
                "INPUT_VALIDATION_PROFILE": "android-debug",
                "INPUT_COMMAND_PROFILE": "synthetic-smoke",
                "INPUT_PLATFORM": "android",
                "INPUT_SOURCE_TRUST": "trusted-pr",
            }
            errors = io.StringIO()
            code = ciw.main(
                [
                    "--root",
                    str(ROOT),
                    "flutter",
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
            self.assertEqual(values["runner_profile"], "mobile")
            self.assertEqual(
                values["runs_on_json"],
                '["linux","amd64","mobile"]',
            )
            self.assertNotIn("callback", output.read_text(encoding="utf-8"))

    def test_flutter_plan_dispatch_resolves_contract_owned_apple_runner(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "output"
            environment = {
                "GITHUB_OUTPUT": str(output),
                "GITHUB_REPOSITORY": "StreamScapeTV/ci-workflows",
                "GITHUB_EVENT_NAME": "push",
                "INPUT_ADMITTED_SHA": "d" * 40,
                "INPUT_VALIDATION_PROFILE": "ios-simulator",
                "INPUT_COMMAND_PROFILE": "synthetic-smoke",
                "INPUT_PLATFORM": "ios-simulator",
                "INPUT_SOURCE_TRUST": "trusted-pr",
            }
            errors = io.StringIO()
            code = ciw.main(
                [
                    "--root",
                    str(ROOT),
                    "flutter",
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
            self.assertEqual(values["runner_profile"], "apple")
            self.assertEqual(values["runs_on_json"], '["macOS","ARM64"]')
            self.assertNotIn("callback", output.read_text(encoding="utf-8"))

    def test_node_plan_dispatch_resolves_exact_runtime_and_portable_runner(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "output"
            environment = {
                "GITHUB_OUTPUT": str(output),
                "GITHUB_REPOSITORY": "StreamScapeTV/StreamScapeWeb",
                "GITHUB_EVENT_NAME": "push",
                "INPUT_ADMITTED_SHA": "b" * 40,
                "INPUT_VALIDATION_PROFILE": "locked-node",
                "INPUT_VERSION_FILE": ".nvmrc",
                "INPUT_WORKING_DIRECTORY": ".",
                "INPUT_INSTALL_PROFILE": "npm-ci",
                "INPUT_COMMAND_PROFILE": "quality-test",
                "INPUT_PUBLIC_ENVIRONMENT": "{}",
            }
            errors = io.StringIO()
            code = ciw.main(
                [
                    "--root",
                    str(ROOT),
                    "node",
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
            self.assertEqual(values["node_version"], "22.18.0")
            self.assertEqual(values["runner_profile"], "portable")
            self.assertEqual(
                values["runs_on_json"],
                '["linux","amd64","general","small"]',
            )
            self.assertEqual(values["artifact_exception_used"], "false")
            serialized = output.read_text(encoding="utf-8")
            self.assertNotIn("NEXT_PUBLIC", serialized)
            self.assertNotIn("callback", serialized)

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
            with mock.patch.object(
                ciw,
                "exact_checkout",
                return_value=expected,
            ) as checkout:
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
                mock.patch.object(
                    ciw,
                    "event_from_environment",
                    return_value=object(),
                ),
                mock.patch.object(
                    ciw,
                    "_release_provider",
                    return_value=object(),
                ),
                mock.patch.object(
                    ciw,
                    "resolve_release_authority",
                    return_value=authority,
                ),
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
            mock.patch.dict(
                "os.environ",
                {"INPUT_OPERATION": "verify-set"},
                clear=False,
            ),
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

    def test_scripts_and_actions_are_compatibility_delegates(self) -> None:
        for script in (
            "scripts/ci/resolve_source.py",
            "scripts/ci/runner_contract.py",
            "scripts/ci/foundation.py",
            "scripts/ci/release_tag_authority.py",
            "scripts/ci/python.py",
            "scripts/ci/node.py",
            "scripts/ci/android.py",
            "scripts/ci/gitops.py",
        ):
            source = (ROOT / script).read_text(encoding="utf-8")
            self.assertTrue(
                "ciw" in source
                or "ci_workflows.source" in source
                or "foundation_cli" in source,
                script,
            )
        actions = sorted((ROOT / "actions").glob("*/action.yml"))
        self.assertTrue(actions)
        special_adapters = {
            "validate-android-live-service": "scripts/ci/android_completion.py",
            "validate-android-release": "scripts/ci/android_completion.py",
            "resolve-execution-backend": "scripts/ci/execution_backend.py",
        }
        for action in actions:
            source = action.read_text(encoding="utf-8")
            expected_adapter = special_adapters.get(
                action.parent.name,
                "scripts/ci/ciw.py",
            )
            self.assertIn(expected_adapter, source, action.as_posix())

    def test_unknown_command_is_rejected_by_argparse(self) -> None:
        with self.assertRaises(SystemExit) as caught:
            ciw.parser().parse_args(["source", "caller-selected-handler"])
        self.assertEqual(2, caught.exception.code)


if __name__ == "__main__":
    unittest.main()
