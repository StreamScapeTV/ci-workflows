from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class AppleTerminalCleanupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.workflow = (
            ROOT / ".github/workflows/reusable-apple.yml"
        ).read_text(encoding="utf-8")
        self.cleanup_adapter = (
            ROOT / "scripts/ci/apple_checkout_cleanup.py"
        ).read_text(encoding="utf-8")

    def test_public_workflow_uses_immutable_helpers_and_no_follow_source_cleanup(self) -> None:
        self.assertNotIn("path: .ciw", self.workflow)
        self.assertNotIn("./.ciw/actions/", self.workflow)
        self.assertNotIn(
            "python3 .ciw/scripts/ci/apple_checkout_cleanup.py central",
            self.workflow,
        )
        self.assertIn("Clear fixed admitted source root without following links", self.workflow)
        self.assertIn("Remove exact admitted source checkout once", self.workflow)
        self.assertEqual(self.workflow.count('remove_no_follow(Path("source"))'), 2)
        self.assertEqual(self.workflow.count("os.lstat(path)"), 2)
        self.assertEqual(self.workflow.count("stat.S_ISLNK"), 2)
        self.assertEqual(self.workflow.count('os.path.lexists("source")'), 2)
        self.assertIn("_remove_no_follow(path)", self.cleanup_adapter)
        self.assertIn("os.path.lexists(path)", self.cleanup_adapter)

    def test_terminal_projection_includes_primary_and_every_cleanup_surface(self) -> None:
        for identifier in (
            "execute",
            "apple_cleanup",
            "residue",
            "clean",
            "source_cleanup",
            "workspace_cleanup",
            "terminal",
        ):
            self.assertIn(f"- id: {identifier}", self.workflow)
        for variable in (
            "EXECUTE_OUTCOME",
            "APPLE_CLEANUP_OUTCOME",
            "RESIDUE_OUTCOME",
            "CLEAN_OUTCOME",
            "SOURCE_CLEANUP_OUTCOME",
            "WORKSPACE_CLEANUP_OUTCOME",
        ):
            self.assertIn(variable, self.workflow)
        self.assertIn("Project terminal Apple validation status", self.workflow)
        self.assertIn('echo "result=failure" >> "${GITHUB_OUTPUT}"', self.workflow)
        self.assertIn("exit 1", self.workflow)

    def test_every_cleanup_operation_runs_to_terminal_projection(self) -> None:
        for identifier in (
            "apple_cleanup",
            "residue",
            "clean",
            "source_cleanup",
            "workspace_cleanup",
        ):
            start = self.workflow.index(f"- id: {identifier}")
            end = self.workflow.find("\n      - ", start + 1)
            block = self.workflow[start : end if end >= 0 else None]
            self.assertIn("if: always()", block, identifier)
        terminal_start = self.workflow.index("- id: terminal")
        terminal = self.workflow[terminal_start:]
        self.assertIn("if: always()", terminal)
        self.assertIn('echo "result=failure" >> "${GITHUB_OUTPUT}"', terminal)
        self.assertIn("exit 1", terminal)


if __name__ == "__main__":
    unittest.main()
