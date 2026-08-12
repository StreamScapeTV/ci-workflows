from __future__ import annotations

import hashlib
from pathlib import Path
import unittest

from ci_workflows.release_manifest import _release_surface


ROOT = Path(__file__).resolve().parents[1]


class ReleaseSurfaceTest(unittest.TestCase):
    def test_real_release_workflow_and_contract_files_are_hashed(self) -> None:
        surface = _release_surface(ROOT)
        workflow = ROOT / ".github/workflows/reusable-release.yml"
        expected_workflow_sha = hashlib.sha256(workflow.read_bytes()).hexdigest()
        self.assertEqual(
            expected_workflow_sha,
            surface["workflow_apis"]["release.orchestrate"]["sha256"],
        )
        self.assertEqual(
            ".github/workflows/reusable-release.yml",
            surface["workflow_apis"]["release.orchestrate"]["file"],
        )
        self.assertEqual(
            {"release-manifest", "flux-handoff", "releases"},
            set(surface["schemas"]),
        )
        for digest in surface["schemas"].values():
            self.assertRegex(digest, r"^[0-9a-f]{64}$")
        self.assertRegex(
            surface["function_library"]["sha256"],
            r"^[0-9a-f]{64}$",
        )

    def test_release_surface_hash_is_stable_across_repeated_reads(self) -> None:
        first = _release_surface(ROOT)
        second = _release_surface(ROOT)
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
