from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fixture_builder import create_repository
from ci_workflows.validation_harness import validate_repository


class ValidationExpressionContextTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        create_repository(self.root)
        self.workflow = self.root / ".github/workflows/reusable-sample.yml"
        self.original = self.workflow.read_text(encoding="utf-8")

    def context_findings(self):
        return tuple(
            finding
            for finding in validate_repository(
                self.root,
                include_public_api_validator=False,
            ).findings
            if finding.rule == "invalid-expression-context"
        )

    def test_job_level_env_rejects_runner_with_precise_location(self) -> None:
        self.workflow.write_text(
            self.original.replace(
                "    timeout-minutes: 20\n    outputs:\n",
                "    timeout-minutes: 20\n"
                "    env:\n"
                "      REGISTRY_AUTH_FILE: ${{ runner.temp }}/registry-auth.json\n"
                "    outputs:\n",
                1,
            ),
            encoding="utf-8",
        )

        findings = self.context_findings()
        self.assertEqual(1, len(findings))
        finding = findings[0]
        self.assertEqual(".github/workflows/reusable-sample.yml", finding.path)
        self.assertIn("job 'validate' env.REGISTRY_AUTH_FILE", finding.message)
        self.assertIn("context 'runner'", finding.message)
        self.assertIn("jobs.<job_id>.env", finding.message)

    def test_workflow_level_env_rejects_runner(self) -> None:
        self.workflow.write_text(
            self.original.replace(
                "permissions:\n",
                "env:\n"
                "  PACKAGE_ROOT: ${{ runner.temp }}/package\n"
                "permissions:\n",
                1,
            ),
            encoding="utf-8",
        )

        findings = self.context_findings()
        self.assertEqual(1, len(findings))
        self.assertIn("workflow env.PACKAGE_ROOT", findings[0].message)
        self.assertIn("context 'runner'", findings[0].message)
        self.assertIn("env allows only", findings[0].message)

    def test_job_if_rejects_runner_before_runner_dispatch(self) -> None:
        self.workflow.write_text(
            self.original.replace(
                "    runs-on: [linux, amd64, general, small]\n",
                "    if: ${{ runner.os == 'Linux' }}\n"
                "    runs-on: [linux, amd64, general, small]\n",
                1,
            ),
            encoding="utf-8",
        )

        findings = self.context_findings()
        self.assertEqual(1, len(findings))
        self.assertIn("job 'validate' field 'if'", findings[0].message)
        self.assertIn("jobs.<job_id>.if", findings[0].message)

    def test_step_runtime_runner_context_remains_allowed(self) -> None:
        self.workflow.write_text(
            self.original.replace(
                "        shell: bash\n        run: |\n",
                "        shell: bash\n"
                "        env:\n"
                "          REGISTRY_AUTH_FILE: ${{ runner.temp }}/registry-auth.json\n"
                "        run: |\n"
                "          echo \"runner=${{ runner.os }}\" >/dev/null\n",
                1,
            ),
            encoding="utf-8",
        )

        self.assertEqual((), self.context_findings())

    def test_quoted_runner_text_is_not_a_context_reference(self) -> None:
        self.workflow.write_text(
            self.original.replace(
                "    timeout-minutes: 20\n    outputs:\n",
                "    timeout-minutes: 20\n"
                "    env:\n"
                "      PACKAGE_ROOT: ${{ format('runner.temp/{0}', inputs.admitted_sha) }}\n"
                "    outputs:\n",
                1,
            ),
            encoding="utf-8",
        )

        self.assertEqual((), self.context_findings())

    def test_property_named_runner_is_not_a_root_context_reference(self) -> None:
        self.workflow.write_text(
            self.original.replace(
                "    timeout-minutes: 20\n    outputs:\n",
                "    timeout-minutes: 20\n"
                "    env:\n"
                "      EVENT_RUNNER: ${{ github.event.runner }}\n"
                "    outputs:\n",
                1,
            ),
            encoding="utf-8",
        )

        self.assertEqual((), self.context_findings())

    def test_step_if_rejects_secrets_but_step_env_allows_them(self) -> None:
        self.workflow.write_text(
            self.original.replace(
                "        shell: bash\n        run: |\n",
                "        shell: bash\n"
                "        if: ${{ secrets.RUNTIME_TOKEN != '' }}\n"
                "        env:\n"
                "          RUNTIME_TOKEN: ${{ secrets.RUNTIME_TOKEN }}\n"
                "        run: |\n",
                1,
            ),
            encoding="utf-8",
        )

        findings = self.context_findings()
        self.assertEqual(1, len(findings))
        self.assertIn("step 2 field 'if'", findings[0].message)
        self.assertIn("context 'secrets'", findings[0].message)
        self.assertIn("jobs.<job_id>.steps.if", findings[0].message)

    def test_job_output_allows_runner_context(self) -> None:
        self.workflow.write_text(
            self.original.replace(
                "      result: ${{ steps.execute.outputs.result }}\n",
                "      result: ${{ runner.os }}\n",
                1,
            ),
            encoding="utf-8",
        )

        self.assertEqual((), self.context_findings())


if __name__ == "__main__":
    unittest.main()
