from __future__ import annotations

from pathlib import Path
import json
import os
import stat
import subprocess
import tempfile
import time
import unittest
import zipfile

import yaml

ROOT = Path(__file__).resolve().parents[1]
ACCEPTANCE = ROOT / ".github/workflows/repository-acceptance.yml"
REPOSITORY = ROOT / ".github/workflows/repository.yml"
DISPATCH = ROOT / ".github/workflows/central-ci-dispatch.yml"
COMMAND = ROOT / "scripts/ci/repository_command.py"
FIXTURE = ROOT / "scripts/ci/repository_acceptance_fixture.py"
EVIDENCE = ROOT / "scripts/ci/repository_evidence.py"


class RepositoryAcceptanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.acceptance = yaml.safe_load(ACCEPTANCE.read_text(encoding="utf-8"))
        cls.acceptance_text = ACCEPTANCE.read_text(encoding="utf-8")
        cls.repository = yaml.safe_load(REPOSITORY.read_text(encoding="utf-8"))
        cls.dispatch = yaml.safe_load(DISPATCH.read_text(encoding="utf-8"))
        cls.dispatch_text = DISPATCH.read_text(encoding="utf-8")

    def test_public_repository_workflow_exposes_no_fixture_mode_or_timeout(self) -> None:
        inputs = self.repository["on"]["workflow_call"]["inputs"]
        self.assertNotIn("fixture", " ".join(inputs).lower())
        self.assertNotIn("acceptance", inputs)
        self.assertNotIn("timeout", inputs)
        execute = next(
            step for step in self.repository["jobs"]["execute"]["steps"]
            if step.get("name") == "Execute fixed repository-owned entrypoint"
        )
        self.assertIn("repository_command.py", execute["run"])
        self.assertNotIn("--timeout-seconds", execute["run"])
        repository_text = REPOSITORY.read_text(encoding="utf-8")
        self.assertIn("repository_evidence.py", repository_text)
        self.assertIn('python3 "${helper}" scrub', repository_text)
        self.assertIn('python3 "${helper}" package', repository_text)

    def test_acceptance_workflow_has_only_ci_run_input_and_fixed_internal_modes(self) -> None:
        call = self.acceptance["on"]["workflow_call"]
        self.assertEqual(set(call["inputs"]), {"ci_run_id"})
        self.assertEqual(
            set(call["secrets"]),
            {
                "AGENT_STATE_SUPABASE_URL",
                "AGENT_STATE_SUPABASE_SECRET_KEY",
                "GOOGLE_DRIVE_CI_LOGS_FOLDER_ID",
                "GOOGLE_DRIVE_CLIENT_ID",
                "GOOGLE_DRIVE_CLIENT_SECRET",
                "GOOGLE_DRIVE_REFRESH_TOKEN",
            },
        )
        fixture_job = self.acceptance["jobs"]["fixture"]
        self.assertEqual(fixture_job["strategy"]["matrix"]["mode"], ["success", "failure", "stall"])
        self.assertEqual(fixture_job["strategy"]["max-parallel"], 1)
        self.assertIn("--timeout-seconds 8", self.acceptance_text)
        self.assertIn("repository_command.py", self.acceptance_text)
        self.assertIn("repository_evidence.py scrub", self.acceptance_text)
        self.assertIn("repository_evidence.py package", self.acceptance_text)
        self.assertIn("actions/google-drive@main", self.acceptance_text)
        self.assertIn("phase: finish", self.acceptance_text)
        self.assertIn("real product/toolchain integration", self.acceptance_text)

    def test_dispatch_acceptance_is_fixed_to_central_main_and_zero_inputs(self) -> None:
        steps = self.dispatch["jobs"]["request"]["steps"]
        validation = next(
            step for step in steps
            if step.get("name") == "Validate generic repository-owned CI request"
        )
        script = validation["run"]

        def run(*, repository: str, ref: str, is_tag: str, inputs: dict) -> subprocess.CompletedProcess[str]:
            return subprocess.run(
                ["bash", "-c", script],
                cwd=ROOT,
                env={
                    **os.environ,
                    "TEST_PROFILE": "acceptance",
                    "INPUTS_JSON": json.dumps(inputs),
                    "REQUEST_REPOSITORY": repository,
                    "REQUEST_REF": ref,
                    "REQUEST_IS_TAG": is_tag,
                },
                text=True,
                capture_output=True,
                check=False,
            )

        good = run(repository="StreamScapeTV/ci-workflows", ref="main", is_tag="false", inputs={})
        self.assertEqual(good.returncode, 0, good.stderr)
        for kwargs, message in (
            ({"repository": "ExampleOrg/example", "ref": "main", "is_tag": "false", "inputs": {}}, "fixed to StreamScapeTV/ci-workflows main"),
            ({"repository": "StreamScapeTV/ci-workflows", "ref": "feature", "is_tag": "false", "inputs": {}}, "fixed to StreamScapeTV/ci-workflows main"),
            ({"repository": "StreamScapeTV/ci-workflows", "ref": "main", "is_tag": "true", "inputs": {}}, "fixed to StreamScapeTV/ci-workflows main"),
            ({"repository": "StreamScapeTV/ci-workflows", "ref": "main", "is_tag": "false", "inputs": {"host_os": "linux"}}, "accepts no semantic inputs"),
        ):
            with self.subTest(kwargs=kwargs):
                result = run(**kwargs)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(message, result.stderr)

        job = self.dispatch["jobs"]["repository_acceptance"]
        self.assertEqual(job["uses"], "./.github/workflows/repository-acceptance.yml")
        self.assertEqual(set(job["with"]), {"ci_run_id"})
        self.assertFalse(job["concurrency"]["cancel-in-progress"])

    def _run_mode(self, mode: str) -> tuple[int, Path, Path, bytes, set[str]]:
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        root = Path(td.name)
        log = root / "central.log"
        log_dir = root / "logs"
        artifacts = root / "artifacts"
        progress = root / "progress.txt"
        archive = root / "evidence.zip"
        log_dir.mkdir()
        artifacts.mkdir()
        log.write_bytes(b"")
        progress.write_bytes(b"")
        secret = f"acceptance-secret-{mode}"
        env = {
            **os.environ,
            "CI_LOG": str(log),
            "CI_LOG_DIR": str(log_dir),
            "CI_ARTIFACT_DIR": str(artifacts),
            "CI_PROGRESS_FILE": str(progress),
            "REPOSITORY_ACCEPTANCE_SECRET": secret,
        }
        args = [
            "python3", str(COMMAND), "--cwd", str(ROOT), "--log-path", str(log),
        ]
        if mode == "stall":
            args.extend(["--timeout-seconds", "1.5"])
        args.extend(["--", "python3", str(FIXTURE), mode])
        started = time.monotonic()
        command = subprocess.run(args, cwd=ROOT, env=env, check=False)
        elapsed = time.monotonic() - started
        if mode == "stall":
            self.assertLess(elapsed, 5.0)

        scrub_env = {**env, "CI_SECRET_ACCEPTANCE": secret}
        scrub = subprocess.run(
            ["python3", str(EVIDENCE), "scrub"],
            cwd=ROOT,
            env=scrub_env,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(scrub.returncode, 0, scrub.stderr)
        package = subprocess.run(
            ["python3", str(EVIDENCE), "package", "--archive", str(archive)],
            cwd=ROOT,
            env=scrub_env,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(package.returncode, 0, package.stderr)
        with zipfile.ZipFile(archive) as bundle:
            names = set(bundle.namelist())
            payload = b"".join(bundle.read(name) for name in names)
            for name, target in (
                ("artifacts/Acceptance.framework/Headers", b"Versions/Current/Headers"),
                ("artifacts/Acceptance.framework/Versions/Current", b"A"),
            ):
                info = bundle.getinfo(name)
                self.assertEqual(stat.S_IFMT((info.external_attr >> 16) & 0xFFFF), stat.S_IFLNK)
                self.assertEqual(bundle.read(name), target)
        return command.returncode, log, archive, payload, names


    def test_command_helper_normalizes_signal_exit_to_shell_status(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            log = root / "central.log"
            command = subprocess.run(
                [
                    "python3", str(COMMAND),
                    "--cwd", str(ROOT),
                    "--log-path", str(log),
                    "--",
                    "python3", "-c",
                    "import os,signal; os.kill(os.getpid(), signal.SIGTERM)",
                ],
                cwd=ROOT,
                check=False,
            )
            self.assertEqual(command.returncode, 128 + 15)

    def test_success_failure_and_stall_use_same_scrub_package_boundaries(self) -> None:
        expected = {"success": 0, "failure": 23, "stall": 124}
        for mode, expected_code in expected.items():
            with self.subTest(mode=mode):
                code, log, _archive, payload, names = self._run_mode(mode)
                self.assertEqual(code, expected_code)
                secret = f"acceptance-secret-{mode}".encode()
                self.assertNotIn(secret, log.read_bytes())
                self.assertNotIn(secret, payload)
                self.assertIn(b"[REDACTED]", log.read_bytes())
                self.assertIn("logs/fixture.log", names)
                self.assertIn("progress.txt", names)
                self.assertNotIn("artifacts/oversized-artifact.bin", names)
                self.assertNotIn(b"must-not-enter-evidence", payload)


if __name__ == "__main__":
    unittest.main()
