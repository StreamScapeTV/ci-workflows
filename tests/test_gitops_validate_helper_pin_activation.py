from __future__ import annotations

import json
import unittest
from pathlib import Path

import yaml

from ci_workflows.validation_model import ActionsLoader

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "reusable-gitops-validation.yml"
ACTION_LOCK = ROOT / "contracts" / "action-tool-lock.json"
IMPLEMENTATION = ROOT / "src" / "ci_workflows" / "gitops_composition.py"
HELPER = "StreamScapeTV/ci-workflows/actions/validate-gitops"
REVIEWED_SHA = "1e703ba47bc0e8f01b72bc805c2375aa38df609e"
STALE_SHAS = (
    "dfbd4c4396edffc5ac7a0a5804b1490d8ec9e239",
    "b99c4296fe7cad06ef6c5956b1b0eb86a49f0145",
)
RELEASE = "issue #571 active composition classifier checkpoint"


class GitOpsValidateHelperPinActivationTests(unittest.TestCase):
    def test_every_gitops_phase_uses_the_reviewed_classifier_checkpoint(self) -> None:
        source = WORKFLOW.read_text(encoding="utf-8")
        workflow = yaml.load(source, Loader=ActionsLoader)
        helper_refs = [
            str(step["uses"])
            for job in workflow["jobs"].values()
            for step in job.get("steps", [])
            if str(step.get("uses", "")).startswith(f"{HELPER}@")
        ]
        self.assertEqual(5, len(helper_refs))
        self.assertEqual({f"{HELPER}@{REVIEWED_SHA}"}, set(helper_refs))
        for stale_sha in STALE_SHAS:
            self.assertNotIn(f"{HELPER}@{stale_sha}", source)

    def test_action_lock_and_current_source_match_the_reviewed_checkpoint(self) -> None:
        lock = json.loads(ACTION_LOCK.read_text(encoding="utf-8"))
        entry = next(item for item in lock["third_party_actions"] if item["uses"] == HELPER)
        self.assertEqual(REVIEWED_SHA, entry["sha"])
        self.assertEqual(RELEASE, entry["release"])
        self.assertEqual("composite", entry["runtime"])
        self.assertEqual(
            f"https://github.com/StreamScapeTV/ci-workflows/tree/{REVIEWED_SHA}/actions/validate-gitops",
            entry["source"],
        )

        implementation = IMPLEMENTATION.read_text(encoding="utf-8")
        self.assertIn("from .gitops_render import _is_helm_template_source", implementation)
        self.assertIn("if not _is_helm_template_source(path, root)", implementation)


if __name__ == "__main__":
    unittest.main()
