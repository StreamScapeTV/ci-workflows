from __future__ import annotations

import io
import json
from pathlib import Path
import tempfile
import unittest

from ci_workflows import ciw
from ci_workflows.oci_publish import OciPublishError
from ci_workflows.oci_supply_evidence import build_supply_evidence

ROOT = Path(__file__).resolve().parents[1]
SHA = "a" * 40
HELPER_SHA = "b" * 40
PUBLICATION_EVIDENCE = "c" * 64
FOUNDATION_EVIDENCE = "evidence-" + "d" * 28
TOOLCHAIN = '{"buildah":"1.33.7","podman":"4.9.3","skopeo":"1.13.3"}'


def _kwargs() -> dict[str, object]:
    return {
        "root": ROOT,
        "central_workflow_sha": SHA,
        "publication_helper_sha": HELPER_SHA,
        "builder_id": "buildah-v1",
        "runner_profile": "buildah-medium",
        "toolchain_json": TOOLCHAIN,
        "publication_evidence_id": PUBLICATION_EVIDENCE,
        "foundation_evidence_id": FOUNDATION_EVIDENCE,
        "source_sha": "e" * 40,
        "product_id": "iptv-backend-image",
        "release_version": "1.2.3",
        "execution_result": "success",
        "build_cleanup_outcome": "success",
        "build_residue_outcome": "success",
        "publication_cleanup_outcome": "success",
        "publication_residue_outcome": "success",
        "workspace_cleanup_outcome": "success",
    }


class OciTerminalSupplyEvidenceTests(unittest.TestCase):
    def test_record_is_canonical_deterministic_redacted_and_complete(self) -> None:
        first = build_supply_evidence(**_kwargs())
        second = build_supply_evidence(**_kwargs())
        self.assertEqual(first, second)
        self.assertEqual(
            first.json_text,
            json.dumps(first.payload, sort_keys=True, separators=(",", ":")),
        )
        self.assertEqual(first.evidence_id, first.payload["supply_evidence_id"])
        self.assertEqual(
            first.payload["central"],
            {
                "builder_id": "buildah-v1",
                "publication_helper_sha": HELPER_SHA,
                "runner_profile": "buildah-medium",
                "workflow_sha": SHA,
            },
        )
        self.assertEqual(
            first.payload["toolchain"],
            {"buildah": "1.33.7", "podman": "4.9.3", "skopeo": "1.13.3"},
        )
        cleanup = first.payload["cleanup"]
        self.assertIsInstance(cleanup, dict)
        self.assertEqual(cleanup["terminal_result"], "success")
        self.assertEqual(cleanup["workspace"], {"cleanup": "success"})
        for forbidden in ("/Users/", "/home/", "registry_token", "https://"):
            self.assertNotIn(forbidden, first.json_text)

    def test_any_cleanup_failure_is_bound_into_terminal_failure(self) -> None:
        values = _kwargs()
        values["workspace_cleanup_outcome"] = "failure"
        evidence = build_supply_evidence(**values)
        cleanup = evidence.payload["cleanup"]
        self.assertIsInstance(cleanup, dict)
        self.assertEqual(cleanup["terminal_result"], "failure")
        self.assertEqual(cleanup["workspace"], {"cleanup": "failure"})

    def test_toolchain_and_terminal_identities_fail_closed(self) -> None:
        mutations = {
            "central_workflow_sha": "main",
            "publication_helper_sha": "b" * 39,
            "publication_evidence_id": "c" * 63,
            "foundation_evidence_id": "foundation-unknown",
            "execution_result": "passed",
            "workspace_cleanup_outcome": "cancelled",
            "toolchain_json": '{"buildah":"latest"}',
        }
        for name, value in mutations.items():
            with self.subTest(name=name):
                inputs = _kwargs()
                inputs[name] = value
                with self.assertRaises(OciPublishError):
                    build_supply_evidence(**inputs)

    def test_registered_final_phase_appends_summary_from_contract_owned_plan(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "output"
            summary = Path(directory) / "summary"
            errors = io.StringIO()
            environment = {
                "GITHUB_OUTPUT": str(output),
                "GITHUB_STEP_SUMMARY": str(summary),
                "GITHUB_REPOSITORY": "StreamScapeTV/iptv-backend",
                "GITHUB_EVENT_NAME": "push",
                "INPUT_ADMITTED_SHA": "e" * 40,
                "INPUT_RELEASE_AUTHORITY_SHA": "e" * 40,
                "INPUT_PRODUCT_ID": "iptv-backend-image",
                "INPUT_RELEASE_VERSION": "1.2.3",
                "INPUT_PLATFORM_SET": "linux-multi-arch",
                "INPUT_CENTRAL_WORKFLOW_SHA": SHA,
                "INPUT_PUBLICATION_HELPER_SHA": HELPER_SHA,
                "INPUT_VERIFIED_TOOLCHAIN_JSON": TOOLCHAIN,
                "INPUT_PUBLICATION_EVIDENCE_ID": PUBLICATION_EVIDENCE,
                "INPUT_FOUNDATION_EVIDENCE_ID": FOUNDATION_EVIDENCE,
                "INPUT_EXECUTION_RESULT": "success",
                "INPUT_BUILD_CLEANUP_OUTCOME": "success",
                "INPUT_BUILD_RESIDUE_OUTCOME": "success",
                "INPUT_PUBLICATION_CLEANUP_OUTCOME": "success",
                "INPUT_PUBLICATION_RESIDUE_OUTCOME": "success",
                "INPUT_WORKSPACE_CLEANUP_OUTCOME": "success",
            }
            code = ciw.main(
                ["--root", str(ROOT), "oci", "publish", "--phase", "final-evidence"],
                environment=environment,
                stdout=io.StringIO(),
                stderr=errors,
            )
            self.assertEqual(0, code, errors.getvalue())
            outputs = dict(
                line.split("=", 1)
                for line in output.read_text(encoding="utf-8").splitlines()
            )
            self.assertEqual("success", outputs["result"])
            self.assertRegex(outputs["supply_evidence_id"], r"^[0-9a-f]{64}$")
            summary_text = summary.read_text(encoding="utf-8")
            self.assertEqual(1, summary_text.count("## Final OCI supply evidence"))
            payload = json.loads(
                summary_text.split("```json\n", 1)[1].split("\n```", 1)[0]
            )
            self.assertEqual("buildah-v1", payload["central"]["builder_id"])
            self.assertEqual("buildah-medium", payload["central"]["runner_profile"])
            self.assertEqual(
                outputs["supply_evidence_id"], payload["supply_evidence_id"]
            )


if __name__ == "__main__":
    unittest.main()
