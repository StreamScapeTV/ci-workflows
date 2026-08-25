from __future__ import annotations

import copy
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml

from ci_workflows.gitops import build_plan, load_gitops_contract
from ci_workflows.gitops_execution import (
    GitOpsTools,
    cleanup_gitops_state,
    execute_gitops_plan,
)
from ci_workflows.gitops_types import GitOpsProfile, GitOpsRequest, GitOpsValidationError

ROOT = Path(__file__).resolve().parents[1]


class GitOpsIssue571ActiveCompositionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.source = self.base / "source"
        self.state = self.base / "state" / "gitops"
        self.source.mkdir(parents=True)
        self._write_fixture_tree()
        self.base_sha = self._commit("initial")
        self.contract = self._contract()

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _write_fixture_tree(self) -> None:
        chart = self.source / "apps" / "generic-chart"
        templates = chart / "templates" / "nested"
        templates.mkdir(parents=True)
        (chart / "Chart.yaml").write_text(
            "apiVersion: v2\n"
            "name: generic-chart\n"
            "version: 0.1.0\n",
            encoding="utf-8",
        )
        (templates / "deployment.yaml").write_text(
            "apiVersion: apps/v1\n"
            "kind: Deployment\n"
            "metadata:\n"
            "  name: {{ include \"generic-chart.name\" . }}\n"
            "spec:\n"
            "  replicas: {{ .Values.replicaCount }}\n",
            encoding="utf-8",
        )
        (self.source / "apps" / "raw.yaml").write_text(
            "apiVersion: v1\n"
            "kind: ConfigMap\n"
            "metadata:\n"
            "  name: raw-config\n"
            "data:\n"
            "  mode: current\n",
            encoding="utf-8",
        )

    def _commit(self, message: str) -> str:
        if not (self.source / ".git").exists():
            subprocess.run(["git", "init", "-q"], cwd=self.source, check=True)
            subprocess.run(
                ["git", "config", "user.name", "GitOps Test"],
                cwd=self.source,
                check=True,
            )
            subprocess.run(
                ["git", "config", "user.email", "gitops@example.invalid"],
                cwd=self.source,
                check=True,
            )
        subprocess.run(["git", "add", "-A"], cwd=self.source, check=True)
        subprocess.run(
            ["git", "commit", "-q", "--allow-empty", "-m", message],
            cwd=self.source,
            check=True,
        )
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=self.source,
            text=True,
        ).strip()

    def _head(self) -> str:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=self.source,
            text=True,
        ).strip()

    def _contract(self) -> dict[str, object]:
        contract = copy.deepcopy(load_gitops_contract(ROOT))
        contract["targets"]["issue-571-root-yaml"] = {
            "expected_render_path": None,
            "include": ["apps/**/*.yaml"],
            "kind": "yaml",
            "kubernetes_version": "1.34.0",
            "required_values": [],
            "root": ".",
            "schema_path": None,
            "sops_files": [],
            "values_files": [],
            "vendored_dependencies": [],
        }
        contract["consumer_contracts"]["synthetic"]["profiles"]["changed-tree"] = {
            "policy_script": None,
            "targets": ["issue-571-root-yaml"],
        }
        return contract

    def _request(self, *, sha: str) -> GitOpsRequest:
        return GitOpsRequest(
            repository="StreamScapeTV/ci-workflows",
            admitted_sha=sha,
            consumer_contract="synthetic",
            validation_profile=GitOpsProfile.CHANGED_TREE,
            source_trust="trusted-exact",
            change_base_sha=self.base_sha,
            policy_script_profile=None,
        )

    @staticmethod
    def _tools() -> GitOpsTools:
        return GitOpsTools(
            yaml=yaml,
            binaries={},
            versions={"pyyaml": "6.0.3"},
        )

    def _execute(self, sha: str):
        plan = build_plan(self.contract, self._request(sha=sha), self.source)
        return execute_gitops_plan(
            plan,
            self.source,
            self.state,
            tools=self._tools(),
        )

    def test_changed_tree_execution_skips_template_source_under_real_chart(self) -> None:
        raw = self.source / "apps" / "raw.yaml"
        raw.write_text(
            raw.read_text(encoding="utf-8").replace("current", "changed"),
            encoding="utf-8",
        )
        head = self._commit("downstream-like-change")

        result = self._execute(head)

        self.assertEqual(("issue-571-root-yaml",), result.selected_targets)
        self.assertEqual(2, result.validated_files)
        self.assertTrue(result.clean_tree)
        cleanup_gitops_state(self.state)

    def test_malformed_ordinary_raw_yaml_still_fails_closed(self) -> None:
        (self.source / "apps" / "raw.yaml").write_text(
            "apiVersion: [unterminated\n",
            encoding="utf-8",
        )
        head = self._commit("malformed-raw-yaml")

        with self.assertRaisesRegex(GitOpsValidationError, "yaml_invalid"):
            self._execute(head)

    def test_chartless_templates_directory_remains_strict_raw_yaml(self) -> None:
        templates = self.source / "apps" / "templates"
        templates.mkdir()
        (templates / "not-a-chart-template.yaml").write_text(
            "metadata:\n"
            "  name: {{ .Values.name }}\n",
            encoding="utf-8",
        )
        head = self._commit("chartless-template")

        with self.assertRaisesRegex(GitOpsValidationError, "yaml_invalid"):
            self._execute(head)


if __name__ == "__main__":
    unittest.main()
