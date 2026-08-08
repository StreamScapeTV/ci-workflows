from __future__ import annotations

import dataclasses
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ci_workflows import node as node_validation
from ci_workflows import node_execution

ROOT = Path(__file__).resolve().parents[1]
SHA = "a" * 40


def request(
    *,
    repository: str = "StreamScapeTV/StreamScapeWeb",
    profile: str = "next-static-export",
    command: str = "quality-test-build",
    version_file: str | None = ".nvmrc",
    node_version: str | None = None,
    working_directory: str = ".",
    install_profile: str = "npm-ci",
    output: str | None = "out",
    verifier: str | None = "scripts/verify-cloudflare-pages-output.ts",
    public_environment: dict[str, str] | None = None,
    trust: str = "trusted-exact",
) -> node_validation.NodeValidationRequest:
    return node_validation.NodeValidationRequest(
        repository=repository,
        admitted_sha=SHA,
        validation_profile=profile,
        version_file=version_file,
        node_version=node_version,
        working_directory=working_directory,
        install_profile=install_profile,
        command_profile=command,
        script_path="tool/ci_quality_gate.sh" if command == "source-audit" else None,
        static_output_directory=output,
        output_verifier_path=verifier,
        public_environment=public_environment or {},
        artifact_exception_id=None,
        source_trust=trust,
    )


def initialize_repository(root: Path) -> str:
    (root / "scripts").mkdir()
    (root / ".nvmrc").write_text("22.18.0\n", encoding="utf-8")
    (root / "package.json").write_text(
        json.dumps(
            {
                "name": "fixture",
                "engines": {
                    "node": ">=22.18.0 <23",
                    "npm": ">=10.9.2 <11",
                },
                "scripts": {
                    "verify:ci-artifacts": "node scripts/noop.mjs",
                    "lint": "node scripts/noop.mjs",
                    "typecheck": "node scripts/noop.mjs",
                    "test": "node scripts/noop.mjs",
                    "build": "node scripts/noop.mjs",
                },
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    (root / "package-lock.json").write_text(
        json.dumps(
            {
                "name": "fixture",
                "lockfileVersion": 3,
                "packages": {"": {"name": "fixture"}},
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    (root / "scripts/noop.mjs").write_text("process.exit(0);\n", encoding="utf-8")
    (root / "scripts/verify-cloudflare-pages-output.ts").write_text(
        "process.exit(0);\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=root, check=True)
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "fixture"], cwd=root, check=True)
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    ).stdout.strip()


class NodeValidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.contract = node_validation.load_node_contract(ROOT)

    def test_public_environment_is_bounded_and_profile_scoped(self) -> None:
        environment = {
            "GITHUB_REPOSITORY": "StreamScapeTV/StreamScapeWeb",
            "GITHUB_EVENT_NAME": "push",
            "INPUT_ADMITTED_SHA": SHA,
            "INPUT_VALIDATION_PROFILE": "next-static-export",
            "INPUT_VERSION_FILE": ".nvmrc",
            "INPUT_WORKING_DIRECTORY": ".",
            "INPUT_INSTALL_PROFILE": "npm-ci",
            "INPUT_COMMAND_PROFILE": "quality-test-build",
            "INPUT_STATIC_OUTPUT_DIRECTORY": "out",
            "INPUT_OUTPUT_VERIFIER_PATH": "scripts/verify-cloudflare-pages-output.ts",
            "INPUT_PUBLIC_ENVIRONMENT": json.dumps(
                {"NEXT_PUBLIC_API_URL": "https://example.invalid"}
            ),
        }
        value = node_validation.request_from_environment(environment, self.contract)
        plan = node_validation.resolve_validation_plan(self.contract, value)
        self.assertEqual(
            plan.public_environment,
            {"NEXT_PUBLIC_API_URL": "https://example.invalid"},
        )
        invalid = dict(environment)
        invalid["INPUT_PUBLIC_ENVIRONMENT"] = json.dumps(
            {"NEXT_PUBLIC_PROJECT": "not-allowed-for-web"}
        )
        with self.assertRaisesRegex(
            node_validation.NodeValidationError,
            "public_environment_rejected",
        ):
            node_validation.resolve_validation_plan(
                self.contract,
                node_validation.request_from_environment(invalid, self.contract),
            )
        for bad in (
            {"NEXT_PUBLIC_API_URL": "${{ secrets.TOKEN }}"},
            {"NEXT_PUBLIC_API_URL": "ghp_abcdefghijklmnopqrstuvwxyz123456"},
            {"UNKNOWN_PUBLIC": "value"},
            {"NEXT_PUBLIC_API_URL": "line1\nline2"},
        ):
            contaminated = dict(environment)
            contaminated["INPUT_PUBLIC_ENVIRONMENT"] = json.dumps(bad)
            with self.assertRaisesRegex(
                node_validation.NodeValidationError,
                "public_environment_rejected",
            ):
                node_validation.request_from_environment(
                    contaminated,
                    self.contract,
                )

    def test_version_sources_are_mutually_exclusive_and_exact(self) -> None:
        base = {
            "GITHUB_REPOSITORY": "StreamScapeTV/agent-state",
            "GITHUB_EVENT_NAME": "push",
            "INPUT_ADMITTED_SHA": SHA,
            "INPUT_VALIDATION_PROFILE": "frontend-contract-static",
            "INPUT_WORKING_DIRECTORY": "frontend",
            "INPUT_INSTALL_PROFILE": "npm-ci",
            "INPUT_COMMAND_PROFILE": "contract-test-build",
            "INPUT_STATIC_OUTPUT_DIRECTORY": "out",
            "INPUT_PUBLIC_ENVIRONMENT": "{}",
        }
        for updates in (
            {},
            {"INPUT_NODE_VERSION": ">=22"},
            {"INPUT_NODE_VERSION": "22.18.0", "INPUT_VERSION_FILE": ".nvmrc"},
        ):
            environment = {**base, **updates}
            with self.assertRaisesRegex(
                node_validation.NodeValidationError,
                "invalid_runtime_source",
            ):
                node_validation.request_from_environment(
                    environment,
                    self.contract,
                )
        value = node_validation.request_from_environment(
            {**base, "INPUT_NODE_VERSION": "22.18.0"},
            self.contract,
        )
        self.assertEqual(value.node_version, "22.18.0")

    def test_static_output_digest_is_deterministic_and_rejects_malformed_output(self) -> None:
        plan = node_validation.resolve_validation_plan(
            self.contract,
            request(public_environment={"NEXT_PUBLIC_API_URL": "https://example.invalid"}),
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "out"
            output.mkdir()
            (output / "index.html").write_text("<html></html>\n", encoding="utf-8")
            (output / "asset.js").write_text("console.log(1);\n", encoding="utf-8")
            first = node_validation.verify_static_output(
                root,
                plan,
                self.contract["output_limits"],
            )
            second = node_validation.verify_static_output(
                root,
                plan,
                self.contract["output_limits"],
            )
            self.assertEqual(first, second)
            self.assertRegex(first or "", r"^[0-9a-f]{64}$")
            (output / "_worker.js").write_text("worker\n", encoding="utf-8")
            with self.assertRaisesRegex(
                node_validation.NodeValidationError,
                "output_malformed",
            ):
                node_validation.verify_static_output(
                    root,
                    plan,
                    self.contract["output_limits"],
                )
            (output / "_worker.js").unlink()
            (output / "index.html").unlink()
            with self.assertRaisesRegex(
                node_validation.NodeValidationError,
                "output_malformed",
            ):
                node_validation.verify_static_output(
                    root,
                    plan,
                    self.contract["output_limits"],
                )

    def test_output_missing_empty_symlink_and_server_bundle_fail_closed(self) -> None:
        plan = node_validation.resolve_validation_plan(
            self.contract,
            request(public_environment={"NEXT_PUBLIC_API_URL": "https://example.invalid"}),
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaisesRegex(
                node_validation.NodeValidationError,
                "output_missing",
            ):
                node_validation.verify_static_output(
                    root,
                    plan,
                    self.contract["output_limits"],
                )
            (root / "out").mkdir()
            with self.assertRaisesRegex(
                node_validation.NodeValidationError,
                "output_malformed",
            ):
                node_validation.verify_static_output(
                    root,
                    plan,
                    self.contract["output_limits"],
                )
            outside = root / "outside.html"
            outside.write_text("outside\n", encoding="utf-8")
            (root / "out/link.html").symlink_to(outside)
            with self.assertRaisesRegex(
                node_validation.NodeValidationError,
                "output_malformed",
            ):
                node_validation.verify_static_output(
                    root,
                    plan,
                    self.contract["output_limits"],
                )
            (root / "out/link.html").unlink()
            (root / "out/server").mkdir()
            (root / "out/server/index.html").write_text("server\n", encoding="utf-8")
            with self.assertRaisesRegex(
                node_validation.NodeValidationError,
                "output_malformed",
            ):
                node_validation.verify_static_output(
                    root,
                    plan,
                    self.contract["output_limits"],
                )

    def test_cleanup_unlinks_generated_symlink_without_following(self) -> None:
        plan = node_validation.resolve_validation_plan(
            self.contract,
            request(public_environment={"NEXT_PUBLIC_API_URL": "https://example.invalid"}),
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = root / "state"
            copy_root = state / "node-validation/source"
            copy_root.mkdir(parents=True)
            outside = root / "outside"
            outside.mkdir()
            sentinel = outside / "sentinel"
            sentinel.write_text("keep\n", encoding="utf-8")
            (copy_root / "node_modules").symlink_to(outside, target_is_directory=True)
            node_execution.cleanup_generated(
                copy_root,
                state,
                plan,
                self.contract["generated_cleanup_names"],
            )
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "keep\n")
            self.assertFalse((state / "node-validation").exists())

    def test_command_failure_is_not_swallowed(self) -> None:
        completed = subprocess.CompletedProcess(["npm", "test"], 9, "", "failed")
        with mock.patch("subprocess.run", return_value=completed):
            with self.assertRaisesRegex(
                node_validation.NodeValidationError,
                "tests_failed",
            ):
                node_execution.run_command(
                    ["npm", "test"],
                    cwd=ROOT,
                    environment={},
                    timeout_seconds=30,
                    code="tests_failed",
                )

    def test_complete_plan_executes_fixed_restore_and_cleans_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source"
            source.mkdir()
            sha = initialize_repository(source)
            value = dataclasses.replace(
                request(public_environment={"NEXT_PUBLIC_API_URL": "https://example.invalid"}),
                admitted_sha=sha,
            )
            plan = node_validation.resolve_validation_plan(self.contract, value)
            state = Path(directory) / "state"
            (state / "node-validation").mkdir(parents=True)
            real_run = subprocess.run
            observed: list[tuple[str, ...]] = []

            def fake_run(
                argv: list[str],
                *,
                cwd: Path,
                env: dict[str, str],
                stdout: object,
                stderr: object,
                text: bool,
                check: bool,
                timeout: int,
            ) -> subprocess.CompletedProcess[str]:
                command = tuple(argv)
                observed.append(command)
                if argv[0] == "git":
                    return real_run(
                        argv,
                        cwd=cwd,
                        env=env,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                        check=False,
                        timeout=timeout,
                    )
                if argv == ["node", "--version"]:
                    return subprocess.CompletedProcess(argv, 0, "v22.18.0\n", "")
                if argv == ["npm", "--version"]:
                    return subprocess.CompletedProcess(argv, 0, "10.9.2\n", "")
                if argv == ["npm", "run", "build"]:
                    output = Path(cwd) / "out"
                    output.mkdir(exist_ok=True)
                    (output / "index.html").write_text("<html></html>\n", encoding="utf-8")
                return subprocess.CompletedProcess(argv, 0, "", "")

            with mock.patch("subprocess.run", side_effect=fake_run):
                result = node_execution.execute_node_plan(
                    source,
                    state,
                    plan,
                    self.contract,
                    {"PATH": os.environ.get("PATH", "")},
                )
            self.assertEqual(result.node_version, "22.18.0")
            self.assertEqual(result.npm_version, "10.9.2")
            self.assertEqual(result.install_result, "success")
            self.assertEqual(result.build_result, "success")
            self.assertTrue(result.output_verified)
            self.assertTrue(result.clean_tree)
            self.assertFalse((state / "node-validation").exists())
            self.assertIn(("npm", "ci", "--no-audit", "--no-fund"), observed)
            self.assertNotIn(("npm", "install"), observed)


if __name__ == "__main__":
    unittest.main()
