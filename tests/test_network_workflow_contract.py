from __future__ import annotations

import json
import unittest
from pathlib import Path

import yaml

from ci_workflows.validation_model import ActionsLoader

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/reusable-network-download.yml"
ACTION = ROOT / "actions/network/action.yml"
BOOTSTRAP = ROOT / "contracts/bootstrap-public-workflows.json"
VALIDATION = ROOT / "contracts/public-workflows/validation.json"
TYPES = ROOT / "contracts/public-workflow-types.json"
DOC = ROOT / "docs/workflows/network.md"
NETWORK_CHECKPOINT = "cb28865f7990d2f4592ebc9d16e4c9bace56b805"
FOUNDATION_CHECKPOINT = "70e08d4ddf8930046632a7135950e924b82e22bf"


class NetworkWorkflowContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.source = WORKFLOW.read_text(encoding="utf-8")
        self.workflow = yaml.load(self.source, Loader=ActionsLoader)

    def test_public_workflow_is_call_only_read_only_and_zero_secret(self) -> None:
        self.assertEqual({"workflow_call"}, set(self.workflow["on"]))
        self.assertEqual({"contents": "read"}, self.workflow["permissions"])
        call = self.workflow["on"]["workflow_call"]
        self.assertNotIn("secrets", call)
        self.assertEqual(
            {
                "url",
                "relative_path",
                "expected_sha256",
                "expected_size",
                "expected_content_type",
                "maximum_bytes",
                "archive_format",
                "relative_destination",
            },
            set(call["inputs"]),
        )
        self.assertEqual(
            {
                "result",
                "download_result_json",
                "extraction_result_json",
                "cleanup_result",
            },
            set(call["outputs"]),
        )
        self.assertNotIn("local_path", call["outputs"])
        self.assertTrue(call["inputs"]["url"]["required"])
        self.assertEqual("dependency.bin", call["inputs"]["relative_path"]["default"])
        self.assertEqual(536870912, call["inputs"]["maximum_bytes"]["default"])
        self.assertEqual("none", call["inputs"]["archive_format"]["default"])
        self.assertEqual("extracted", call["inputs"]["relative_destination"]["default"])

    def test_workflow_uses_immutable_network_action_and_terminal_cleanup(self) -> None:
        job = self.workflow["jobs"]["network"]
        self.assertEqual("CI / Network download", job["name"])
        self.assertEqual(["linux", "amd64", "general", "small"], job["runs-on"])
        self.assertEqual(15, job["timeout-minutes"])
        steps = job["steps"]
        workspace = next(step for step in steps if step.get("id") == "workspace")
        download = next(step for step in steps if step.get("id") == "download")
        extract = next(step for step in steps if step.get("id") == "extract")
        cleanup = next(step for step in steps if step.get("id") == "cleanup")
        self.assertIn(f"@{FOUNDATION_CHECKPOINT}", workspace["uses"])
        self.assertEqual("disabled", workspace["with"]["cache_mode"])
        self.assertEqual(
            f"StreamScapeTV/ci-workflows/actions/network@{NETWORK_CHECKPOINT}",
            download["uses"].split(" #", 1)[0],
        )
        self.assertEqual(download["uses"], extract["uses"])
        self.assertEqual("download", download["with"]["operation"])
        self.assertEqual("extract", extract["with"]["operation"])
        self.assertEqual("inputs.archive_format != 'none'", extract["if"])
        self.assertEqual("always()", cleanup["if"])
        self.assertIn(f"@{FOUNDATION_CHECKPOINT}", cleanup["uses"])
        lowered = self.source.casefold()
        for forbidden in (
            "actions/cache",
            "upload-artifact",
            "download-artifact",
            "provenance",
            "oidc",
            "id-token: write",
            "secrets: inherit",
        ):
            self.assertNotIn(forbidden, lowered)

    def test_composite_action_exposes_local_path_only_for_same_job_use(self) -> None:
        action = yaml.load(ACTION.read_text(encoding="utf-8"), Loader=ActionsLoader)
        self.assertEqual("composite", action["runs"]["using"])
        self.assertIn("local_path", action["outputs"])
        self.assertIn("same job", action["outputs"]["local_path"]["description"])
        run = action["runs"]["steps"][0]["run"]
        self.assertIn("network run", run)
        self.assertIn("--operation", run)
        for forbidden in ("eval ", "bash -c", "sh -c"):
            self.assertNotIn(forbidden, run)

    def test_public_contract_and_type_catalog_match_workflow(self) -> None:
        validation = json.loads(VALIDATION.read_text(encoding="utf-8"))
        rows = {row["api_name"]: row for row in validation["workflows"]}
        row = rows["network.download"]
        self.assertEqual(str(WORKFLOW.relative_to(ROOT)), row["file"])
        self.assertEqual("1.0.0", row["api_version"])
        self.assertEqual("read-only-validation", row["trust_class"])
        self.assertEqual("validation-read", row["permission_profile"])
        self.assertEqual("portable", row["semantic_runner_profile"])
        self.assertEqual([], row["secrets"])
        self.assertEqual([], row["repository_owned_hooks"])
        self.assertEqual(
            list(self.workflow["on"]["workflow_call"]["inputs"]),
            [item["name"] for item in row["inputs"]],
        )
        self.assertEqual(
            set(self.workflow["on"]["workflow_call"]["outputs"]),
            set(row["outputs"]),
        )
        types = json.loads(TYPES.read_text(encoding="utf-8"))
        for item in row["inputs"]:
            self.assertIn(item["name"], types["input_catalog"])
        for output in row["outputs"]:
            self.assertIn(output, types["output_catalog"])
        self.assertEqual(["none", "zip", "tar"], types["input_catalog"]["archive_format"]["enum"])
        self.assertEqual(8589934592, types["input_catalog"]["maximum_bytes"]["maximum"])

    def test_bootstrap_allowlist_and_documentation_publish_the_boundary(self) -> None:
        bootstrap = json.loads(BOOTSTRAP.read_text(encoding="utf-8"))
        rows = {row["path"]: row for row in bootstrap["allowed"]}
        row = rows[str(WORKFLOW.relative_to(ROOT))]
        self.assertEqual(363, row["issue"])
        self.assertEqual("implemented-public-workflow", row["status"])
        self.assertEqual([], row["required_follow_up"])
        text = DOC.read_text(encoding="utf-8")
        for phrase in (
            "same job",
            "local_path",
            "does **not** expose a `local_path`",
            "does not create an artifact bridge",
            "HTTPS",
            "SHA-256",
            "ZIP and TAR",
            "if: always()",
            "Actions cache",
        ):
            self.assertIn(phrase, text)


if __name__ == "__main__":
    unittest.main()
