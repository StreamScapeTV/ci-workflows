from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class AppleReusableCallerRegressionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.workflow = (
            ROOT / ".github/workflows/reusable-apple.yml"
        ).read_text(encoding="utf-8")
        registry = json.loads(
            (ROOT / "contracts/public-workflows/validation.json").read_text(
                encoding="utf-8"
            )
        )
        self.registry = next(
            row for row in registry["workflows"] if row["api_name"] == "validation.apple"
        )

    def _workflow_call_block(self, section: str, next_section: str) -> str:
        return self.workflow.split(f"{section}:", 1)[1].split(
            f"{next_section}:", 1
        )[0]

    def _workflow_call_names(self, section: str, next_section: str) -> set[str]:
        return set(
            re.findall(
                r"^      ([a-z_]+):$",
                self._workflow_call_block(section, next_section),
                re.M,
            )
        )

    def test_every_public_workflow_call_input_declares_a_string_type(self) -> None:
        block = self._workflow_call_block("inputs", "secrets")
        inputs = self._workflow_call_names("inputs", "secrets")
        self.assertEqual(
            len(inputs),
            len(re.findall(r"^        type: string$", block, re.M)),
            "every Apple workflow_call input must declare its GitHub Actions type",
        )
        self.assertRegex(
            block,
            r"(?ms)^      private_dependency_id:\n(?:        .*\n)*?        type: string$",
        )
        self.assertIn("artifact_exception_id", inputs)
        self.assertNotIn("secrets: inherit", self.workflow)

    def test_private_dependency_secret_is_explicit(self) -> None:
        secrets = self._workflow_call_names("secrets", "outputs")
        self.assertIn("private_dependency_token", secrets)
        self.assertNotIn("secrets: inherit", self.workflow)

    def test_registry_matches_the_callable_surface(self) -> None:
        inputs = self._workflow_call_names("inputs", "secrets")
        secrets = self._workflow_call_names("secrets", "outputs")
        self.assertEqual(
            inputs,
            {row["name"] for row in self.registry["inputs"]},
        )
        self.assertEqual(secrets, set(self.registry["secrets"]))


if __name__ == "__main__":
    unittest.main()
