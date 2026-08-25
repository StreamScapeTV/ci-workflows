from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import yaml

from ci_workflows.gitops_render import _yaml_target
from ci_workflows.gitops_types import (
    GitOpsTarget,
    GitOpsTargetKind,
    GitOpsValidationError,
)


class GitOpsIssue557HelmTemplateClassificationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.source = Path(self.temporary.name).resolve()
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
            "  name: raw-config\n",
            encoding="utf-8",
        )
        self.target = GitOpsTarget(
            target_id="generic-root-yaml",
            kind=GitOpsTargetKind.YAML,
            root=".",
            include=("apps/**/*.yaml", "clusters/**/*.yaml"),
        )

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_real_helm_chart_templates_are_not_raw_yaml_documents(self) -> None:
        documents, validated_files = _yaml_target(
            self.target,
            self.source,
            yaml,
        )
        self.assertEqual(2, validated_files)
        self.assertEqual(
            {"ConfigMap", None},
            {
                document.get("kind") if isinstance(document, dict) else None
                for document in documents
            },
        )

    def test_malformed_real_raw_yaml_still_fails_closed(self) -> None:
        (self.source / "apps" / "broken.yaml").write_text(
            "apiVersion: [unterminated\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(GitOpsValidationError, "yaml_invalid"):
            _yaml_target(self.target, self.source, yaml)

    def test_chartless_templates_directory_is_not_excluded(self) -> None:
        chartless = self.source / "apps" / "templates"
        chartless.mkdir()
        (chartless / "not-a-chart-template.yaml").write_text(
            "metadata:\n"
            "  name: {{ .Values.name }}\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(GitOpsValidationError, "yaml_invalid"):
            _yaml_target(self.target, self.source, yaml)


if __name__ == "__main__":
    unittest.main()
