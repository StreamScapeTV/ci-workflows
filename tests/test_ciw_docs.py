from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from ci_workflows.ciw_docs import (
    CIWError,
    render_ciw_docs,
    validate_command_contract,
    write_ciw_docs,
)

ROOT = Path(__file__).resolve().parents[1]


class CIWDocumentationTests(unittest.TestCase):
    def test_checked_in_documentation_is_exact_and_lists_every_command(self) -> None:
        rendered = render_ciw_docs(contract_root=ROOT)
        checked_in = (ROOT / "docs/reference/ciw.md").read_text(encoding="utf-8")
        self.assertEqual(checked_in, rendered)
        contract = json.loads((ROOT / "contracts/ciw-commands.json").read_text(encoding="utf-8"))
        for command in contract["commands"]:
            self.assertIn(
                f"`ciw {command['domain']} {command['operation']}`",
                checked_in,
            )
        for wrapper in contract["compatibility_wrappers"]:
            self.assertIn(f"`{wrapper['path']}`", checked_in)

    def test_renderer_check_detects_drift(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "contracts").mkdir()
            (root / "docs/reference").mkdir(parents=True)
            (root / "contracts/ciw-commands.json").write_text(
                (ROOT / "contracts/ciw-commands.json").read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            (root / "docs/reference/ciw.md").write_text("stale\n", encoding="utf-8")
            with self.assertRaisesRegex(CIWError, "ciw_docs_drift"):
                write_ciw_docs(contract_root=root, check=True)

    def test_alias_cycle_and_caller_selected_handler_metadata_fail_closed(self) -> None:
        payload = json.loads((ROOT / "contracts/ciw-commands.json").read_text(encoding="utf-8"))
        payload["aliases"] = {
            "source legacy-one": "source legacy-two",
            "source legacy-two": "source legacy-one",
        }
        with self.assertRaisesRegex(CIWError, "ciw_alias_cycle"):
            validate_command_contract(payload)

        payload = json.loads((ROOT / "contracts/ciw-commands.json").read_text(encoding="utf-8"))
        payload["commands"][0]["handler"] = "${{ inputs.handler }}"
        with self.assertRaisesRegex(CIWError, "ciw_handler_invalid"):
            validate_command_contract(payload)


if __name__ == "__main__":
    unittest.main()
