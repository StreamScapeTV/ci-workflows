from __future__ import annotations

import ast
import json
import unittest
from pathlib import Path

import yaml

from ci_workflows.foundation_docs import render_foundation_docs

ROOT = Path(__file__).resolve().parents[1]
ACTIONS = {
    "prepare-workspace": ("workspace prepare",),
    "verify-toolchain": ("tooling verify", "tooling install-asset"),
    "checkout-private-dependency": ("dependencies checkout-private",),
    "verify-repository-policy": ("policy verify-repository",),
    "render-evidence": ("evidence render",),
    "cleanup-workspace": ("workspace cleanup",),
}
FORBIDDEN_INPUTS = {
    "command",
    "shell",
    "callback",
    "deletion_path",
    "secret_name",
    "runner",
    "runner_labels",
    "engine",
    "container_engine",
}


class FoundationContractTests(unittest.TestCase):
    def test_fixture_manifest_covers_all_required_positive_and_negative_cases(self) -> None:
        manifest = json.loads(
            (ROOT / "tests/fixtures/foundation/cases.json").read_text(encoding="utf-8")
        )
        positives = {item["id"] for item in manifest["positive"]}
        negatives = {item["id"] for item in manifest["negative"]}
        self.assertEqual(
            positives,
            {
                "linux-minimal-workspace",
                "macos-full-cleanup",
                "baseline-toolchain",
                "exact-private-dependency",
                "clean-zero-artifact-policy",
                "deterministic-redacted-evidence",
                "cache-disabled-default",
                "bounded-android-diagnostics",
            },
        )
        self.assertEqual(
            negatives,
            {
                "path-traversal",
                "symlink-escape",
                "malicious-deletion-target",
                "interrupted-execution",
                "partial-setup",
                "credential-residue",
                "dirty-tree",
                "generated-output-drift",
                "token-like-content",
                "forbidden-file",
                "tracked-symlink-escape",
                "undeclared-artifact",
                "cache-poisoning-scope",
                "checksum-mismatch",
                "redirect-host-change",
                "unsafe-evidence-field",
                "cleanup-residue",
                "failed-command",
                "artifact-exception-limit",
                "runtime-capability-mismatch",
            },
        )

    def test_actions_are_thin_composites_calling_only_the_bounded_ciw_registry(self) -> None:
        for action, commands in ACTIONS.items():
            with self.subTest(action=action):
                path = ROOT / "actions" / action / "action.yml"
                data = yaml.safe_load(path.read_text(encoding="utf-8"))
                self.assertEqual(data["runs"]["using"], "composite")
                steps = data["runs"]["steps"]
                self.assertEqual(len(steps), 1)
                run = steps[0]["run"]
                self.assertIn("scripts/ci/ciw.py", run)
                for command in commands:
                    self.assertIn(command, run)
                if action == "verify-toolchain":
                    self.assertIn('case "${INPUT_OPERATION}"', run)
                    self.assertIn("verify-set)", run)
                    self.assertIn("install-asset)", run)
                self.assertNotIn("eval ", run)
                self.assertNotIn("source ", run)
                self.assertNotIn("curl ", run)
                self.assertNotIn("rm -rf", run)
                self.assertTrue(set(data.get("inputs", {})).isdisjoint(FORBIDDEN_INPUTS))

    def test_actions_catalog_lists_the_bounded_sequence_and_cleanup_duty(self) -> None:
        catalog = (ROOT / "actions/README.md").read_text(encoding="utf-8")
        for action in ACTIONS:
            self.assertIn(f"`{action}`", catalog)
        self.assertIn("if: always()", catalog)
        self.assertIn("Callers never supply a deletion path", catalog)
        self.assertIn("Caching remains disabled by default", catalog)
        self.assertIn("routine GitHub Actions artifacts remain zero", catalog)
        self.assertIn("`ciw`", catalog)

    def test_documented_functions_exist_as_named_public_definitions(self) -> None:
        contract = json.loads(
            (ROOT / "contracts/foundation-primitives.json").read_text(encoding="utf-8")
        )
        for module in contract["modules"]:
            relative = module["module"].removeprefix("ci_workflows.").replace(".", "/")
            source = ROOT / "src/ci_workflows" / f"{relative}.py"
            tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
            functions = {
                node.name
                for node in tree.body
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and not node.name.startswith("_")
            }
            self.assertTrue({item["name"] for item in module["functions"]} <= functions)

    def test_generated_documentation_is_exact_and_records_cleanup_constraints(self) -> None:
        rendered = render_foundation_docs(contract_root=ROOT)
        checked_in = (ROOT / "docs/architecture/foundation-primitives.md").read_text(
            encoding="utf-8"
        )
        self.assertEqual(checked_in, rendered)
        for token in (
            "if: always()",
            "source.resolve",
            "zero",
            "disabled",
            "explicit repository-owner bootstrap authorization",
            "ciw",
        ):
            self.assertIn(token, checked_in)

    def test_contracts_are_bounded_and_cache_artifact_defaults_are_zero(self) -> None:
        workspace = json.loads((ROOT / "contracts/workspace-paths.json").read_text())
        cache = json.loads((ROOT / "contracts/cache-policy.json").read_text())
        artifacts = json.loads((ROOT / "contracts/artifact-exceptions.json").read_text())
        tools = json.loads((ROOT / "contracts/tool-lock.json").read_text())
        self.assertEqual(set(workspace["supported_os"]), {"Linux", "macOS"})
        self.assertEqual(cache["default_mode"], "disabled")
        self.assertFalse(cache["modes"]["disabled"]["restore"])
        self.assertFalse(cache["modes"]["disabled"]["save"])
        self.assertEqual(artifacts["default"], "zero-artifacts")
        self.assertEqual(
            {item["id"] for item in artifacts["exceptions"]},
            {"android-validation-diagnostics"},
        )
        exception = artifacts["exceptions"][0]
        self.assertEqual(exception["maximum_count"], 1)
        self.assertLessEqual(exception["maximum_retention_days"], 3)
        self.assertEqual(
            exception["allowed_names"],
            ["android-validation-diagnostics"],
        )
        self.assertTrue(tools["download_policy"]["mutable_fallback_forbidden"])
        self.assertEqual(tools["download_policy"]["required_digest"], "sha256")


if __name__ == "__main__":
    unittest.main()
