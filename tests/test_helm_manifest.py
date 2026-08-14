from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ci_workflows.helm_manifest import remote_chart_manifest_digest
from ci_workflows.helm_types import HelmValidationError


class HelmManifestReadBackTests(unittest.TestCase):
    def setup_state(self, root: Path) -> tuple[Path, Path]:
        source = root / "source"
        source.mkdir()
        state = root / "state"
        state.mkdir()
        auth = state / "helm-validation/config/registry/config.json"
        auth.parent.mkdir(parents=True)
        auth.write_text('{"auths":{}}', encoding="utf-8")
        return source, state

    @staticmethod
    def manifest(package_sha: str) -> str:
        return json.dumps(
            {
                "schemaVersion": 2,
                "config": {
                    "mediaType": "application/vnd.cncf.helm.config.v1+json",
                    "digest": "sha256:" + "f" * 64,
                    "size": 100,
                },
                "layers": [
                    {
                        "mediaType": "application/vnd.cncf.helm.chart.content.v1.tar+gzip",
                        "digest": "sha256:" + package_sha,
                        "size": 200,
                    }
                ],
            },
            separators=(",", ":"),
        )

    def test_exact_raw_manifest_bytes_define_chart_digest(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, state = self.setup_state(root)
            package_sha = "e" * 64
            manifest = self.manifest(package_sha)
            digest = "sha256:" + hashlib.sha256(manifest.encode("utf-8")).hexdigest()
            calls: list[list[str]] = []

            def fake_run(argv, **kwargs):
                command = list(argv)
                calls.append(command)
                return subprocess.CompletedProcess(command, 0, manifest, "")

            with patch("ci_workflows.helm_manifest._run", side_effect=fake_run):
                actual = remote_chart_manifest_digest(
                    source,
                    state,
                    "oci://git.faruqi.dev/mimranfaruqi/helm-charts/iptv-backend",
                    "1.2.3",
                    package_sha,
                    {"PATH": "/usr/bin", "HOME": str(root)},
                )
            self.assertEqual(actual, digest)
            self.assertEqual(len(calls), 1)
            self.assertIn("--raw", calls[0])
            self.assertNotIn("--format", calls[0])

    def test_helm_layer_shape_and_package_digest_are_strict(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, state = self.setup_state(root)
            package_sha = "e" * 64

            bad_manifest = self.manifest("0" * 64)
            with patch(
                "ci_workflows.helm_manifest._run",
                return_value=subprocess.CompletedProcess([], 0, bad_manifest, ""),
            ):
                with self.assertRaisesRegex(
                    HelmValidationError,
                    "remote_manifest_invalid",
                ):
                    remote_chart_manifest_digest(
                        source,
                        state,
                        "oci://git.faruqi.dev/mimranfaruqi/helm-charts/iptv-backend",
                        "1.2.3",
                        package_sha,
                        {"PATH": "/usr/bin", "HOME": str(root)},
                    )

            invalid_media = json.loads(self.manifest(package_sha))
            invalid_media["config"]["mediaType"] = "application/octet-stream"
            invalid_raw = json.dumps(invalid_media, separators=(",", ":"))
            with patch(
                "ci_workflows.helm_manifest._run",
                return_value=subprocess.CompletedProcess([], 0, invalid_raw, ""),
            ):
                with self.assertRaisesRegex(
                    HelmValidationError,
                    "remote_manifest_invalid",
                ):
                    remote_chart_manifest_digest(
                        source,
                        state,
                        "oci://git.faruqi.dev/mimranfaruqi/helm-charts/iptv-backend",
                        "1.2.3",
                        package_sha,
                        {"PATH": "/usr/bin", "HOME": str(root)},
                    )

    def test_malformed_or_oversized_raw_manifest_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, state = self.setup_state(root)
            with patch(
                "ci_workflows.helm_manifest._run",
                return_value=subprocess.CompletedProcess([], 0, "not-json", ""),
            ):
                with self.assertRaisesRegex(
                    HelmValidationError,
                    "remote_manifest_invalid",
                ):
                    remote_chart_manifest_digest(
                        source,
                        state,
                        "oci://git.faruqi.dev/mimranfaruqi/helm-charts/iptv-backend",
                        "1.2.3",
                        "e" * 64,
                        {"PATH": "/usr/bin", "HOME": str(root)},
                    )

            oversized = "{" + (" " * 2_000_001) + "}"
            with patch(
                "ci_workflows.helm_manifest._run",
                return_value=subprocess.CompletedProcess([], 0, oversized, ""),
            ):
                with self.assertRaisesRegex(
                    HelmValidationError,
                    "remote_manifest_read_back_failed",
                ):
                    remote_chart_manifest_digest(
                        source,
                        state,
                        "oci://git.faruqi.dev/mimranfaruqi/helm-charts/iptv-backend",
                        "1.2.3",
                        "e" * 64,
                        {"PATH": "/usr/bin", "HOME": str(root)},
                    )

    def test_release_adapter_routes_manifest_proof_through_raw_module(self) -> None:
        script = (
            Path(__file__).resolve().parents[1] / "scripts/ci/helm_release.py"
        ).read_text(encoding="utf-8")
        self.assertIn(
            "from ci_workflows.helm_manifest import remote_chart_manifest_digest",
            script,
        )


if __name__ == "__main__":
    unittest.main()
