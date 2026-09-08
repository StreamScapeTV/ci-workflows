from pathlib import Path
import os
import subprocess
import tempfile
import textwrap
import unittest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/python.yml"


class PythonWorkflowTests(unittest.TestCase):
    def _workflow(self) -> tuple[dict, list[dict], dict[str, dict]]:
        workflow = yaml.safe_load(WORKFLOW.read_text())
        steps = workflow["jobs"]["ci"]["steps"]
        by_name = {step.get("name"): step for step in steps if step.get("name")}
        return workflow, steps, by_name

    def test_runtime_version_is_source_owned_and_bounded(self) -> None:
        workflow, steps, by_name = self._workflow()
        inputs = workflow["on"]["workflow_call"]["inputs"]
        self.assertEqual(
            set(inputs),
            {"repository", "ref", "test_profile", "ci_run_id", "upload_private_log"},
        )
        self.assertNotIn("python_version", inputs)

        resolver = by_name["Resolve source Python version"]["run"]
        self.assertIn("version='3.x'", resolver)
        self.assertIn("if test -f .python-version", resolver)
        self.assertIn("wc -l < .python-version", resolver)
        self.assertIn("tr -d '\\r\\n' < .python-version", resolver)
        self.assertIn(r"^3\.[0-9]{1,2}\.[0-9]{1,2}$", resolver)
        self.assertIn("must contain exactly one bounded CPython 3.x.y version", resolver)
        self.assertIn("GITHUB_OUTPUT", resolver)

        setup = by_name["Set up source Python"]
        self.assertEqual(setup["uses"], "actions/setup-python@v6")
        self.assertEqual(setup["with"]["python-version"], "${{ steps.python_version.outputs.version }}")

        names = [step.get("name") for step in steps]
        self.assertLess(names.index("Check out source"), names.index("Resolve source Python version"))
        self.assertLess(names.index("Resolve source Python version"), names.index("Set up source Python"))
        self.assertLess(names.index("Set up source Python"), names.index("Run fixed Python profile"))

    def test_python_executor_keeps_generic_profiles_and_one_bounded_privileged_profile(self) -> None:
        source = WORKFLOW.read_text()
        self.assertNotIn('python-version: "3.12"', source)
        self.assertNotIn("inputs.python_version", source)
        self.assertIn("release-gates)", source)
        self.assertIn("bash scripts/run_release_gates.sh", source)
        self.assertIn("agent-state-issue-reconcile)", source)


    def _run_backend_postgres_fixture(
        self,
        *,
        validation_status: int = 0,
        cleanup_status: int = 0,
    ) -> tuple[subprocess.CompletedProcess[str], str]:
        _, _, by_name = self._workflow()
        command = by_name["Run fixed Python profile"]["run"]
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            fake_bin = root / "bin"
            scripts = root / "scripts"
            fake_bin.mkdir()
            scripts.mkdir()
            docker_state = root / "docker-state"
            ci_log = root / "ci.log"

            docker = fake_bin / "docker"
            docker.write_text(
                textwrap.dedent(
                    """\
                    #!/usr/bin/env bash
                    set -u
                    case "${1:-}" in
                      run)
                        : > "${FAKE_DOCKER_STATE}"
                        exit 0
                        ;;
                      exec)
                        test -e "${FAKE_DOCKER_STATE}"
                        exit $?
                        ;;
                      inspect)
                        test -e "${FAKE_DOCKER_STATE}"
                        exit $?
                        ;;
                      rm)
                        status="${FAKE_DOCKER_RM_STATUS:-0}"
                        if test "${status}" = 0; then
                          rm -f "${FAKE_DOCKER_STATE}"
                        fi
                        exit "${status}"
                        ;;
                      *) exit 2 ;;
                    esac
                    """
                ),
                encoding="utf-8",
            )
            docker.chmod(0o755)

            validation = scripts / "run_backend_postgres_validation.sh"
            validation.write_text(
                "#!/usr/bin/env bash\nexit \"${FAKE_VALIDATION_STATUS:-0}\"\n",
                encoding="utf-8",
            )
            validation.chmod(0o755)

            env = os.environ.copy()
            env.update(
                {
                    "PATH": f"{fake_bin}:{env['PATH']}",
                    "TEST_PROFILE": "backend-postgres",
                    "SOURCE_REPOSITORY": "StreamScapeTV/iptv-backend",
                    "SOURCE_REF": "fixture",
                    "GITHUB_RUN_ID": "1",
                    "GITHUB_RUN_ATTEMPT": "1",
                    "CI_LOG": str(ci_log),
                    "FAKE_DOCKER_STATE": str(docker_state),
                    "FAKE_DOCKER_RM_STATUS": str(cleanup_status),
                    "FAKE_VALIDATION_STATUS": str(validation_status),
                }
            )
            result = subprocess.run(
                ["bash", "-c", command],
                cwd=root,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )
            return result, ci_log.read_text(encoding="utf-8")

    def test_backend_postgres_preserves_product_failure_across_successful_cleanup(self) -> None:
        result, log = self._run_backend_postgres_fixture(validation_status=7)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("validation_exit_status=7 cleanup_exit_status=0", log)
        self.assertIn("Backend PostgreSQL validation exited with status 7.", log)

    def test_backend_postgres_succeeds_only_when_product_and_cleanup_succeed(self) -> None:
        result, log = self._run_backend_postgres_fixture()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("validation_exit_status=0 cleanup_exit_status=0", log)

    def test_backend_postgres_cleanup_failure_is_authoritative(self) -> None:
        result, log = self._run_backend_postgres_fixture(cleanup_status=9)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("validation_exit_status=0 cleanup_exit_status=9", log)
        self.assertIn("PostgreSQL test service cleanup exited with status 9.", log)

    def test_backend_postgres_uses_explicit_validation_and_cleanup_statuses(self) -> None:
        _, _, by_name = self._workflow()
        run = by_name["Run fixed Python profile"]["run"]
        start = run.index("run_backend_postgres()")
        end = run.index("\n\nif [[ -f requirements.txt ]]", start)
        postgres = run[start:end]
        self.assertNotIn("if ! (", postgres)
        self.assertIn("validation_status=$?", postgres)
        self.assertIn("cleanup_status=$?", postgres)
        self.assertIn("validation_status != 0 || cleanup_status != 0", postgres)
        self.assertIn("trap 'cleanup_postgres || true' EXIT", postgres)

    def test_agent_state_issue_reconcile_credentials_are_main_only_and_profile_scoped(self) -> None:
        _, _, by_name = self._workflow()
        commands = by_name["Run fixed Python profile"]
        env = commands["env"]
        gate = (
            "inputs.test_profile == 'agent-state-issue-reconcile' && "
            "inputs.repository == 'StreamScapeTV/agent-state-supabase' && "
            "inputs.ref == 'main'"
        )
        self.assertEqual(env["SOURCE_REPOSITORY"], "${{ inputs.repository || github.repository }}")
        self.assertEqual(env["SOURCE_REF"], "${{ inputs.ref || github.ref_name }}")
        self.assertEqual(env["GITHUB_TOKEN"], "${{ " + gate + " && steps.source.outputs.token || '' }}")
        self.assertEqual(
            env["SUPABASE_URL"],
            "${{ " + gate + " && secrets.AGENT_STATE_SUPABASE_URL || '' }}",
        )
        self.assertEqual(
            env["SUPABASE_SERVICE_ROLE_KEY"],
            "${{ " + gate + " && secrets.AGENT_STATE_SUPABASE_SECRET_KEY || '' }}",
        )

        run = commands["run"]
        self.assertIn('test "${SOURCE_REPOSITORY}" = "StreamScapeTV/agent-state-supabase"', run)
        self.assertIn('test "${SOURCE_REF}" = "main"', run)
        self.assertIn("scripts/reconcile_github_issue_inventory.py --owner StreamScapeTV", run)
        self.assertIn("--owner StreamScapeTV --apply", run)
        self.assertIn('("missing", "extra", "github_navigation_mismatches")', run)
        self.assertIn("post-apply GitHub/Agent State equality failed", run)


if __name__ == "__main__":
    unittest.main()
