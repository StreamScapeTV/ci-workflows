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

    def test_fixed_central_checkout_is_removed_without_following_links(self) -> None:
        self.assertIn(
            "Clear stale fixed central checkout root without following links",
            self.workflow,
        )
        self.assertIn(
            "Remove stale fixed central checkout root without following links",
            self.workflow,
        )
        self.assertEqual(self.workflow.count('remove_no_follow(Path(".ciw"))'), 2)
        self.assertEqual(self.workflow.count("os.lstat(path)"), 4)
        self.assertEqual(self.workflow.count("stat.S_ISLNK"), 4)
        self.assertEqual(self.workflow.count("test ! -e .ciw"), 2)
        self.assertEqual(self.workflow.count("test ! -L .ciw"), 2)
        self.assertEqual(self.workflow.count('os.path.lexists(".ciw")'), 2)
        self.assertNotIn(
            "python3 .ciw/scripts/ci/apple_checkout_cleanup.py central",
            self.workflow,
        )
        self.assertIn("_remove_no_follow(path)", self.cleanup_adapter)
        self.assertIn("os.path.lexists(path)", self.cleanup_adapter)

    def test_terminal_projection_includes_primary_and_every_cleanup_surface(self) -> None:
        for identifier in (
            "execute",
            "apple_cleanup",
            "apple_residue",
            "source_cleanup",
            "workspace_cleanup",
            "ciw_cleanup",
        ):
            self.assertIn(f"- id: {identifier}", self.workflow)
        for variable in (
            "EXECUTE_OUTCOME",
            "APPLE_CLEANUP_OUTCOME",
            "APPLE_RESIDUE_OUTCOME",
            "SOURCE_CLEANUP_OUTCOME",
            "WORKSPACE_CLEANUP_OUTCOME",
            "CIW_CLEANUP_OUTCOME",
        ):
            self.assertIn(variable, self.workflow)
        self.assertIn('"central_checkout": os.environ["CIW_CLEANUP_OUTCOME"]', self.workflow)
        self.assertIn('"cleanup_failures": failed_cleanup', self.workflow)
        self.assertIn('"primary": None if primary == "success" else primary', self.workflow)
        self.assertIn("Project primary and combined terminal Apple failures", self.workflow)

    def test_every_terminal_operation_runs_to_the_final_projection(self) -> None:
        for identifier in (
            "execute",
            "apple_cleanup",
            "apple_residue",
            "source_cleanup",
            "workspace_cleanup",
            "ciw_cleanup",
        ):
            start = self.workflow.index(f"- id: {identifier}")
            end = self.workflow.find("\n      - ", start + 1)
            block = self.workflow[start : end if end >= 0 else None]
            self.assertIn("continue-on-error: true", block, identifier)
        terminal = self.workflow.split(
            "Project primary and combined terminal Apple failures",
            1,
        )[1]
        self.assertIn("if: always()", terminal)
        self.assertIn("raise SystemExit(1)", terminal)


if __name__ == "__main__":
    unittest.main()
