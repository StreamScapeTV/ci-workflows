from __future__ import annotations

import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/reusable-oci-build.yml"
FOUNDATION_SHA = "70e08d4ddf8930046632a7135950e924b82e22bf"
OCI_SHA = "be0ec9505800bb5678083fc7ce912be83a90f139"


class OciReusableSourceIdentityTests(unittest.TestCase):
    def test_reusable_oci_uses_locked_immutable_private_actions_without_central_clone(self) -> None:
        source = WORKFLOW.read_text(encoding="utf-8")
        for forbidden in (
            "repository: ${{ job.workflow_repository }}",
            "ref: ${{ job.workflow_sha }}",
            "repository: StreamScapeTV/ci-workflows",
            "path: .ciw",
            "./.ciw/actions/",
            "secrets: inherit",
        ):
            self.assertNotIn(forbidden, source)
        self.assertNotIn("actions/checkout@", source)
        self.assertNotIn("github.workflow_sha", source)
        self.assertNotIn("GITHUB_WORKFLOW_SHA", source)

        expected = {
            "StreamScapeTV/ci-workflows/actions/validate-oci": OCI_SHA,
            "StreamScapeTV/ci-workflows/actions/exact-checkout": FOUNDATION_SHA,
            "StreamScapeTV/ci-workflows/actions/prepare-workspace": FOUNDATION_SHA,
            "StreamScapeTV/ci-workflows/actions/render-evidence": FOUNDATION_SHA,
            "StreamScapeTV/ci-workflows/actions/cleanup-workspace": FOUNDATION_SHA,
        }
        for helper, sha in expected.items():
            self.assertIn(f"uses: {helper}@{sha}", source)

        action_lock = json.loads(
            (ROOT / "contracts/action-tool-lock.json").read_text(encoding="utf-8")
        )
        locked = {
            item["uses"]: item
            for item in action_lock["third_party_actions"]
            if item["uses"] in expected
        }
        self.assertEqual(set(expected), set(locked))
        for helper, sha in expected.items():
            self.assertEqual(sha, locked[helper]["sha"])
            self.assertEqual("composite", locked[helper]["runtime"])

        self.assertIn("admitted_sha: ${{ inputs.admitted_sha }}", source)
        self.assertIn(
            'test "$(git rev-parse HEAD)" = "${{ inputs.admitted_sha }}"',
            source,
        )
        self.assertIn("git status --porcelain --untracked-files=all", source)


if __name__ == "__main__":
    unittest.main()
