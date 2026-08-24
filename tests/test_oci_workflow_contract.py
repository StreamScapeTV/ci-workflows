from __future__ import annotations

import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class OciInternalContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.action = (ROOT / "actions/validate-oci/action.yml").read_text()
        cls.contract = json.loads((ROOT / "contracts/oci-products.json").read_text())
        cls.schema = json.loads((ROOT / "contracts/oci-build.schema.json").read_text())

    def test_unconsumed_public_build_facade_and_contract_smoke_are_retired(self) -> None:
        self.assertFalse((ROOT / ".github/workflows/reusable-oci-build.yml").exists())
        self.assertFalse((ROOT / ".github/workflows/oci-build-smoke.yml").exists())
        products = json.loads(
            (ROOT / "contracts/public-workflows/products.json").read_text(encoding="utf-8")
        )
        self.assertNotIn("oci.build", {row["api_name"] for row in products["workflows"]})
        self.assertNotIn(
            ".github/workflows/reusable-oci-build.yml",
            {
                row["path"]
                for row in json.loads(
                    (ROOT / "contracts/bootstrap-public-workflows.json").read_text(
                        encoding="utf-8"
                    )
                )["allowed"]
            },
        )

    def test_action_remains_thin_and_internal_output_rich(self) -> None:
        self.assertIn("scripts/ci/ciw.py", self.action)
        self.assertIn("oci validate", self.action)
        self.assertIn("--phase", self.action)
        self.assertNotIn("scripts/ci/oci.py", self.action)
        self.assertNotIn("shell callback", self.action.lower())
        self.assertLess(len(self.action.splitlines()), 120)
        self.assertIn("resolved_inputs_json:", self.action)
        self.assertIn("runner_profile:", self.action)
        self.assertIn("runs_on_json:", self.action)

    def test_product_contract_covers_internal_build_products_without_public_api_promise(self) -> None:
        products = self.contract["products"]
        self.assertEqual(
            {
                "iptv-backend-image",
                "agent-state-image",
                "flux-runner-images",
                "ciw-oci-smoke",
                "ciw-oci-input-smoke",
            },
            set(products),
        )
        flux = products["flux-runner-images"]
        self.assertTrue(flux["independent_bootstrap"])
        self.assertTrue(flux["flux_asset"])
        self.assertEqual("buildah-high", flux["runner_profile"])
        smoke = products["ciw-oci-smoke"]["targets"][0]
        self.assertIsNone(smoke["smoke_script"])
        self.assertEqual(
            "tests/fixtures/oci-build/smoke/inputs.lock.json",
            smoke["build_input_lock_path"],
        )
        self.assertEqual("scratch-only-v1", smoke["input_policy_id"])
        input_smoke = products["ciw-oci-input-smoke"]
        self.assertTrue(input_smoke["adoption_ready"])
        input_target = input_smoke["targets"][0]
        self.assertEqual(
            "tests/fixtures/oci-build/input-smoke/inputs.lock.json",
            input_target["build_input_lock_path"],
        )
        self.assertEqual("oci-inputs-public-v1", input_target["input_policy_id"])
        for product_id in (
            "iptv-backend-image",
            "agent-state-image",
            "flux-runner-images",
        ):
            self.assertFalse(products[product_id]["adoption_ready"])

    def test_internal_input_policy_is_central_closed_and_never_caller_selected(self) -> None:
        self.assertEqual("1.1.0", self.contract["contract_version"])
        self.assertEqual(
            {"oci-inputs-public-v1", "scratch-only-v1"},
            set(self.contract["input_policies"]),
        )
        policy = self.contract["input_policies"]["oci-inputs-public-v1"]
        self.assertEqual(["docker.io"], policy["allowed_registry_hosts"])
        self.assertEqual(["registry-1.docker.io"], policy["allowed_registry_api_hosts"])
        self.assertEqual(["auth.docker.io"], policy["allowed_registry_token_hosts"])
        self.assertEqual(
            ["production.cloudfront.docker.com"],
            policy["allowed_registry_blob_hosts"],
        )
        self.assertEqual(["raw.githubusercontent.com"], policy["allowed_download_hosts"])
        self.assertTrue(policy["https_only"])
        self.assertFalse(policy["ambient_auth"])
        public_schema = json.dumps(self.schema, sort_keys=True)
        for forbidden in (
            "input_policy_id",
            "build_input_lock_path",
            "allowed_registry_hosts",
            "source_url",
        ):
            self.assertNotIn(forbidden, public_schema)


if __name__ == "__main__":
    unittest.main()
