from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from ci_workflows.oci_publish import PublishRequest, resolve_plan
from ci_workflows.oci_publish_assertions import assert_filesystem_contract
from ci_workflows.ciw_oci import _publication_summary
from tests.test_oci_publication_filesystem import ROOT, SHA, _layout


class PublicationAssertionEvidenceTests(unittest.TestCase):
    def test_run_summary_contains_bounded_immutable_assertion_proof(self) -> None:
        immutable = {
            "release": {"source_sha": SHA, "version": "1.2.3"},
            "targets": {
                "agent-state-api": {
                    "assertions": {
                        "result": "passed",
                        "runtime": {
                            "command": {
                                "count": 7,
                                "digest": "sha256:" + "1" * 64,
                            }
                        },
                    }
                }
            },
        }
        summary = _publication_summary(
            {
                "evidence_id": "2" * 64,
                "immutable_references_json": json.dumps(immutable),
            }
        )
        self.assertIn("Trusted OCI publication evidence", summary)
        self.assertIn("`" + "2" * 64 + "`", summary)
        self.assertIn('"result": "passed"', summary)
        self.assertNotIn("app.main:app", summary)

    def test_real_runtime_evidence_is_deterministic_and_redacts_commands(self) -> None:
        plan = resolve_plan(
            ROOT,
            PublishRequest(
                repository="StreamScapeTV/agent-state",
                admitted_sha=SHA,
                release_authority_sha=SHA,
                product_id="agent-state-image",
                release_version="1.2.3",
                source_trust="trusted-exact",
            ),
        )
        target = plan.targets[0]
        with tempfile.TemporaryDirectory() as directory:
            layout = _layout(
                Path(directory),
                ("/usr/local/bin/uvicorn",),
                target=target,
            )
            first = assert_filesystem_contract(ROOT, plan, target, layout)
            second = assert_filesystem_contract(ROOT, plan, target, layout)

        self.assertEqual(first, second)
        self.assertEqual(first["result"], "passed")
        self.assertEqual(first["verified_platforms"], list(target.platforms))
        self.assertRegex(first["contract_digest"], r"^sha256:[0-9a-f]{64}$")
        self.assertEqual(first["runtime"]["command"]["count"], 6)
        encoded = json.dumps(first, sort_keys=True, separators=(",", ":"))
        self.assertNotIn("app.main:app", encoded)
        self.assertNotIn("--host", encoded)
        self.assertNotIn("0.0.0.0", encoded)
        self.assertEqual(
            first["filesystem"]["required_executables"],
            ["/usr/local/bin/uvicorn"],
        )
        self.assertEqual(first["healthcheck"], {"mode": "absent"})

    def test_exact_healthcheck_evidence_hashes_the_test_vector(self) -> None:
        raw = json.loads(
            (ROOT / "contracts/oci-products.json").read_text(encoding="utf-8")
        )
        declared = {
            "test": ["CMD-SHELL", "curl -fsS http://127.0.0.1:8080/health"],
            "interval_nanoseconds": 30_000_000_000,
            "timeout_nanoseconds": 5_000_000_000,
            "start_period_nanoseconds": 10_000_000_000,
            "start_interval_nanoseconds": 1_000_000_000,
            "retries": 3,
        }
        raw["publication_assertions"]["iptv-backend-image"]["iptv-backend"][
            "healthcheck"
        ] = declared
        plan = resolve_plan(
            ROOT,
            PublishRequest(
                repository="StreamScapeTV/iptv-backend",
                admitted_sha=SHA,
                release_authority_sha=SHA,
                product_id="iptv-backend-image",
                release_version="1.2.3",
                source_trust="trusted-exact",
            ),
        )
        target = plan.targets[0]
        image_healthcheck = {
            "Test": declared["test"],
            "Interval": declared["interval_nanoseconds"],
            "Timeout": declared["timeout_nanoseconds"],
            "StartPeriod": declared["start_period_nanoseconds"],
            "StartInterval": declared["start_interval_nanoseconds"],
            "Retries": declared["retries"],
        }
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            contract_path = temporary / "contracts/oci-products.json"
            contract_path.parent.mkdir()
            contract_path.write_text(json.dumps(raw), encoding="utf-8")
            layout = _layout(
                temporary / "image",
                (
                    "/app/docker/start.sh",
                    "/usr/local/bin/python3",
                    "/usr/local/bin/python3.12",
                ),
                symlinks={"/usr/local/bin/python3": "python3.12"},
                healthcheck=image_healthcheck,
                target=target,
            )
            evidence = assert_filesystem_contract(
                temporary, plan, target, layout
            )

        self.assertEqual(evidence["healthcheck"]["mode"], "exact")
        self.assertEqual(evidence["healthcheck"]["test_mode"], "CMD-SHELL")
        self.assertEqual(evidence["healthcheck"]["test"]["count"], 2)
        encoded = json.dumps(evidence, sort_keys=True, separators=(",", ":"))
        self.assertNotIn("curl", encoded)
        self.assertNotIn("127.0.0.1", encoded)


if __name__ == "__main__":
    unittest.main()
