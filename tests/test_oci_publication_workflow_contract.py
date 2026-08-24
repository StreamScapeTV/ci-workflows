from __future__ import annotations

import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class OciPublicationInternalContractTests(unittest.TestCase):
    def test_unconsumed_public_publish_facade_is_retired(self) -> None:
        self.assertFalse((ROOT / ".github/workflows/reusable-oci-publish.yml").exists())
        products = json.loads(
            (ROOT / "contracts/public-workflows/products.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertNotIn(
            "oci.publish",
            {row["api_name"] for row in products["workflows"]},
        )

    def test_composite_action_remains_thin_and_has_no_caller_destination_or_command(self) -> None:
        text = (ROOT / "actions/publish-oci/action.yml").read_text(encoding="utf-8")
        self.assertIn("scripts/ci/ciw.py", text)
        self.assertIn("oci publish --phase", text)
        self.assertNotIn("scripts/ci/oci_publish.py", text)
        self.assertNotIn("registry_repository:", text)
        self.assertNotIn("registry_host:", text)
        self.assertNotIn("command:", text)
        self.assertNotIn("runner_labels:", text)
        self.assertNotIn("secrets: inherit", text)

    def test_mock_smoke_has_no_registry_credentials_or_artifacts(self) -> None:
        text = (ROOT / ".github/workflows/oci-publish-smoke.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("pull_request:", text)
        self.assertIn("runs-on: [ubuntu-latest]", text)
        self.assertNotIn("runs-on: [linux, amd64", text)
        self.assertNotIn("registry_token", text)
        self.assertNotIn("registry_username", text)
        self.assertNotIn("upload-artifact", text)
        self.assertNotIn("packages: write", text)

    def test_internal_publication_schema_remains_destination_and_command_closed(self) -> None:
        schema = json.loads(
            (ROOT / "contracts/oci-publication.schema.json").read_text(
                encoding="utf-8"
            )
        )
        request = schema["$defs"]["request"]
        self.assertFalse(request["additionalProperties"])
        self.assertEqual(
            set(request["properties"]),
            {"admitted_sha", "product_id", "release_version", "platform_set"},
        )


if __name__ == "__main__":
    unittest.main()
