from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from ci_workflows.oci_publish_contract import (
    OciPublishError,
    PublishRequest,
    resolve_plan,
    verify,
)
from ci_workflows import oci_publish as publication_runtime
from ci_workflows.oci_types import OciBuildInputEvidence
from ci_workflows.issue_dependency_manifest import (
    ManifestValidationError,
    validate_json_schema,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/oci-publish/oci-products.json"
SHA = "a" * 40
DIGEST = "sha256:" + "1" * 64
MAX_OCI_TAG_VERSION = "1." + "1" * 124 + ".1"
OVERSIZED_OCI_TAG_VERSION = "1." + "1" * 125 + ".1"


def _resolved_inputs(
    platforms: tuple[str, ...] = ("linux/amd64",),
) -> dict[str, object]:
    payload = {
        "lock_digest": "sha256:" + "8" * 64,
        "input_policy_id": "oci-inputs-public-v1",
        "bases": [
            {
                "stage_id": "stage-1",
                "declared_reference": (
                    "example.invalid/base@sha256:" + "4" * 64
                ),
                "root_digest": "sha256:" + "4" * 64,
                "platforms": [
                    {
                        "platform": platform,
                        "manifest_digest": "sha256:" + "a" * 64,
                        "config_digest": "sha256:" + "b" * 64,
                    }
                    for platform in platforms
                ],
            }
        ],
        "external_inputs": [
            {
                "input_id": "runtime-config",
                "digest": "sha256:" + "c" * 64,
                "size_bytes": 4096,
            }
        ],
    }
    evidence_id = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return {**payload, "evidence_id": evidence_id}


def _assertion_evidence(
    platforms: tuple[str, ...] = ("linux/amd64",),
) -> dict[str, object]:
    return {
        "result": "passed",
        "verified_platforms": list(platforms),
        "contract_digest": "sha256:" + "5" * 64,
        "runtime": {
            "user": "runner",
            "entrypoint": {"count": 0, "digest": "sha256:" + "6" * 64},
            "command": {"count": 1, "digest": "sha256:" + "7" * 64},
            "ports": [],
        },
        "filesystem": {
            "required_files": [],
            "required_tools": ["buildah"],
            "required_executables": ["/usr/bin/buildah"],
            "forbidden_tools": ["docker"],
            "forbidden_paths": ["/var/run/docker.sock"],
        },
        "healthcheck": {"mode": "absent"},
    }


def _metadata_labels(target: object | None = None) -> dict[str, str]:
    target_id = getattr(target, "target_id", "runner-buildah")
    source_repository = getattr(target, "source_repository", "StreamScapeTV/flux")
    metadata = getattr(
        target,
        "metadata",
        {
            "title": "StreamScapeTV runner images",
            "description": "Inventory-controlled organization runner image family",
            "licenses": "Repository-defined",
        },
    )
    source_reference = getattr(target, "source_reference", "sha-" + SHA)
    version_reference = getattr(target, "version_reference", "image:1.2.3")
    return {
        "dev.streamscape.product": target_id,
        "org.opencontainers.image.created": "2026-08-12T00:00:00Z",
        "org.opencontainers.image.description": metadata["description"],
        "org.opencontainers.image.licenses": metadata["licenses"],
        "org.opencontainers.image.revision": source_reference.rsplit("sha-", 1)[1],
        "org.opencontainers.image.source": f"https://github.com/{source_repository}",
        "org.opencontainers.image.title": metadata["title"],
        "org.opencontainers.image.version": version_reference.rsplit(":", 1)[1],
    }


def _target_reference() -> dict[str, object]:
    repository = "git.faruqi.dev/mimranfaruqi/github-actions-runner-buildah"
    return {
        "repository": repository,
        "version": repository + ":1.2.3",
        "source_reference": repository + ":sha-" + SHA,
        "manifest_digest": DIGEST,
        "resolved_inputs": _resolved_inputs(),
        "assertions": _assertion_evidence(),
    }


def _registry_write_policy() -> dict[str, str]:
    return {
        "policy_id": "flux-runners-create-only-v1",
        "registry_host": "git.faruqi.dev",
        "required_enforcement": "server-side-create-only-tags-v1",
        "status": "verified",
        "authority_repository": "StreamScapeTV/flux",
        "authority_source_sha": "1" * 40,
        "evidence_id": "sha256:" + "2" * 64,
    }


class PublicSchemaParityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.schema = json.loads(
            (ROOT / "contracts/oci-publication.schema.json").read_text(
                encoding="utf-8"
            )
        )

    def _definition_schema(self, name: str) -> dict[str, object]:
        return {
            "$defs": self.schema["$defs"],
            "$ref": f"#/$defs/{name}",
        }

    def _object_schema(self, schema: dict[str, object]) -> dict[str, object]:
        return {"$defs": self.schema["$defs"], **schema}

    def _assert_rejected(self, value: object, schema: dict[str, object]) -> None:
        with self.assertRaises(ManifestValidationError):
            validate_json_schema(value, schema)

    def test_container_paths_match_runtime_canonicalization(self) -> None:
        schema = self._definition_schema("containerPath")
        for value in ("/app/start.sh", "/usr/local/bin/python3", "/a.b-c_d"):
            validate_json_schema(value, schema)
        for value in (
            "/",
            "//app/start.sh",
            "/app//start.sh",
            "/app/./start.sh",
            "/app/../start.sh",
            "/app/start.sh/",
            "/app/start.sh ",
        ):
            with self.subTest(value=value):
                self._assert_rejected(value, schema)

    def test_schema_destinations_equal_checked_in_public_product_authority(self) -> None:
        product_contract = json.loads(
            (ROOT / "contracts/oci-products.json").read_text(encoding="utf-8")
        )
        supported_products = (
            "agent-state-image",
            "flux-runner-images",
            "iptv-backend-image",
        )
        expected = sorted(
            target["publication_repository"]
            for product_id in supported_products
            for target in product_contract["products"][product_id]["targets"]
        )
        self.assertEqual(
            self.schema["$defs"]["repository"]["enum"],
            expected,
        )

    def test_resolved_inputs_and_registry_references_reject_open_or_noncanonical_values(self) -> None:
        input_schema = self._definition_schema("resolvedInputs")
        repository_schema = self._definition_schema("repository")
        tag_schema = self._definition_schema("immutableTagReference")
        validate_json_schema(_resolved_inputs(), input_schema)
        for key, value in (
            ("url", "https://secret.example/input"),
            ("destination", "/runner/work/private"),
            ("token", "secret"),
        ):
            opened = {**_resolved_inputs(), key: value}
            with self.subTest(key=key):
                self._assert_rejected(opened, input_schema)
        mutable = _resolved_inputs()
        mutable["bases"][0]["declared_reference"] = "example.invalid/base:latest"
        self._assert_rejected(mutable, input_schema)

        repository = "git.faruqi.dev/mimranfaruqi/github-actions-runner-buildah"
        validate_json_schema(repository, repository_schema)
        validate_json_schema(repository + ":1.2.3", tag_schema)
        validate_json_schema(repository + ":sha-" + SHA, tag_schema)
        for value in (
            "ghcr.io/streamscapetv/flux-runner-buildah",
            "git.faruqi.dev/mimranfaruqi/unknown",
            "git.faruqi.dev/other/iptv-backend",
            "Git.faruqi.dev/mimranfaruqi/iptv-backend",
            "git.faruqi.dev/mimranfaruqi/iptv-backend:latest",
        ):
            with self.subTest(repository=value):
                self._assert_rejected(value, repository_schema)
        for value in (
            "ghcr.io/streamscapetv/flux-runner-buildah:1.2.3",
            "git.faruqi.dev/mimranfaruqi/unknown:1.2.3",
            "git.faruqi.dev/other/iptv-backend:1.2.3",
            "git.faruqi.dev/mimranfaruqi/iptv-backend:latest",
            repository + ":01.2.3",
        ):
            with self.subTest(reference=value):
                self._assert_rejected(value, tag_schema)

    def test_release_tag_schema_accepts_128_characters_and_rejects_129(self) -> None:
        version_schema = self._definition_schema("stableSemver")
        reference_schema = self._definition_schema("immutableTagReference")
        repository = "git.faruqi.dev/mimranfaruqi/iptv-backend"
        self.assertEqual(len(MAX_OCI_TAG_VERSION), 128)
        self.assertEqual(len(OVERSIZED_OCI_TAG_VERSION), 129)
        validate_json_schema(MAX_OCI_TAG_VERSION, version_schema)
        validate_json_schema(
            repository + ":" + MAX_OCI_TAG_VERSION,
            reference_schema,
        )
        validate_json_schema(repository + ":sha-" + SHA, reference_schema)
        self._assert_rejected(OVERSIZED_OCI_TAG_VERSION, version_schema)
        self._assert_rejected(
            repository + ":" + OVERSIZED_OCI_TAG_VERSION,
            reference_schema,
        )

    def test_scratch_only_empty_input_evidence_remains_valid(self) -> None:
        target = SimpleNamespace(
            platforms=("linux/amd64",),
            input_policy_id="scratch-only-v1",
        )
        self.assertEqual(
            publication_runtime._validate_resolved_input_evidence(  # noqa: SLF001
                OciBuildInputEvidence.empty().to_dict(),
                target,
                "build_evidence_mismatch",
            ),
            OciBuildInputEvidence.empty().to_dict(),
        )

    def test_result_maps_require_product_id_property_names(self) -> None:
        result = self.schema["$defs"]["result"]
        properties = result["properties"]
        target_map = properties["immutable_references"]["properties"]["targets"]
        maps = (
            (properties["manifest_digests"], DIGEST),
            (
                properties["platform_digests"],
                {
                    "linux/amd64": {
                        "manifest_digest": DIGEST,
                        "config_digest": "sha256:" + "2" * 64,
                        "layer_digests": ["sha256:" + "3" * 64],
                        "labels": _metadata_labels(),
                    }
                },
            ),
            (target_map, _target_reference()),
        )

        for raw_schema, value in maps:
            with self.subTest(schema=raw_schema):
                self.assertEqual(
                    raw_schema["propertyNames"], {"$ref": "#/$defs/productId"}
                )
                schema = self._object_schema(raw_schema)
                validate_json_schema({"runner-buildah": value}, schema)
                self._assert_rejected({"runner//buildah": value}, schema)

    def test_platform_evidence_has_closed_cardinality_and_value_bounds(self) -> None:
        schema = self._definition_schema("platformEvidence")
        row = {
            "manifest_digest": DIGEST,
            "config_digest": "sha256:" + "2" * 64,
            "layer_digests": ["sha256:" + "3" * 64] * 256,
            "labels": _metadata_labels(),
        }
        validate_json_schema({"linux/amd64": row}, schema)

        too_many_layers = {
            **row,
            "layer_digests": ["sha256:" + "3" * 64] * 257,
        }
        self._assert_rejected({"linux/amd64": too_many_layers}, schema)

        extra_label = {
            **row,
            "labels": {**_metadata_labels(), "attacker.example": "unbounded"},
        }
        self._assert_rejected({"linux/amd64": extra_label}, schema)

        oversized_label = _metadata_labels()
        oversized_label["org.opencontainers.image.description"] = "x" * 513
        self._assert_rejected(
            {"linux/amd64": {**row, "labels": oversized_label}},
            schema,
        )

        platform_schema = self.schema["$defs"]["platformEvidence"]
        labels_schema = next(
            iter(platform_schema["patternProperties"].values())
        )["properties"]["labels"]
        self.assertEqual(platform_schema["maxProperties"], 2)
        self.assertEqual(labels_schema["maxProperties"], 8)
        self.assertFalse(labels_schema["additionalProperties"])

    def test_result_target_maps_are_bounded_to_supported_product_shape(self) -> None:
        properties = self.schema["$defs"]["result"]["properties"]
        self.assertEqual(properties["manifest_digests"]["maxProperties"], 2)
        self.assertEqual(properties["platform_digests"]["maxProperties"], 2)
        self.assertEqual(
            properties["immutable_references"]["properties"]["targets"][
                "maxProperties"
            ],
            2,
        )


class PlatformConfirmationTests(unittest.TestCase):
    def _root(self) -> tempfile.TemporaryDirectory[str]:
        temp = tempfile.TemporaryDirectory()
        contract_dir = Path(temp.name) / "contracts"
        contract_dir.mkdir()
        shutil.copyfile(FIXTURE, contract_dir / "oci-products.json")
        return temp

    def test_platform_set_confirms_checked_in_matrix(self) -> None:
        with self._root() as temp:
            plan = resolve_plan(
                Path(temp),
                PublishRequest(
                    "StreamScapeTV/backend",
                    SHA,
                    SHA,
                    "backend-image",
                    "1.2.3",
                    "trusted-exact",
                    "linux-multi-arch",
                ),
            )
        self.assertEqual(plan.targets[0].platforms, ("linux/amd64", "linux/arm64/v8"))

    def test_platform_set_cannot_narrow_checked_in_matrix(self) -> None:
        with self._root() as temp:
            with self.assertRaisesRegex(OciPublishError, "platform_override_forbidden"):
                resolve_plan(
                    Path(temp),
                    PublishRequest(
                        "StreamScapeTV/backend",
                        SHA,
                        SHA,
                        "backend-image",
                        "1.2.3",
                        "trusted-exact",
                        "linux-amd64",
                    ),
                )


class PublicProjectionTests(unittest.TestCase):
    def test_real_enabled_plan_projects_to_registered_schema_and_flux_handoff(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            contract_path = root / "contracts/oci-products.json"
            contract_path.parent.mkdir()
            product_contract = json.loads(
                (ROOT / "contracts/oci-products.json").read_text(encoding="utf-8")
            )
            product_contract["products"]["flux-runner-images"][
                "adoption_ready"
            ] = True
            policy_id = product_contract["products"]["flux-runner-images"][
                "registry_write_policy_id"
            ]
            product_contract["registry_write_policies"][policy_id] = {
                key: value
                for key, value in _registry_write_policy().items()
                if key != "policy_id"
            }
            contract_path.write_text(json.dumps(product_contract), encoding="utf-8")
            plan = resolve_plan(
                root,
                PublishRequest(
                    "StreamScapeTV/flux",
                    SHA,
                    SHA,
                    "flux-runner-images",
                    "1.2.3",
                    "trusted-exact",
                    "linux-amd64",
                ),
            )

        manifest = "sha256:" + "1" * 64
        config = "sha256:" + "2" * 64
        layer = "sha256:" + "3" * 64
        targets = {target.target_id: target for target in plan.targets}
        assertion_evidence = {
            target_id: _assertion_evidence(target.platforms)
            for target_id, target in targets.items()
        }
        platform_digests = {
            target_id: {
                platform: {
                    "manifest_digest": manifest,
                    "config_digest": config,
                    "layer_digests": [layer],
                    "labels": _metadata_labels(target),
                }
                for platform in target.platforms
            }
            for target_id, target in targets.items()
        }
        detailed = {
            "result": "success",
            "repositories_json": json.dumps(
                {
                    target_id: target.registry_repository
                    for target_id, target in targets.items()
                },
                sort_keys=True,
                separators=(",", ":"),
            ),
            "version_references_json": json.dumps(
                {
                    target_id: target.version_reference
                    for target_id, target in targets.items()
                },
                sort_keys=True,
                separators=(",", ":"),
            ),
            "source_references_json": json.dumps(
                {
                    target_id: target.source_reference
                    for target_id, target in targets.items()
                },
                sort_keys=True,
                separators=(",", ":"),
            ),
            "manifest_digests_json": json.dumps(
                {target_id: manifest for target_id in targets},
                sort_keys=True,
                separators=(",", ":"),
            ),
            "resolved_inputs_json": json.dumps(
                {
                    target_id: _resolved_inputs(target.platforms)
                    for target_id, target in targets.items()
                },
                sort_keys=True,
                separators=(",", ":"),
            ),
            "assertion_evidence_json": json.dumps(
                assertion_evidence,
                sort_keys=True,
                separators=(",", ":"),
            ),
            "platform_digests_json": json.dumps(
                platform_digests,
                sort_keys=True,
                separators=(",", ":"),
            ),
        }
        with patch(
            "ci_workflows.oci_publish_contract._runtime.verify",
            return_value=detailed,
        ):
            outputs = verify(plan, {})
        self.assertEqual(
            outputs["manifest_digests_json"],
            detailed["manifest_digests_json"],
        )
        self.assertNotIn("image_digest", outputs)
        self.assertIn(
            '"canary_id":"flux-runner-images-canary"',
            outputs["immutable_references_json"],
        )
        self.assertIn(
            '"rollback_id":"flux-runner-images-rollback"',
            outputs["immutable_references_json"],
        )
        immutable_references = json.loads(outputs["immutable_references_json"])
        target_reference = immutable_references["targets"]["runner-buildah"]
        self.assertEqual(
            target_reference["source_reference"],
            "git.faruqi.dev/mimranfaruqi/github-actions-runner-buildah:sha-"
            + SHA,
        )
        self.assertNotIn("source_sha", target_reference)
        self.assertEqual(immutable_references["release"]["source_sha"], SHA)
        self.assertEqual(
            immutable_references["registry_write_policy"],
            _registry_write_policy(),
        )
        self.assertEqual(target_reference["manifest_digest"], manifest)
        self.assertEqual(
            target_reference["resolved_inputs"],
            _resolved_inputs(("linux/amd64",)),
        )
        self.assertEqual(
            target_reference["assertions"],
            assertion_evidence["runner-buildah"],
        )

        types = json.loads(
            (ROOT / "contracts/public-workflow-types.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(
            types["output_catalog"]["manifest_digests_json"]["type"],
            "json-object",
        )
        schema = json.loads(
            (ROOT / "contracts/oci-publication.schema.json").read_text(
                encoding="utf-8"
            )
        )
        validate_json_schema(
            {
                "result": outputs["result"],
                "manifest_digests": json.loads(
                    outputs["manifest_digests_json"]
                ),
                "platform_digests": json.loads(
                    outputs["platform_digests_json"]
                ),
                "immutable_references": immutable_references,
            },
            schema,
        )


if __name__ == "__main__":
    unittest.main()
