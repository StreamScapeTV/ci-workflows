from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYTHON_DOC = ROOT / "docs/workflows/python.md"
VALIDATE_PYTHON_ACTION = "StreamScapeTV/ci-workflows/actions/validate-python"


class PythonDocumentationTests(unittest.TestCase):
    def test_guidance_uses_main_library_reference_without_component_lock(self) -> None:
        guide = PYTHON_DOC.read_text(encoding="utf-8")
        self.assertIn(f"{VALIDATE_PYTHON_ACTION}@main", guide)
        self.assertNotIn("action-tool-lock.json", guide)
        self.assertNotIn("immutable checkpoint", guide)
        self.assertNotRegex(
            guide,
            r"StreamScapeTV/ci-workflows/actions/[^\s`]+@[0-9a-f]{40}",
        )

    def test_product_neutral_runtime_and_private_service_contract_remains_documented(self) -> None:
        guide = PYTHON_DOC.read_text(encoding="utf-8")
        self.assertIn("runner-provided CPython 3.12", guide)
        self.assertIn("host-cpython-3.12", guide)
        self.assertIn("consumer-owned", guide.casefold())
        self.assertIn("CIW_POSTGRES_URL", guide)
        self.assertIn("dependency_file", guide)
        self.assertIn("script_path", guide)
        self.assertIn("No Actions cache", guide)
        self.assertIn("Private command output", guide)
        self.assertIn("credentials", guide)
        self.assertNotIn("zero routine artifacts", guide.casefold())
        self.assertNotIn("routine Actions artifacts remain zero", guide)
        self.assertNotIn("command_profile", guide)
        self.assertNotIn("verify-toolchain", guide)
        self.assertNotIn("render-evidence", guide)
        for forbidden in (
            "actions/setup-python",
            "uses: actions/setup-python",
            "sudo apt",
            "apt-get",
            "arguments_json",
            "environment_json",
        ):
            self.assertNotIn(forbidden, guide)


if __name__ == "__main__":
    unittest.main()
