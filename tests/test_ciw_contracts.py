from __future__ import annotations

import argparse
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from ci_workflows.ciw import command_specs, runtime_command_index, validate_runtime_contract
from ci_workflows.ciw_docs import load_command_contract, validate_command_contract
from ci_workflows.ciw_types import (
    CIWContext,
    CIWError,
    CIWResult,
    project_error,
    write_command_file,
)
from ci_workflows.foundation_types import FoundationError
from ci_workflows.gitops_types import GitOpsValidationError
from ci_workflows.node_types import NodeValidationError
from ci_workflows.oci_publish import OciPublishError
from ci_workflows.oci_types import OciBuildError
from ci_workflows.python_types import PythonValidationError
from ci_workflows.release_tag_authority import ReleaseTagError
from ci_workflows.runners import RunnerContractError
from ci_workflows.source_types import SourceAdmissionError

ROOT = Path(__file__).resolve().parents[1]


class CIWContractTests(unittest.TestCase):
    def test_checked_in_contract_and_runtime_registry_agree_exactly(self) -> None:
        contract = load_command_contract(ROOT)
        expected = {
            f"{item['domain']} {item['operation']}"
            for item in contract["commands"]
        }
        self.assertEqual(expected, set(runtime_command_index()))
        self.assertEqual(26, len(expected))
        self.assertIn("android validate", expected)
        self.assertIn("apple validate", expected)
        self.assertIn("flutter validate", expected)
        self.assertIn("python validate", expected)
        self.assertIn("node validate", expected)
        self.assertIn("gitops validate", expected)
        self.assertIn("oci publish", expected)
        self.assertIn("oci validate", expected)
        self.assertEqual(len(command_specs()), len(expected))
        validate_runtime_contract(ROOT)

    def test_oci_publish_handler_action_and_command_outputs_agree_exactly(self) -> None:
        contract = load_command_contract(ROOT)
        command = next(
            item
            for item in contract["commands"]
            if item["domain"] == "oci" and item["operation"] == "publish"
        )
        expected = set(command["outputs"])
        spec = runtime_command_index()["oci publish"]
        self.assertEqual(command["handler"], spec.qualified_handler)

        action = yaml.safe_load(
            (ROOT / "actions/publish-oci/action.yml").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(expected, set(action["outputs"]))
        self.assertIn("manifest_digests_json", expected)
        self.assertNotIn("image_digest", expected)

        projected = {name: "value" for name in expected}
        delegated = CIWResult("oci", "publish", outputs=projected)
        context = CIWContext(
            root=ROOT,
            environment={},
            stdout=io.StringIO(),
            stderr=io.StringIO(),
        )
        with patch(
            "ci_workflows.ciw.execute_oci_publish", return_value=delegated
        ):
            result = spec.handler(argparse.Namespace(), context)
        self.assertEqual(projected, result.outputs)

    def test_oci_validate_action_projects_internal_publication_digest(self) -> None:
        contract = load_command_contract(ROOT)
        command = next(
            item
            for item in contract["commands"]
            if item["domain"] == "oci" and item["operation"] == "validate"
        )
        action = yaml.safe_load(
            (ROOT / "actions/validate-oci/action.yml").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(set(command["outputs"]), set(action["outputs"]))
        self.assertIn("manifest_digests_json", action["outputs"])
        self.assertIn("publication_manifest_digests_json", action["outputs"])

    def test_contract_exposes_only_the_bounded_domain_tree(self) -> None:
        commands = set(validate_command_contract(
            json.loads((ROOT / "contracts/ciw-commands.json").read_text(encoding="utf-8"))
        ))
        self.assertEqual(
            {item.split(" ", 1)[0] for item in commands},
            {
                "source",
                "runners",
                "android",
                "apple",
                "flutter",
                "python",
                "node",
                "gitops",
                "oci",
                "workspace",
                "tooling",
                "dependencies",
                "policy",
                "evidence",
                "release-tag",
            },
        )
        contract = load_command_contract(ROOT)
        self.assertTrue(
            set(contract["future_namespaces"]).isdisjoint(
                {item.split(" ", 1)[0] for item in commands}
            )
        )
        self.assertEqual(contract["aliases"], {})

    def test_compatibility_wrappers_are_checked_in_and_cover_existing_entry_points(self) -> None:
        contract = load_command_contract(ROOT)
        wrappers = {item["path"]: set(item["commands"]) for item in contract["compatibility_wrappers"]}
        self.assertEqual(
            set(wrappers),
            {
                "scripts/ci/resolve_source.py",
                "scripts/ci/runner_contract.py",
                "scripts/ci/foundation.py",
                "scripts/ci/release_tag_authority.py",
                "scripts/ci/android.py",
                "scripts/ci/apple.py",
                "scripts/ci/python.py",
                "scripts/ci/node.py",
                "scripts/ci/gitops.py",
                "scripts/ci/oci.py",
                "scripts/ci/oci_publish.py",
            },
        )
        self.assertEqual(wrappers["scripts/ci/android.py"], {"android validate"})
        self.assertEqual(wrappers["scripts/ci/apple.py"], {"apple validate"})
        self.assertEqual(wrappers["scripts/ci/python.py"], {"python validate"})
        self.assertEqual(wrappers["scripts/ci/node.py"], {"node validate"})
        self.assertEqual(wrappers["scripts/ci/gitops.py"], {"gitops validate"})
        self.assertEqual(wrappers["scripts/ci/oci.py"], {"oci validate"})
        self.assertEqual(wrappers["scripts/ci/oci_publish.py"], {"oci publish"})
        for path in wrappers:
            self.assertTrue((ROOT / path).is_file())

    def test_error_projection_preserves_domain_codes_and_redacts_unexpected_errors(self) -> None:
        cases = (
            (SourceAdmissionError("stale_pr_head"), "source", "stale_pr_head"),
            (RunnerContractError("invalid-selector", "private detail"), "runners", "invalid-selector"),
            (PythonValidationError("dependency_lock_drift"), "python", "dependency_lock_drift"),
            (NodeValidationError("lockfile_drift"), "node", "lockfile_drift"),
            (GitOpsValidationError("tool_archive_rejected"), "gitops", "tool_archive_rejected"),
            (OciBuildError("oci_layout_malformed"), "oci", "oci_layout_malformed"),
            (OciPublishError("registry_readback_mismatch"), "oci", "registry_readback_mismatch"),
            (FoundationError("cleanup_residue_detected"), "workspace", "cleanup_residue_detected"),
            (ReleaseTagError("release_tag_moved"), "release-tag", "release_tag_moved"),
        )
        for error, domain, expected in cases:
            with self.subTest(domain=domain):
                projected = project_error(error, domain=domain)
                self.assertEqual(expected, projected.code)
                self.assertEqual(2, projected.exit_code)
                self.assertNotIn("private detail", str(projected))
        unexpected = project_error(RuntimeError("token=private"), domain="policy")
        self.assertEqual("ciw_unexpected_failure", unexpected.code)
        self.assertNotIn("private", str(unexpected))

    def test_result_and_command_file_reject_newline_injection(self) -> None:
        with self.assertRaisesRegex(CIWError, "invalid_github_command_value"):
            CIWResult("source", "resolve", outputs={"source_sha": "a\nb"})
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "output"
            write_command_file(path, {"safe_name": "safe-value"})
            self.assertEqual("safe_name=safe-value\n", path.read_text(encoding="utf-8"))
            with self.assertRaisesRegex(CIWError, "invalid_github_command_value"):
                write_command_file(path, {"unsafe": "value\rnext"})


if __name__ == "__main__":
    unittest.main()
