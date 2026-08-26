from __future__ import annotations

import unittest
from pathlib import Path

import yaml

from ci_workflows.validation_model import ActionsLoader

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "reusable-gitops-validation.yml"
IMPLEMENTATION = ROOT / "src" / "ci_workflows" / "gitops_composition.py"
HELPER = "StreamScapeTV/ci-workflows/actions/validate-gitops"


class GitOpsValidateHelperPinActivationTests(unittest.TestCase):
    def test_every_gitops_phase_uses_current_classifier_helper(self) -> None:
        source = WORKFLOW.read_text(encoding="utf-8")
        workflow = yaml.load(source, Loader=ActionsLoader)
        helper_refs = [
            str(step["uses"])
            for job in workflow["jobs"].values()
            for step in job.get("steps", [])
            if str(step.get("uses", "")).startswith(f"{HELPER}@")
        ]
        self.assertEqual(5, len(helper_refs))
        self.assertEqual({f"{HELPER}@main"}, set(helper_refs))
        self.assertNotIn("checkpoint", source.casefold())

    def test_current_source_keeps_active_composition_classifier(self) -> None:
        implementation = IMPLEMENTATION.read_text(encoding="utf-8")
        self.assertIn("from .gitops_render import _is_helm_template_source", implementation)
        self.assertIn("if not _is_helm_template_source(path, root)", implementation)


if __name__ == "__main__":
    unittest.main()
