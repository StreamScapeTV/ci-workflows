"""Focused runtime and contract coverage for ``ciw android validate``."""
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


class AndroidCIWDispatchTests(unittest.TestCase):
    def test_runtime_registry_matches_android_command_contract(self) -> None:
        runtime = ciw.runtime_command_index()
        self.assertIn("android validate", runtime)
        spec = runtime["android validate"]
        self.assertEqual(spec.domain, "android")
        self.assertEqual(spec.operation, "validate")
        self.assertEqual(
            spec.qualified_handler,
            "ci_workflows.ciw.handle_android_validate",
        )

        contract = load_command_contract(ROOT)
        row = next(
            item
            for item in contract["commands"]
            if item["domain"] == "android" and item["operation"] == "validate"
        )
        self.assertEqual(row["handler"], spec.qualified_handler)
        ciw.validate_runtime_contract(ROOT)

    def test_parser_exposes_only_bounded_android_validate_operation(self) -> None:
        arguments = ciw.parser().parse_args(
            ["android", "validate", "--phase", "plan", "--source-root", "source"]
        )
        self.assertEqual(arguments.domain, "android")
        self.assertEqual(arguments.operation, "validate")
        self.assertEqual(arguments.phase, "plan")
        self.assertEqual(arguments.source_root, "source")
        self.assertIs(arguments._command_spec, ciw.runtime_command_index()["android validate"])

        with self.assertRaises(SystemExit):
            ciw.parser().parse_args(["android", "arbitrary"])

    def test_handler_delegates_to_the_typed_android_adapter(self) -> None:
        arguments = argparse.Namespace(phase="plan", source_root="source")
        context = CIWContext(
            root=ROOT,
            environment={},
            stdout=io.StringIO(),
            stderr=io.StringIO(),
        )
        expected = CIWResult("android", "validate", outputs={"result": "planned"})
        with mock.patch.object(
            ciw,
            "execute_android_validate",
            return_value=expected,
        ) as execute:
            actual = ciw.handle_android_validate(arguments, context)
        self.assertIs(actual, expected)
        execute.assert_called_once_with(arguments, context)


if __name__ == "__main__":
    unittest.main()
