from __future__ import annotations

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
from ci_workflows.issue_dependency_manifest import (
    ManifestValidationError,
    validate_json_schema,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/oci-publish/oci-products.json"
SHA = "a" * 40
DIGEST = "sha256:" + "1" * 64


def _assertion_evidence() -> dict[str, object]:
    return {
        "result": "passed",
        "verified_platforms": ["linux/amd64"],
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


def _target_reference() -> dict[str, object]:
    repository = "ghcr.io/streamscapetv/flux-runner-buildah"
    return {
        "repository": repository,
        "version": repository + ":1.2.3",
        "source_reference": repository + ":sha-" + SHA,
        "manifest_digest": DIGEST,
        "base_references": ["example.invalid/base@sha256:" + "4" * 64],
        "assertions": _assertion_evidence(),
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

    def test_base_and_registry_references_reject_noncanonical_segments(self) -> None:
        base_schema = self._definition_schema("baseReference")
        repository_schema = self._definition_schema("repository")
        tag_schema = self._definition_schema("immutableTagReference")
        digest = "sha256:" + "9" * 64

        for value in ("scratch", f"registry.example/team/image@{digest}"):
            validate_json_schema(value, base_schema)
        for value in (
            f"registry.example//image@{digest}",
            f"registry.example/./image@{digest}",
            f"registry.example/../image@{digest}",
        ):
            with self.subTest(base=value):
                self._assert_rejected(value, base_schema)

        repository = "ghcr.io/streamscapetv/flux-runner-buildah"
        validate_json_schema(repository, repository_schema)
        validate_json_schema(repository + ":1.2.3", tag_schema)
        validate_json_schema(repository + ":sha-" + SHA, tag_schema)
        for value in (
            "ghcr.io/streamscapetv//runner",
            "ghcr.io/streamscapetv/./runner",
            "ghcr.io/streamscapetv/../runner",
            "ghcr.io/streamscapetv/team/runner",
        ):
            with self.subTest(repository=value):
                self._assert_rejected(value, repository_schema)
        for value in (
            "ghcr.io/streamscapetv//runner:1.2.3",
            "ghcr.io/streamscapetv/./runner:1.2.3",
            "ghcr.io/streamscapetv/team/runner:1.2.3",
            repository + ":01.2.3",
        ):
            with self.subTest(reference=value):
                self._assert_rejected(value, tag_schema)

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
                        "labels": {"org.opencontainers.image.revision": SHA},
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
    def test_verify_projects_registered_outputs_and_flux_handoff(self) -> None:
        target = SimpleNamespace(target_id="runner-buildah")
        plan = SimpleNamespace(
            targets=(target,),
            admitted_sha=SHA,
            release_version="1.2.3",
            flux_asset=True,
            canary_id="runner-images-canary",
            previous_known_good="flux-policy:runner-images/current-known-good",
            rollback_id="runner-images-rollback",
        )
        manifest = "sha256:" + "1" * 64
        config = "sha256:" + "2" * 64
        layer = "sha256:" + "3" * 64
        assertion_evidence = {
            "result": "passed",
            "verified_platforms": ["linux/amd64"],
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
        detailed = {
            "result": "success",
            "repositories_json": '{"runner-buildah":"ghcr.io/streamscapetv/flux-runner-buildah"}',
            "version_references_json": '{"runner-buildah":"ghcr.io/streamscapetv/flux-runner-buildah:1.2.3"}',
            "source_references_json": '{"runner-buildah":"ghcr.io/streamscapetv/flux-runner-buildah:sha-' + SHA + '"}',
            "manifest_digests_json": '{"runner-buildah":"' + manifest + '"}',
            "resolved_base_references_json": json.dumps(
                {
                    "runner-buildah": [
                        "example.invalid/base@sha256:" + "4" * 64
                    ]
                },
                sort_keys=True,
                separators=(",", ":"),
            ),
            "assertion_evidence_json": json.dumps(
                {"runner-buildah": assertion_evidence},
                sort_keys=True,
                separators=(",", ":"),
            ),
            "platform_digests_json": json.dumps(
                {
                    "runner-buildah": {
                        "linux/amd64": {
                            "manifest_digest": manifest,
                            "config_digest": config,
                            "layer_digests": [layer],
                            "labels": {
                                "org.opencontainers.image.revision": SHA,
                            },
                        }
                    }
                },
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
            '"canary_id":"runner-images-canary"',
            outputs["immutable_references_json"],
        )
        self.assertIn(
            '"rollback_id":"runner-images-rollback"',
            outputs["immutable_references_json"],
        )
        immutable_references = json.loads(outputs["immutable_references_json"])
        target_reference = immutable_references["targets"]["runner-buildah"]
        self.assertEqual(
            target_reference["source_reference"],
            "ghcr.io/streamscapetv/flux-runner-buildah:sha-" + SHA,
        )
        self.assertNotIn("source_sha", target_reference)
        self.assertEqual(immutable_references["release"]["source_sha"], SHA)
        self.assertEqual(target_reference["manifest_digest"], manifest)
        self.assertEqual(
            target_reference["base_references"],
            ["example.invalid/base@sha256:" + "4" * 64],
        )
        self.assertEqual(target_reference["assertions"], assertion_evidence)

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
