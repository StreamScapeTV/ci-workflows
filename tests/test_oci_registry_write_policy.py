from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from ci_workflows import oci_execution
from ci_workflows import oci_publish as publication
from ci_workflows.oci_contract import load_contract
from ci_workflows.oci_publish import OciPublishError, PublishRequest


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/oci-publish/oci-products.json"
SHA = "a" * 40


def _request(
    repository: str = "StreamScapeTV/backend",
    product_id: str = "backend-image",
) -> PublishRequest:
    return PublishRequest(
        repository=repository,
        admitted_sha=SHA,
        release_authority_sha=SHA,
        product_id=product_id,
        release_version="1.2.3",
        source_trust="trusted-exact",
    )


class RegistryWritePolicyTests(unittest.TestCase):
    def _fixture_root(self) -> tempfile.TemporaryDirectory[str]:
        temporary = tempfile.TemporaryDirectory()
        contract_directory = Path(temporary.name) / "contracts"
        contract_directory.mkdir()
        shutil.copyfile(FIXTURE, contract_directory / "oci-products.json")
        return temporary

    def test_checked_in_product_and_smoke_policies_are_blocked(self) -> None:
        payload = json.loads(
            (ROOT / "contracts/oci-products.json").read_text(encoding="utf-8")
        )
        policies = payload["registry_write_policies"]
        self.assertTrue(policies)
        for product_id, product in payload["products"].items():
            with self.subTest(product_id=product_id):
                policy = policies[product["registry_write_policy_id"]]
                self.assertEqual("blocked", policy["status"])
                self.assertIsNone(policy["authority_source_sha"])
                self.assertIsNone(policy["evidence_id"])
                with tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    (root / "contracts").mkdir()
                    (root / "contracts/oci-products.json").write_text(
                        json.dumps(payload), encoding="utf-8"
                    )
                    with self.assertRaisesRegex(
                        OciPublishError, "registry_write_policy_not_ready"
                    ):
                        publication.resolve_plan(
                            root,
                            _request(product["repository"], product_id),
                        )

    def test_adoption_flip_alone_does_not_enable_publication(self) -> None:
        payload = json.loads(
            (ROOT / "contracts/oci-products.json").read_text(encoding="utf-8")
        )
        payload["products"]["iptv-backend-image"]["adoption_ready"] = True
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "contracts").mkdir()
            (root / "contracts/oci-products.json").write_text(
                json.dumps(payload), encoding="utf-8"
            )
            with self.assertRaisesRegex(
                OciPublishError, "registry_write_policy_not_ready"
            ):
                publication.resolve_plan(
                    root,
                    _request(
                        "StreamScapeTV/iptv-backend", "iptv-backend-image"
                    ),
                )

    def test_verified_synthetic_policy_is_bound_to_plan(self) -> None:
        with self._fixture_root() as directory:
            plan = publication.resolve_plan(Path(directory), _request())
        self.assertEqual("fixture-create-only-v1", plan.registry_write_policy.policy_id)
        self.assertEqual(
            {
                "policy_id": "fixture-create-only-v1",
                "registry_host": "registry.example.invalid",
                "required_enforcement": "server-side-create-only-tags-v1",
                "status": "verified",
                "authority_repository": "StreamScapeTV/flux",
                "authority_source_sha": "1" * 40,
                "evidence_id": "sha256:" + "2" * 64,
            },
            publication.registry_write_policy_evidence(plan),
        )

    def test_policy_contract_is_closed_and_host_bound(self) -> None:
        mutations = {
            "open": lambda value: value["registry_write_policies"][
                "fixture-create-only-v1"
            ].update({"secret_name": "REGISTRY_TOKEN"}),
            "blocked_with_evidence": lambda value: value[
                "registry_write_policies"
            ]["fixture-create-only-v1"].update({"status": "blocked"}),
            "host_mismatch": lambda value: value["registry_write_policies"][
                "fixture-create-only-v1"
            ].update({"registry_host": "other.example.invalid"}),
            "unused_malformed_host": lambda value: value[
                "registry_write_policies"
            ].update(
                {
                    "unused-create-only-v1": {
                        **value["registry_write_policies"][
                            "fixture-create-only-v1"
                        ],
                        "registry_host": "evil.example.invalid/path",
                    }
                }
            ),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name), self._fixture_root() as directory:
                path = Path(directory) / "contracts/oci-products.json"
                payload = json.loads(path.read_text(encoding="utf-8"))
                mutate(payload)
                path.write_text(json.dumps(payload), encoding="utf-8")
                with self.assertRaisesRegex(OciPublishError, "invalid_contract"):
                    publication.resolve_plan(Path(directory), _request())

    def test_build_contract_rejects_open_or_mismatched_policy(self) -> None:
        payload = json.loads(
            (ROOT / "contracts/oci-products.json").read_text(encoding="utf-8")
        )
        policy_id = payload["products"]["iptv-backend-image"][
            "registry_write_policy_id"
        ]
        for name, update in (
            ("open", {"callback": "https://example.invalid"}),
            ("host", {"registry_host": "other.example.invalid"}),
            ("verified_without_proof", {"status": "verified"}),
        ):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / "contracts").mkdir()
                mutated = json.loads(json.dumps(payload))
                mutated["registry_write_policies"][policy_id].update(update)
                (root / "contracts/oci-products.json").write_text(
                    json.dumps(mutated), encoding="utf-8"
                )
                with self.assertRaisesRegex(Exception, "invalid_contract"):
                    load_contract(root)

    def test_public_request_exposes_no_registry_policy_field(self) -> None:
        schema = json.loads(
            (ROOT / "contracts/oci-publication.schema.json").read_text(
                encoding="utf-8"
            )
        )
        request = schema["$defs"]["request"]
        self.assertFalse(request["additionalProperties"])
        self.assertEqual(
            {
                "admitted_sha",
                "product_id",
                "release_version",
                "platform_set",
            },
            set(request["properties"]),
        )

    def test_authenticated_plan_state_rejects_policy_substitution(self) -> None:
        with self._fixture_root() as directory:
            root = Path(directory)
            plan = publication.resolve_plan(root, _request())
            roots = oci_execution._test_capacity_roots(  # noqa: SLF001
                root / "capacity",
                domain="oci-publish",
                prefix="ciw-oci-publish",
            )
            oci_execution.prepare_capacity_roots(roots)
            (roots.scratch_root / "registry-auth.json").write_text(
                "{}\n", encoding="utf-8"
            )
            (roots.scratch_root / "registry-auth.json").chmod(0o600)
            publication._write_publication_plan_state(plan, roots)  # noqa: SLF001
            substituted = replace(
                plan,
                registry_write_policy=replace(
                    plan.registry_write_policy,
                    authority_source_sha="3" * 40,
                ),
            )
            with self.assertRaisesRegex(
                OciPublishError, "publication_state_missing"
            ):
                publication._load_publication_plan_state(  # noqa: SLF001
                    substituted, roots
                )

    def test_forged_typed_policy_is_rejected_before_registry_login(self) -> None:
        mutations = {
            "policy_id": "Invalid",
            "registry_host": "other.example.invalid",
            "required_enforcement": "client-preflight-v1",
            "status": "blocked",
            "authority_repository": "StreamScapeTV/ci-workflows",
            "authority_source_sha": "main",
            "evidence_id": "unverified",
        }
        with self._fixture_root() as directory:
            plan = publication.resolve_plan(Path(directory), _request())
            for field, value in mutations.items():
                with self.subTest(field=field), patch.object(
                    publication, "_run"
                ) as registry_run:
                    forged = replace(
                        plan,
                        registry_write_policy=replace(
                            plan.registry_write_policy,
                            **{field: value},
                        ),
                    )
                    with self.assertRaisesRegex(
                        OciPublishError,
                        "invalid_contract",
                    ):
                        publication.authenticate(
                            forged, {}, "registry-user", "registry-token"
                        )
                    registry_run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
