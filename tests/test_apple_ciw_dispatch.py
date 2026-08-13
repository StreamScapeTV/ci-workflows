"""Focused runtime and contract coverage for ``ciw apple validate``."""
from __future__ import annotations

import argparse
import io
import unittest
from pathlib import Path
from unittest import mock

from ci_workflows import ciw
from ci_workflows.ciw_docs import load_command_contract
from ci_workflows.ciw_types import CIWContext, CIWResult

ROOT = Path(__file__).resolve().parents[1]


class AppleCIWDispatchTests(unittest.TestCase):
    def test_runtime_registry_matches_apple_command_contract(self) -> None:
        runtime = ciw.runtime_command_index()
        self.assertIn("apple validate", runtime)
        spec = runtime["apple validate"]
        self.assertEqual(spec.domain, "apple")
        self.assertEqual(spec.operation, "validate")
        self.assertEqual(
            spec.qualified_handler,
            "ci_workflows.ciw.handle_apple_validate",
        )

        contract = load_command_contract(ROOT)
        row = next(
            item
            for item in contract["commands"]
            if item["domain"] == "apple" and item["operation"] == "validate"
        )
        self.assertEqual(row["handler"], spec.qualified_handler)
        ciw.validate_runtime_contract(ROOT)

    def test_parser_exposes_only_bounded_apple_validate_operation(self) -> None:
        arguments = ciw.parser().parse_args(
            ["apple", "validate", "--phase", "plan", "--source-root", "source"]
        )
        self.assertEqual(arguments.domain, "apple")
        self.assertEqual(arguments.operation, "validate")
        self.assertEqual(arguments.phase, "plan")
        self.assertEqual(arguments.source_root, "source")
        self.assertEqual(
            arguments._command_spec,
            ciw.runtime_command_index()["apple validate"],
        )

        with self.assertRaises(SystemExit):
            ciw.parser().parse_args(["apple", "arbitrary"])

    def test_handler_delegates_to_the_typed_apple_adapter(self) -> None:
        arguments = argparse.Namespace(phase="plan", source_root="source")
        context = CIWContext(
            root=ROOT,
            environment={},
            stdout=io.StringIO(),
            stderr=io.StringIO(),
        )
        expected = CIWResult("apple", "validate", outputs={"result": "planned"})
        with mock.patch.object(
            ciw,
            "execute_apple_validate",
            return_value=expected,
        ) as execute:
            actual = ciw.handle_apple_validate(arguments, context)
        self.assertIs(actual, expected)
        execute.assert_called_once_with(arguments, context)


if __name__ == "__main__":
    unittest.main()
