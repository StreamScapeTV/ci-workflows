from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from ci_workflows.python_execution import podman_command  # noqa: E402

PYTHON_ARCHITECTURE = ROOT / "docs/architecture/python-validation.md"


class PythonArchitectureDocumentationTests(unittest.TestCase):
    def test_podman_runroot_guidance_matches_execution_contract(self) -> None:
        command = podman_command(Path("/workspace/validation-state"))
        guide = PYTHON_ARCHITECTURE.read_text(encoding="utf-8")

        self.assertIn("--root", command)
        self.assertNotIn("--runroot", command)
        self.assertIn("job-isolated default runroot", guide)
        self.assertIn("Podman 4.9", guide)
        self.assertIn("50 characters", guide)
        self.assertNotIn("marker-bound `--runroot`", guide)


if __name__ == "__main__":
    unittest.main()
