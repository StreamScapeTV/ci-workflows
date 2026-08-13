from __future__ import annotations

import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ci_workflows.oci_input_contract import (  # noqa: E402
    load_input_lock_contract,
    validate_target_dockerfile_lock,
)


class OciProductSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.contract = json.loads(
            (ROOT / "contracts/oci-products.json").read_text(encoding="utf-8")
        )
        cls.product = cls.contract["products"]["ciw-oci-input-smoke"]
        cls.target = cls.product["targets"][0]
        cls.lock = load_input_lock_contract(
            ROOT,
            cls.target["build_input_lock_path"],
            product_id="ciw-oci-input-smoke",
            target_id=cls.target["target_id"],
            input_policy_id=cls.target["input_policy_id"],
            expected_platforms=("linux/amd64",),
        )

    def test_exact_source_lock_matches_every_smoke_dockerfile_stage(self) -> None:
        dockerfile = ROOT / self.target["dockerfile_path"]
        bases = validate_target_dockerfile_lock(
            dockerfile, self.lock, ("linux/amd64",)
        )
        self.assertEqual(1, len(bases))
        self.assertEqual(
            "docker.io/library/busybox@sha256:"
            "73aaf090f3d85aa34ee199857f03fa3a95c8ede2ffd4cc2cdb5b94e566b11662",
            bases[0].declared_reference,
        )
        self.assertEqual("final", bases[0].stage_marker)
        self.assertEqual("external", bases[0].kind)
        self.assertEqual(1, len(bases[0].platform_identities))
        identity = bases[0].platform_identities[0]
        self.assertEqual("linux/amd64", identity.platform)
        self.assertEqual(
            "sha256:b7f3d86d6e84fc17718c48bcde1450807faa2d56704205c697b4bd5df7b9e29f",
            identity.manifest_digest,
        )
        self.assertEqual(
            "sha256:b116e155074440ffd9e449559433feb4cd2341eb3554b1da1c638c976e56451d",
            identity.config_digest,
        )
        self.assertEqual("sha256:", self.lock.lock_digest[:7])

    def test_external_input_is_exact_bounded_and_consumed_networklessly(self) -> None:
        self.assertEqual(1, len(self.lock.external_inputs))
        external = self.lock.external_inputs[0]
        self.assertEqual("busybox-readme", external.input_id)
        self.assertEqual(
            "https://raw.githubusercontent.com/docker-library/busybox/"
            "9c2087811429374737832a4c936fc411c1b4a22b/README.md",
            external.url,
        )
        self.assertEqual(
            "17f3e2d1c534738042206970bcf147badf46a95343d3e5ae9e3f88c9c9afa612",
            external.sha256,
        )
        self.assertEqual(4096, external.maximum_bytes)
        self.assertEqual(".ciw-build-inputs/README.md", external.destination)
        dockerfile = (
            ROOT / self.target["dockerfile_path"]
        ).read_text(encoding="utf-8")
        self.assertIn(
            "COPY --chmod=0444 .ciw-build-inputs/README.md /ciw-input/README.md",
            dockerfile,
        )
        self.assertNotRegex(dockerfile, r"(?m)^RUN\s")

    def test_source_lock_cannot_define_central_host_or_redirect_policy(self) -> None:
        source_lock = json.loads(
            (ROOT / self.target["build_input_lock_path"]).read_text(encoding="utf-8")
        )
        serialized = json.dumps(source_lock, sort_keys=True)
        for forbidden in (
            "allowed_registry_hosts",
            "allowed_registry_api_hosts",
            "allowed_registry_token_hosts",
            "allowed_registry_blob_hosts",
            "allowed_download_hosts",
            "redirect_policy",
            "ambient_auth",
        ):
            self.assertNotIn(forbidden, serialized)
        policy = self.contract["input_policies"][self.target["input_policy_id"]]
        self.assertEqual(
            {
                "allowed_registry_hosts",
                "allowed_registry_api_hosts",
                "allowed_registry_token_hosts",
                "allowed_registry_blob_hosts",
                "allowed_download_hosts",
                "https_only",
                "ambient_auth",
                "redirect_policy",
                "maximum_redirects",
                "maximum_input_bytes",
            },
            set(policy),
        )
        self.assertEqual(["docker.io"], policy["allowed_registry_hosts"])
        self.assertEqual(
            ["registry-1.docker.io"], policy["allowed_registry_api_hosts"]
        )
        self.assertEqual(
            ["auth.docker.io"], policy["allowed_registry_token_hosts"]
        )
        self.assertEqual(
            ["production.cloudfront.docker.com"],
            policy["allowed_registry_blob_hosts"],
        )
        self.assertEqual(
            ["raw.githubusercontent.com"], policy["allowed_download_hosts"]
        )

    def test_legacy_scratch_smoke_has_an_exact_empty_input_lock(self) -> None:
        target = self.contract["products"]["ciw-oci-smoke"]["targets"][0]
        lock = load_input_lock_contract(
            ROOT,
            target["build_input_lock_path"],
            product_id="ciw-oci-smoke",
            target_id="contract-smoke",
            input_policy_id="scratch-only-v1",
            expected_platforms=("linux/amd64",),
        )
        bases = validate_target_dockerfile_lock(
            ROOT / target["dockerfile_path"], lock, ("linux/amd64",)
        )
        self.assertEqual(1, len(bases))
        self.assertEqual("scratch", bases[0].kind)
        self.assertEqual("scratch", bases[0].declared_reference)
        self.assertEqual((), bases[0].platform_identities)
        self.assertEqual((), lock.external_inputs)
        self.assertEqual("sha256:", lock.lock_digest[:7])
        scratch_policy = self.contract["input_policies"]["scratch-only-v1"]
        for role in (
            "allowed_registry_hosts",
            "allowed_registry_api_hosts",
            "allowed_registry_token_hosts",
            "allowed_registry_blob_hosts",
            "allowed_download_hosts",
        ):
            self.assertEqual([], scratch_policy[role])

    def test_fixture_case_inventory_is_closed_and_unique(self) -> None:
        cases = json.loads(
            (ROOT / "tests/fixtures/oci-build/cases.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual({"schema_version", "positive", "negative"}, set(cases))
        self.assertEqual(1, cases["schema_version"])
        for group in ("positive", "negative"):
            self.assertEqual(len(cases[group]), len(set(cases[group])))
        self.assertEqual(
            {
                "empty-vfs-pinned-non-scratch",
                "pinned-index-multi-platform-identities",
                "locked-external-input-networkless-consumption",
                "all-inputs-verified-before-bud",
                "deterministic-redacted-input-evidence",
                "scratch-empty-input-lock",
                "success-zero-input-residue",
            },
            set(cases["positive"])
            & {
                "empty-vfs-pinned-non-scratch",
                "pinned-index-multi-platform-identities",
                "locked-external-input-networkless-consumption",
                "all-inputs-verified-before-bud",
                "deterministic-redacted-input-evidence",
                "scratch-empty-input-lock",
                "success-zero-input-residue",
            },
        )
        required_negative = {
            "arg-selected-base",
            "undeclared-stage",
            "missing-stage-lock",
            "duplicate-stage-lock",
            "incomplete-platform-lock",
            "wrong-root-digest",
            "wrong-platform-manifest",
            "wrong-platform-config",
            "duplicate-input-id",
            "duplicate-input-destination",
            "http-input-url",
            "unapproved-input-host",
            "private-or-ip-input-host",
            "registry-api-host-role-confusion",
            "registry-token-host-role-confusion",
            "registry-blob-redirect-host-role-confusion",
            "credentialed-input-url",
            "wrong-input-digest",
            "input-redirect-outside-policy",
            "oversize-input",
            "unsafe-input-path",
            "reserved-path-collision",
            "symlink-input-destination",
            "ambient-auth",
            "partial-download-state",
            "acquisition-failure-residue",
            "build-failure-residue",
        }
        self.assertTrue(required_negative <= set(cases["negative"]))


if __name__ == "__main__":
    unittest.main()
