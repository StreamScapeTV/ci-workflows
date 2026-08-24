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


class GitOpsIssue475CompositionTests(unittest.TestCase):
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
        apps = self.source / "apps"
        cluster = self.source / "clusters" / "devops"
        apps.mkdir(parents=True)
        cluster.mkdir(parents=True)
        (apps / "clean.yaml").write_text(
            "apiVersion: v1\n"
            "kind: ConfigMap\n"
            "metadata:\n"
            "  name: clean\n"
            "data:\n"
            "  mode: current\n",
            encoding="utf-8",
        )
        # Deliberate pre-existing formatting debt: trailing whitespace and no
        # final newline.  The YAML remains semantically valid.
        (apps / "legacy.yaml").write_text(
            "apiVersion: v1\n"
            "kind: ConfigMap\n"
            "metadata:\n"
            "  name: legacy\n"
            "data:\n"
            "  mode: old   ",
            encoding="utf-8",
        )
        (apps / "secret.secret.yaml").write_text(
            "apiVersion: v1\n"
            "kind: Secret\n"
            "metadata:\n"
            "  name: encrypted\n"
            "stringData:\n"
            "  token: ENC[AES256_GCM,data:ZmFrZQ==,iv:AA==,tag:AA==,type:str]\n"
            "sops:\n"
            "  mac: ENC[AES256_GCM,data:ZmFrZQ==,iv:AA==,tag:AA==,type:str]\n"
            "  version: '3.9.0'\n",
            encoding="utf-8",
        )
        (cluster / "kustomization.yaml").write_text(
            "apiVersion: kustomize.config.k8s.io/v1beta1\n"
            "kind: Kustomization\n"
            "resources:\n"
            "  - configmap.yaml\n",
            encoding="utf-8",
        )
        (cluster / "configmap.yaml").write_text(
            "apiVersion: v1\n"
            "kind: ConfigMap\n"
            "metadata:\n"
            "  name: rendered\n"
            "data:\n"
            "  mode: cluster\n",
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

    def _contract(self) -> dict[str, object]:
        contract = copy.deepcopy(load_gitops_contract(ROOT))
        targets = contract["targets"]
        targets["issue-475-root-yaml"] = {
            "expected_render_path": None,
            "include": ["apps/**/*.yaml", "clusters/**/*.yaml"],
            "kind": "yaml",
            "kubernetes_version": "1.34.0",
            "required_values": [],
            "root": ".",
            "schema_path": None,
            "sops_files": ["apps/**/*.secret.yaml", "clusters/**/*.secret.yaml"],
            "values_files": [],
            "vendored_dependencies": [],
        }
        targets["issue-475-kustomize"] = {
            "expected_render_path": None,
            "include": ["**/*.yaml"],
            "kind": "kustomize",
            "kubernetes_version": "1.34.0",
            "required_values": [],
            "root": "clusters/devops",
            "schema_path": None,
            "sops_files": [],
            "values_files": [],
            "vendored_dependencies": [],
        }
        profiles = contract["consumer_contracts"]["synthetic"]["profiles"]
        profiles["changed-tree"] = {
            "policy_script": None,
            "targets": ["issue-475-root-yaml", "issue-475-kustomize"],
        }
        profiles["full"] = {
            "policy_script": None,
            "targets": ["issue-475-root-yaml", "issue-475-kustomize"],
        }
        profiles["yaml"] = {
            "policy_script": None,
            "targets": ["issue-475-root-yaml"],
        }
        return contract

    def _request(
        self,
        profile: GitOpsProfile,
        *,
        sha: str | None = None,
        base: str | None = None,
    ) -> GitOpsRequest:
        return GitOpsRequest(
            repository="StreamScapeTV/ci-workflows",
            admitted_sha=sha or self._head(),
            consumer_contract="synthetic",
            validation_profile=profile,
            source_trust="trusted-exact",
            change_base_sha=base,
            policy_script_profile=None,
        )

    def _head(self) -> str:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=self.source,
            text=True,
        ).strip()

    def _tools(self) -> GitOpsTools:
        binaries = self.base / "tools"
        binaries.mkdir(exist_ok=True)
        kustomize = binaries / "kustomize"
        kustomize.write_text(
            "#!/bin/sh\n"
            "set -eu\n"
            "cat <<'EOF'\n"
            "apiVersion: v1\n"
            "kind: ConfigMap\n"
            "metadata:\n"
            "  name: rendered\n"
            "data:\n"
            "  mode: cluster\n"
            "EOF\n",
            encoding="utf-8",
        )
        kustomize.chmod(0o755)
        return GitOpsTools(
            yaml=yaml,
            binaries={"kustomize": kustomize},
            versions={"kustomize": "5.8.1", "pyyaml": "6.0.3"},
        )

    def _execute(self, request: GitOpsRequest):
        plan = build_plan(self.contract, request, self.source)
        return execute_gitops_plan(
            plan,
            self.source,
            self.state,
            tools=self._tools(),
        )

    def test_changed_tree_composes_root_yaml_sops_and_nested_kustomize(self) -> None:
        clean = self.source / "apps" / "clean.yaml"
        clean.write_text(
            clean.read_text(encoding="utf-8").replace("current", "changed"),
            encoding="utf-8",
        )
        rendered = self.source / "clusters" / "devops" / "configmap.yaml"
        rendered.write_text(
            rendered.read_text(encoding="utf-8").replace("cluster", "changed"),
            encoding="utf-8",
        )
        head = self._commit("compose")
        result = self._execute(
            self._request(
                GitOpsProfile.CHANGED_TREE,
                sha=head,
                base=self.base_sha,
            )
        )
        self.assertEqual(
            ("issue-475-root-yaml", "issue-475-kustomize"),
            result.selected_targets,
        )
        self.assertTrue(result.clean_tree)
        self.assertGreaterEqual(result.validated_files, 6)
        cleanup_gitops_state(self.state)

    def test_changed_yaml_style_is_strict_but_unrelated_legacy_debt_is_not(self) -> None:
        clean = self.source / "apps" / "clean.yaml"
        clean.write_text(
            clean.read_text(encoding="utf-8").replace("  mode: current\n", "  mode: changed   "),
            encoding="utf-8",
        )
        head = self._commit("style-defect")
        with self.assertRaisesRegex(
            GitOpsValidationError,
            r"yaml_style_failed: apps/clean\.yaml",
        ):
            self._execute(
                self._request(
                    GitOpsProfile.CHANGED_TREE,
                    sha=head,
                    base=self.base_sha,
                )
            )

    def test_full_is_semantic_only_for_historical_yaml_formatting(self) -> None:
        result = self._execute(self._request(GitOpsProfile.FULL))
        self.assertEqual(
            ("issue-475-root-yaml", "issue-475-kustomize"),
            result.selected_targets,
        )
        cleanup_gitops_state(self.state)

    def test_semantic_corruption_always_fails_even_when_file_is_unchanged(self) -> None:
        legacy = self.source / "apps" / "legacy.yaml"
        legacy.write_text("apiVersion: [unterminated\n", encoding="utf-8")
        broken_base = self._commit("legacy-semantic-corruption")
        clean = self.source / "apps" / "clean.yaml"
        clean.write_text(
            clean.read_text(encoding="utf-8").replace("current", "changed"),
            encoding="utf-8",
        )
        head = self._commit("unrelated-clean-change")
        with self.assertRaisesRegex(GitOpsValidationError, "yaml_invalid"):
            self._execute(
                self._request(
                    GitOpsProfile.CHANGED_TREE,
                    sha=head,
                    base=broken_base,
                )
            )
        with self.assertRaisesRegex(GitOpsValidationError, "yaml_invalid"):
            self._execute(self._request(GitOpsProfile.FULL, sha=head))

    def test_contract_owned_sops_glob_rejects_plaintext_matching_secret(self) -> None:
        secret = self.source / "apps" / "secret.secret.yaml"
        secret.write_text(
            secret.read_text(encoding="utf-8").replace(
                "ENC[AES256_GCM,data:ZmFrZQ==,iv:AA==,tag:AA==,type:str]",
                "plaintext",
                1,
            ),
            encoding="utf-8",
        )
        head = self._commit("plaintext-secret")
        with self.assertRaisesRegex(
            GitOpsValidationError,
            r"sops_plaintext_rejected: apps/secret\.secret\.yaml",
        ):
            self._execute(
                self._request(
                    GitOpsProfile.CHANGED_TREE,
                    sha=head,
                    base=self.base_sha,
                )
            )


if __name__ == "__main__":
    unittest.main()
