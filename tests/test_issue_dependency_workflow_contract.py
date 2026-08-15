from __future__ import annotations

from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "issue-dependency-sync.yml"


class WorkflowContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = WORKFLOW.read_text(encoding="utf-8")

    def test_exact_triggers_and_hourly_schedule(self):
        self.assertRegex(self.text, r'cron:\s+"0 \* \* \* \*"')
        self.assertIn("workflow_dispatch: {}", self.text)
        self.assertNotIn("pull_request:", self.text)
        self.assertNotIn("\npush:", self.text)

    def test_protected_main_and_general_tiny_linux_selector(self):
        self.assertIn("github.repository == 'StreamScapeTV/ci-workflows'", self.text)
        self.assertIn("github.ref == 'refs/heads/main'", self.text)
        self.assertIn("runs-on: [linux, amd64, general, tiny]", self.text)
        self.assertNotIn("runs-on: [linux, amd64, general]", self.text)
        self.assertNotIn("macOS", self.text)
        self.assertNotIn("ARM64", self.text)
        self.assertNotRegex(self.text, r"runs-on:\s*portable\b")

    def test_fixed_secret_is_exposed_only_as_gh_token(self):
        refs = re.findall(r"\$\{\{\s*secrets\.([A-Za-z0-9_]+)\s*\}\}", self.text)
        self.assertEqual(refs, ["DEPENDENCY_SYNC_GITHUB_TOKEN"])
        secret_line = re.search(
            r"GH_TOKEN:\s*\$\{\{\s*secrets\.DEPENDENCY_SYNC_GITHUB_TOKEN\s*\}\}",
            self.text,
        )
        self.assertIsNotNone(secret_line)
        self.assertNotIn("secrets: inherit", self.text)

    def test_no_inputs_or_consumer_parameters(self):
        self.assertNotRegex(self.text, r"workflow_dispatch:\s*\n\s+inputs:")
        forbidden = (
            "repository_input",
            "ref_input",
            "path_input",
            "command_input",
            "secret_name",
        )
        for item in forbidden:
            self.assertNotIn(item, self.text)

    def test_workflow_is_thin_and_calls_typed_adapter(self):
        self.assertIn("scripts/ci/sync_issue_dependencies.py", self.text)
        self.assertIn("bootstrap_validation_runtime.py", self.text)
        self.assertNotIn("gh issue edit", self.text)
        self.assertNotIn("dependencies/blocked_by", self.text)

    def test_concurrency_cleanup_and_zero_artifact_contract(self):
        self.assertIn("group: issue-dependency-sync", self.text)
        self.assertIn("cancel-in-progress: false", self.text)
        self.assertGreaterEqual(self.text.count("if: always()"), 2)
        self.assertNotIn("upload-artifact", self.text)
        self.assertNotIn("download-artifact", self.text)
        self.assertIn("Remove dependency-sync runtime state", self.text)

    def test_dependency_secret_is_not_job_scoped(self):
        job_prefix = self.text.split("steps:", 1)[0]
        self.assertNotIn("GH_TOKEN:", job_prefix)
        self.assertEqual(self.text.count("GH_TOKEN:"), 1)

    def test_no_arbitrary_dispatch_inputs(self):
        # Input-free recovery means the workflow cannot let a caller select
        # repository, ref, path, command, runner, or credential.
        for token in (
            "${{ inputs.",
            "runner_label",
            "repository:",
            "working-directory: ${{",
        ):
            if token == "repository:":
                # github.repository expression is allowed; YAML input key is not.
                self.assertNotRegex(self.text, r"^\s{6,}repository:\s*\$\{\{\s*inputs\.", re.M)
            else:
                self.assertNotIn(token, self.text)


if __name__ == "__main__":
    unittest.main()
