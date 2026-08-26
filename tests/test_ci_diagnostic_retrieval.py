from __future__ import annotations

import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class DiagnosticWithdrawalContractTests(unittest.TestCase):
    def test_broker_has_no_public_diagnostics_process_or_chart_surface(self) -> None:
        container = (ROOT / "broker/Containerfile").read_text(encoding="utf-8")
        values = (ROOT / "charts/ci-broker/values.yaml").read_text(encoding="utf-8")
        schema = (ROOT / "charts/ci-broker/values.schema.json").read_text(encoding="utf-8")

        self.assertNotIn("ci_diagnostics.py", container)
        self.assertNotIn("EXPOSE 8081", container)
        self.assertNotIn("diagnostics:", values)
        self.assertNotIn('"diagnostics"', schema)
        self.assertFalse((ROOT / "charts/ci-broker/templates/diagnostics-deployment.yaml").exists())
        self.assertFalse((ROOT / "charts/ci-broker/templates/diagnostics-service.yaml").exists())
        self.assertFalse((ROOT / "scripts/ci/ci_diagnostics.py").exists())
        self.assertFalse((ROOT / "src/ci_workflows/ci_diagnostics.py").exists())

    def test_private_log_contract_requires_direct_mcp_r2_retrieval(self) -> None:
        contract = json.loads((ROOT / "contracts/ci-diagnostics.json").read_text(encoding="utf-8"))
        retrieval = contract["retrieval"]

        self.assertEqual(retrieval["mode"], "lowercase-cloudflare-mcp-direct-r2")
        self.assertTrue(retrieval["requires_exact_agent_state_receipt"])
        self.assertTrue(retrieval["requires_capability_object_key"])
        self.assertTrue(retrieval["compressed_sha256_verified_before_decompression"])
        self.assertFalse(retrieval["agent_state_lookup_by_reader"])
        self.assertFalse(retrieval["thin_relay_authority"])
        self.assertFalse(retrieval["public_http_reader"])
        self.assertTrue(contract["agent_state"]["raw_logs_forbidden"])
        self.assertFalse(contract["write_policy"]["github_actions_artifact"])

        docs = (ROOT / "docs/workflows/ci-diagnostics.md").read_text(encoding="utf-8").lower()
        self.assertIn("lowercase", docs)
        self.assertIn("cloudflare", docs)
        self.assertIn("sha-256", docs)
        self.assertIn("before", docs)
        self.assertIn("decompress", docs)
        self.assertIn("withdrawn", docs)
        self.assertNotIn("/diagnostics/<receipt-capability>", docs)


if __name__ == "__main__":
    unittest.main()
