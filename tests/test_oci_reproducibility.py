from pathlib import Path
import hashlib
import importlib.util
import json
import sys
import tempfile
import unittest
import yaml

ROOT = Path(__file__).resolve().parents[1]


def _load_oci_helper():
    path = ROOT / "scripts/ci/oci_reproducibility.py"
    spec = importlib.util.spec_from_file_location("ciw_oci_reproducibility", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("unable to load OCI reproducibility helper")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


OCI_HELPER = _load_oci_helper()


def _json_bytes(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _put_blob(layout: Path, data: bytes) -> tuple[str, int]:
    digest = hashlib.sha256(data).hexdigest()
    path = layout / "blobs" / "sha256" / digest
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return f"sha256:{digest}", len(data)


def _write_test_oci_layout(
    layout: Path,
    *,
    missing_arm64: bool = False,
    raw_config_variant: bool = False,
    wrong_config_architecture: bool = False,
) -> None:
    layout.mkdir(parents=True, exist_ok=True)
    (layout / "oci-layout").write_text('{"imageLayoutVersion":"1.0.0"}\n')
    manifest_descriptors = []
    platforms = [("amd64", None)]
    if not missing_arm64:
        platforms.append(("arm64", "v8"))
    for architecture, variant in platforms:
        config = {
            "architecture": (
                "arm64"
                if wrong_config_architecture and architecture == "amd64"
                else architecture
            ),
            "os": "linux",
            "rootfs": {"type": "layers", "diff_ids": [f"sha256:{'1' * 64}"]},
        }
        config_bytes = _json_bytes(config)
        if raw_config_variant and architecture == "amd64":
            config_bytes = (
                b'{"os":"linux","rootfs":{"diff_ids":["sha256:'
                + (b"1" * 64)
                + b'"],"type":"layers"},"architecture":"amd64"}'
            )
        config_digest, config_size = _put_blob(layout, config_bytes)
        manifest = {
            "schemaVersion": 2,
            "mediaType": "application/vnd.oci.image.manifest.v1+json",
            "config": {
                "mediaType": "application/vnd.oci.image.config.v1+json",
                "digest": config_digest,
                "size": config_size,
            },
            "layers": [],
        }
        manifest_bytes = _json_bytes(manifest)
        manifest_digest, manifest_size = _put_blob(layout, manifest_bytes)
        platform = {"os": "linux", "architecture": architecture}
        if variant:
            platform["variant"] = variant
        manifest_descriptors.append(
            {
                "mediaType": "application/vnd.oci.image.manifest.v1+json",
                "digest": manifest_digest,
                "size": manifest_size,
                "platform": platform,
            }
        )
    nested_index = {
        "schemaVersion": 2,
        "mediaType": "application/vnd.oci.image.index.v1+json",
        "manifests": manifest_descriptors,
    }
    nested_bytes = _json_bytes(nested_index)
    nested_digest, nested_size = _put_blob(layout, nested_bytes)
    top_index = {
        "schemaVersion": 2,
        "manifests": [
            {
                "mediaType": "application/vnd.oci.image.index.v1+json",
                "digest": nested_digest,
                "size": nested_size,
                "annotations": {"org.opencontainers.image.ref.name": "proof"},
            }
        ],
    }
    (layout / "index.json").write_bytes(_json_bytes(top_index))


class OciReproducibilityTests(unittest.TestCase):
    def test_oci_reproducibility_workflow_is_fixed_isolated_and_nonpublishing(self) -> None:
        path = ROOT / ".github/workflows/oci-reproducibility.yml"
        workflow = yaml.safe_load(path.read_text())
        text = path.read_text()
        inputs = workflow["on"]["workflow_call"]["inputs"]
        self.assertEqual(
            set(inputs),
            {"repository", "ref", "dockerfile_path", "build_context", "ci_run_id"},
        )
        for forbidden in ("command", "platform", "runner", "registry", "credential", "secret_name"):
            self.assertNotIn(forbidden, inputs)
        self.assertEqual(workflow["jobs"]["prove"]["runs-on"], "ubuntu-24.04")
        steps = workflow["jobs"]["prove"]["steps"]
        by_name = {step.get("name"): step for step in steps if step.get("name")}
        names = [step.get("name") for step in steps]
        build = by_name["Build two isolated dual-platform OCI layouts"]["run"]
        cleanup = by_name["Clean all run-owned OCI state"]["run"]
        finish = by_name["Finish Agent State run"]["with"]["status"]
        self.assertIn("for build_id in a b", build)
        self.assertIn("linux/amd64 linux/arm64/v8", build)
        self.assertIn('--root "${state_root}/graphroot"', build)
        self.assertIn('--runroot "${state_root}/runroot"', build)
        self.assertIn("--storage-driver vfs", build)
        self.assertIn('export TMPDIR="${platform_root}/tmp"', build)
        self.assertIn('export XDG_CACHE_HOME="${platform_root}/xdg-cache"', build)
        self.assertIn("--layers=false", build)
        self.assertIn("--pull=always", build)
        self.assertIn("--timestamp", build)
        self.assertIn("org.opencontainers.image.revision=${SOURCE_SHA}", build)
        self.assertIn('"oci:${layout_root}:proof"', build)
        self.assertIn("raw dual-platform OCI config identity", by_name["Compare raw dual-platform OCI config identity"]["run"])
        self.assertIn('unmount --all', cleanup)
        self.assertIn('rm -rf "${proof_root}"', cleanup)
        self.assertIn('docker image rm --force "${QEMU_IMAGE_ID}"', cleanup)
        self.assertIn('test ! -e "${proof_root}"', cleanup)
        self.assertIn("phase: observe-source", text)
        self.assertIn("job.workflow_repository", text)
        self.assertIn("job.workflow_sha", text)
        self.assertIn("docker/setup-qemu-action@v4", text)
        self.assertIn("cache-image: false", text)
        self.assertIn("actions/google-drive@main", text)
        self.assertIn("mime_type: text/plain", text)
        self.assertNotIn("upload-artifact", text)
        self.assertNotIn("FORGEJO_", text)
        self.assertNotIn("actions/private-git", text)
        self.assertNotIn("docker://", text)
        self.assertNotIn("ghcr.io", text)
        self.assertNotIn("git.faruqi.dev", text)
        self.assertLess(names.index("Compare raw dual-platform OCI config identity"), names.index("Reverify exact requested source after both builds"))
        self.assertLess(names.index("Reverify exact requested source after both builds"), names.index("Clean all run-owned OCI state"))
        self.assertLess(names.index("Clean all run-owned OCI state"), names.index("Upload private reproducibility log to Google Drive"))
        self.assertLess(names.index("Upload private reproducibility log to Google Drive"), names.index("Finish Agent State run"))
        source_reverify = by_name["Reverify exact requested source after both builds"]["run"]
        self.assertIn('test "$(git -C source rev-parse HEAD)" = "${SOURCE_SHA}"', source_reverify)
        self.assertIn('git -C source status --porcelain=v1 --untracked-files=all', source_reverify)
        for required in (
            "steps.build.outcome == 'success'",
            "steps.compare.outcome == 'success'",
            "steps.source_reverify.outcome == 'success'",
            "steps.cleanup.outcome == 'success'",
            "steps.drive.outcome == 'success'",
        ):
            self.assertIn(required, finish)

    def test_oci_reproducibility_helper_accepts_identical_dual_platform_layouts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            left = root / "left"
            right = root / "right"
            _write_test_oci_layout(left)
            _write_test_oci_layout(right)
            result = OCI_HELPER.compare_layouts(left, right)
            self.assertEqual(result["status"], "reproducible")
            self.assertEqual(result["platforms_expected"], ["linux/amd64", "linux/arm64/v8"])
            self.assertEqual(set(result["platforms"]), {"linux/amd64", "linux/arm64/v8"})
            for platform in result["platforms"].values():
                self.assertTrue(platform["config_bytes_identical"])
                self.assertTrue(platform["config_digest"].startswith("sha256:"))
                self.assertEqual(platform["config_digest"], platform["config_raw_sha256"])

    def test_oci_reproducibility_helper_rejects_raw_config_byte_drift(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            left = root / "left"
            right = root / "right"
            _write_test_oci_layout(left)
            _write_test_oci_layout(right, raw_config_variant=True)
            with self.assertRaisesRegex(OCI_HELPER.ReproducibilityError, "raw config bytes mismatch for linux/amd64"):
                OCI_HELPER.compare_layouts(left, right)

    def test_oci_reproducibility_helper_rejects_missing_platform(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            layout = Path(temporary) / "layout"
            _write_test_oci_layout(layout, missing_arm64=True)
            with self.assertRaisesRegex(OCI_HELPER.ReproducibilityError, "platform set mismatch"):
                OCI_HELPER.inspect_layout(layout)

    def test_oci_reproducibility_helper_rejects_descriptor_config_architecture_drift(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            layout = Path(temporary) / "layout"
            _write_test_oci_layout(layout, wrong_config_architecture=True)
            with self.assertRaisesRegex(
                OCI_HELPER.ReproducibilityError,
                "config linux/amd64 has wrong architecture",
            ):
                OCI_HELPER.inspect_layout(layout)


if __name__ == "__main__":
    unittest.main()
