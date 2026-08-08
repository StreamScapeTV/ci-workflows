from __future__ import annotations

import contextlib
import dataclasses
import io
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
        script_path=(
            "tool/ci_quality_gate.sh"
            if command == "source-audit"
            else None
        ),
        static_output_directory=output,
        output_verifier_path=verifier,
        public_environment=public_environment or {},
        artifact_exception_id=None,
        source_trust="trusted-exact",
    )


def initialize_direct_version_repository(root: Path) -> str:
    frontend = root / "frontend"
    frontend.mkdir(parents=True)
    (frontend / "package.json").write_text(
        json.dumps(
            {
                "name": "frontend-fixture",
                "engines": {
                    "node": ">=22.18.0 <23",
                    "npm": ">=10.9.2 <11",
                },
                "scripts": {
                    "test": "node --test",
                    "build:pages": "node build.mjs",
                },
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    (frontend / "package-lock.json").write_text(
        json.dumps(
            {
                "name": "frontend-fixture",
                "lockfileVersion": 3,
                "packages": {"": {"name": "frontend-fixture"}},
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    (frontend / "build.mjs").write_text(
        "process.exit(0);\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.invalid"],
        cwd=root,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=root,
        check=True,
    )
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(
        ["git", "commit", "-qm", "fixture"],
        cwd=root,
        check=True,
    )
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    ).stdout.strip()


def cleanup_layout(root: Path) -> tuple[Path, Path]:
    state = root / "state"
    copy_root = state / "node-validation" / "source"
    copy_root.mkdir(parents=True)
    return state, copy_root


class NodeRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.contract = node_validation.load_node_contract(ROOT)

    def web_plan(
        self,
        public_environment: dict[str, str] | None = None,
    ) -> node_validation.NodeValidationPlan:
        return node_validation.resolve_validation_plan(
            self.contract,
            request(
                public_environment=public_environment
                or {"NEXT_PUBLIC_API_URL": "https://example.invalid"}
            ),
        )

    def test_direct_exact_api_version_executes_without_a_version_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source"
            source.mkdir()
            sha = initialize_direct_version_repository(source)
            value = dataclasses.replace(
                request(
                    repository="StreamScapeTV/agent-state",
                    profile="frontend-contract-static",
                    command="contract-test-build",
                    version_file=None,
                    node_version="22.18.0",
                    working_directory="frontend",
                    output="out",
                    verifier=None,
                    public_environment={
                        "NEXT_PUBLIC_API_BASE_URL": "http://127.0.0.1:7878",
                        "NEXT_PUBLIC_PROJECT": "iptv-apple",
                    },
                ),
                admitted_sha=sha,
            )
            plan = node_validation.resolve_validation_plan(
                self.contract,
                value,
            )
            self.assertIsNone(plan.version_file)
            self.assertEqual(plan.version_authority, "exact-api")
            state = Path(directory) / "state"
            state.mkdir()
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
                observed.append(tuple(argv))
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
                    return subprocess.CompletedProcess(
                        argv,
                        0,
                        "v22.18.0\n",
                        "",
                    )
                if argv == ["npm", "--version"]:
                    return subprocess.CompletedProcess(
                        argv,
                        0,
                        "10.9.2\n",
                        "",
                    )
                if argv == ["npm", "run", "build:pages"]:
                    output = Path(cwd) / "out"
                    output.mkdir(exist_ok=True)
                    (output / "index.html").write_text(
                        "<html></html>\n",
                        encoding="utf-8",
                    )
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
            self.assertFalse((state / "node-validation").exists())
            self.assertIn(
                ("npm", "ci", "--no-audit", "--no-fund"),
                observed,
            )

    def test_public_environment_is_consumer_and_profile_scoped(self) -> None:
        web = self.web_plan(
            {"NEXT_PUBLIC_API_URL": "https://example.invalid"}
        )
        self.assertEqual(
            set(web.public_environment),
            {"NEXT_PUBLIC_API_URL"},
        )
        with self.assertRaisesRegex(
            node_validation.NodeValidationError,
            "public_environment_rejected",
        ):
            self.web_plan({"NEXT_PUBLIC_PROJECT": "not-web"})

        agent_request = request(
            repository="StreamScapeTV/agent-state",
            profile="frontend-contract-static",
            command="contract-test-build",
            version_file=None,
            node_version="22.18.0",
            working_directory="frontend",
            output="out",
            verifier=None,
            public_environment={
                "NEXT_PUBLIC_API_BASE_URL": "http://127.0.0.1:7878",
                "NEXT_PUBLIC_PROJECT": "iptv-apple",
            },
        )
        agent = node_validation.resolve_validation_plan(
            self.contract,
            agent_request,
        )
        self.assertEqual(
            set(agent.public_environment),
            {"NEXT_PUBLIC_API_BASE_URL", "NEXT_PUBLIC_PROJECT"},
        )
        with self.assertRaisesRegex(
            node_validation.NodeValidationError,
            "public_environment_rejected",
        ):
            node_validation.resolve_validation_plan(
                self.contract,
                dataclasses.replace(
                    agent_request,
                    public_environment={
                        "NEXT_PUBLIC_API_URL": "https://not-agent.invalid"
                    },
                ),
            )

        audit = request(
            repository="StreamScapeTV/finance-hub",
            profile="node-source-audit",
            command="source-audit",
            version_file=".node-version",
            node_version=None,
            install_profile="none",
            output=None,
            verifier=None,
        )
        with self.assertRaisesRegex(
            node_validation.NodeValidationError,
            "public_environment_rejected",
        ):
            node_validation.resolve_validation_plan(
                self.contract,
                dataclasses.replace(
                    audit,
                    public_environment={
                        "NEXT_PUBLIC_PROJECT": "forbidden-for-audit"
                    },
                ),
            )

    def test_public_environment_rejects_unknown_control_token_and_context(self) -> None:
        base = {
            "GITHUB_REPOSITORY": "StreamScapeTV/StreamScapeWeb",
            "GITHUB_EVENT_NAME": "push",
            "INPUT_ADMITTED_SHA": SHA,
            "INPUT_VALIDATION_PROFILE": "next-static-export",
            "INPUT_VERSION_FILE": ".nvmrc",
            "INPUT_WORKING_DIRECTORY": ".",
            "INPUT_INSTALL_PROFILE": "npm-ci",
            "INPUT_COMMAND_PROFILE": "quality-test-build",
            "INPUT_STATIC_OUTPUT_DIRECTORY": "out",
            "INPUT_OUTPUT_VERIFIER_PATH": (
                "scripts/verify-cloudflare-pages-output.ts"
            ),
        }
        for bad in (
            {"UNKNOWN_PUBLIC": "value"},
            {"NEXT_PUBLIC_API_URL": "line1\nline2"},
            {"NEXT_PUBLIC_API_URL": "control\u0001value"},
            {
                "NEXT_PUBLIC_API_URL": (
                    "ghp_abcdefghijklmnopqrstuvwxyz123456"
                )
            },
            {"NEXT_PUBLIC_API_URL": "${{ secrets.TOKEN }}"},
            {"NEXT_PUBLIC_API_URL": "${{ github.event.repository.url }}"},
        ):
            with self.subTest(bad=bad):
                contaminated = {
                    **base,
                    "INPUT_PUBLIC_ENVIRONMENT": json.dumps(bad),
                }
                with self.assertRaisesRegex(
                    node_validation.NodeValidationError,
                    "public_environment_rejected",
                ):
                    node_validation.request_from_environment(
                        contaminated,
                        self.contract,
                    )

    def test_public_environment_is_not_logged_projected_or_persisted(self) -> None:
        public_value = "https://public.example.invalid/browser"
        plan = self.web_plan({"NEXT_PUBLIC_API_URL": public_value})
        self.assertNotIn(
            public_value,
            json.dumps(plan.planning_outputs(), sort_keys=True),
        )
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "state"
            state.mkdir()
            environment, _paths = node_execution._execution_environment(
                plan,
                state,
                {"PATH": os.environ.get("PATH", "")},
            )
            self.assertEqual(environment["NEXT_PUBLIC_API_URL"], public_value)
            for path in state.rglob("*"):
                if path.is_file() and not path.is_symlink():
                    self.assertNotIn(
                        public_value,
                        path.read_text(encoding="utf-8"),
                    )
            captured_stdout = io.StringIO()
            captured_stderr = io.StringIO()
            with (
                mock.patch(
                    "subprocess.run",
                    return_value=subprocess.CompletedProcess(
                        ["node", "script.mjs"],
                        0,
                        public_value,
                        public_value,
                    ),
                ),
                contextlib.redirect_stdout(captured_stdout),
                contextlib.redirect_stderr(captured_stderr),
            ):
                node_execution.run_command(
                    ["node", "script.mjs"],
                    cwd=state,
                    environment=environment,
                    timeout_seconds=30,
                    code="quality_failed",
                )
            self.assertEqual(captured_stdout.getvalue(), "")
            self.assertEqual(captured_stderr.getvalue(), "")
            node_execution._remove_no_follow(state / "node-validation")
            self.assertFalse((state / "node-validation").exists())

    def test_cleanup_removes_files_directories_and_nested_read_only_state(self) -> None:
        plan = self.web_plan()
        with tempfile.TemporaryDirectory() as directory:
            state, copy_root = cleanup_layout(Path(directory))
            node_modules = copy_root / "node_modules"
            nested = node_modules / "readonly" / "nested"
            nested.mkdir(parents=True)
            generated = nested / "generated.js"
            generated.write_text("generated\n", encoding="utf-8")
            generated.chmod(0o400)
            nested.chmod(0o500)
            nested.parent.chmod(0o500)
            coverage = copy_root / "coverage"
            coverage.mkdir()
            (coverage / "summary.json").write_text("{}\n", encoding="utf-8")
            node_execution.cleanup_generated(
                copy_root,
                state,
                plan,
                ["node_modules", "coverage"],
            )
            self.assertFalse((state / "node-validation").exists())

    def test_cleanup_unlinks_outside_file_and_directory_symlinks(self) -> None:
        plan = self.web_plan()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state, copy_root = cleanup_layout(root)
            outside_file = root / "outside-file"
            outside_file.write_text("keep-file\n", encoding="utf-8")
            outside_directory = root / "outside-directory"
            outside_directory.mkdir()
            outside_sentinel = outside_directory / "sentinel"
            outside_sentinel.write_text("keep-directory\n", encoding="utf-8")
            (copy_root / "coverage").symlink_to(outside_file)
            (copy_root / "node_modules").symlink_to(
                outside_directory,
                target_is_directory=True,
            )
            node_execution.cleanup_generated(
                copy_root,
                state,
                plan,
                ["coverage", "node_modules"],
            )
            self.assertEqual(
                outside_file.read_text(encoding="utf-8"),
                "keep-file\n",
            )
            self.assertEqual(
                outside_sentinel.read_text(encoding="utf-8"),
                "keep-directory\n",
            )
            self.assertFalse((state / "node-validation").exists())

    def test_cleanup_rejects_traversal_and_unregistered_roots(self) -> None:
        plan = self.web_plan()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state, copy_root = cleanup_layout(root)
            outside = root / "outside"
            outside.write_text("keep\n", encoding="utf-8")
            with self.assertRaisesRegex(
                node_validation.NodeValidationError,
                "cleanup_failed",
            ):
                node_execution.cleanup_generated(
                    copy_root,
                    state,
                    plan,
                    ["../outside"],
                )
            self.assertEqual(outside.read_text(encoding="utf-8"), "keep\n")
            wrong = state / "wrong"
            wrong.mkdir()
            with self.assertRaisesRegex(
                node_validation.NodeValidationError,
                "cleanup_failed",
            ):
                node_execution.cleanup_generated(
                    wrong,
                    state,
                    plan,
                    ["node_modules"],
                )
            node_execution._remove_no_follow(state / "node-validation")

    def test_cleanup_failure_reports_residue(self) -> None:
        plan = self.web_plan()
        with tempfile.TemporaryDirectory() as directory:
            state, copy_root = cleanup_layout(Path(directory))
            node_modules = copy_root / "node_modules"
            node_modules.mkdir()
            residue = node_modules / "residue.fifo"
            os.mkfifo(residue)
            with self.assertRaisesRegex(
                node_validation.NodeValidationError,
                "cleanup_failed",
            ):
                node_execution.cleanup_generated(
                    copy_root,
                    state,
                    plan,
                    ["node_modules"],
                )
            self.assertTrue((state / "node-validation").exists())
            residue.unlink()
            node_execution._remove_no_follow(state / "node-validation")
            self.assertFalse((state / "node-validation").exists())


if __name__ == "__main__":
    unittest.main()
